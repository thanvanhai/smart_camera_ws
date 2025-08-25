import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge
from smart_camera_interfaces.srv import AddCamera, RemoveCamera, UpdateCameraURL
import threading
import time
import requests
import json
import pika  # RabbitMQ
from typing import Dict, Tuple, Optional


class CameraManagerNode(Node):
    """
    Camera Manager (low-latency version)
    - Health monitoring + auto-reconnect
    - Backend sync (auto add cameras)
    - Low-latency capture (grab/retrieve) – always publish newest frame
    - Configurable FPS, frame_width, frame_height via ROS params
    - Watchdog to restart stuck threads
    - RabbitMQ camera lifecycle events (created/removed)
    - Clean shutdown
    """

    def __init__(self):
        super().__init__('camera_manager_node')

        # -------------------- ROS Parameters --------------------
        self.declare_parameter('backend_api_url', 'http://localhost:8000/api/v1/cameras')
        self.declare_parameter('default_fps', 30.0)
        self.declare_parameter('frame_width', 640)
        self.declare_parameter('frame_height', 480)
        self.declare_parameter('reconnect_interval', 5.0)
        self.declare_parameter('health_check_interval', 10.0)
        self.declare_parameter('backend_sync_interval', 60)
        self.declare_parameter('watchdog_interval', 10)
        self.declare_parameter('rabbitmq_url', 'amqp://guest:guest@localhost:5672/')
        self.declare_parameter('rabbitmq_exchange', 'camera_events')

        # Bridge
        self.bridge = CvBridge()

        # Thread-safe storage
        self.cameras: Dict[str, cv2.VideoCapture] = {}
        self.camera_publishers: Dict[str, any] = {}
        self.camera_threads: Dict[str, threading.Thread] = {}
        self.camera_stop_events: Dict[str, threading.Event] = {}
        self.camera_health_status: Dict[str, bool] = {}
        self.camera_configs: Dict[str, dict] = {}  # url, fps, width, height
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()

        # Services
        self.srv_add = self.create_service(AddCamera, '/camera/add', self.add_camera_callback)
        self.srv_remove = self.create_service(RemoveCamera, '/camera/remove', self.remove_camera_callback)
        self.srv_update_url = self.create_service(UpdateCameraURL, '/camera/update_url', self.update_camera_url_callback)

        # RabbitMQ
        self.rabbitmq_url = self.get_parameter('rabbitmq_url').get_parameter_value().string_value
        self.rabbitmq_exchange = self.get_parameter('rabbitmq_exchange').get_parameter_value().string_value
        self._setup_rabbitmq()

        self.get_logger().info('🚀 Camera Manager Node initialized (low-latency mode).')

        # Background services
        self._start_background_services()

    # -------------------- RabbitMQ --------------------
    def _setup_rabbitmq(self):
        try:
            params = pika.URLParameters(self.rabbitmq_url)
            self.rabbit_connection = pika.BlockingConnection(params)
            self.rabbit_channel = self.rabbit_connection.channel()
            self.rabbit_channel.exchange_declare(exchange=self.rabbitmq_exchange, exchange_type='fanout', durable=True)
            self.get_logger().info(f"🐇 RabbitMQ exchange '{self.rabbitmq_exchange}' ready")
        except Exception as e:
            self.get_logger().error(f"❌ RabbitMQ setup failed: {e}")
            self.rabbit_connection = None
            self.rabbit_channel = None

    def _publish_camera_event(self, action: str, camera_id: str):
        if not self.rabbit_channel:
            return
        payload = json.dumps({"action": action, "camera_id": camera_id})
        try:
            self.rabbit_channel.basic_publish(exchange=self.rabbitmq_exchange, routing_key='', body=payload)
            self.get_logger().info(f"🐇 Published camera event: {payload}")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to publish camera event: {e}")

    # -------------------- Background Services --------------------
    def _start_background_services(self):
        threading.Thread(target=self._health_monitoring_loop, daemon=True, name='HealthMonitor').start()
        threading.Thread(target=self._backend_sync_loop, daemon=True, name='BackendSync').start()
        threading.Thread(target=self._watchdog_loop, daemon=True, name='Watchdog').start()

    # -------------------- Camera Capture Loop (LOW LATENCY) --------------------
    def _camera_capture_loop(self, camera_id: str, stop_event: threading.Event):
        config = self.camera_configs[camera_id]
        fps = float(config.get('fps', 30.0))
        frame_interval = 1.0 / fps if fps > 0 else 0.033

        consecutive_failures = 0
        max_failures = 10

        self.get_logger().info(f"THREAD [{camera_id}]: Real-time capture loop started (FPS: {fps})")

        while not stop_event.is_set() and not self._shutdown_event.is_set():
            loop_start = time.time()

            with self._lock:
                cap = self.cameras.get(camera_id)
                pub = self.camera_publishers.get(camera_id)
                desired_w = int(self.camera_configs[camera_id].get('width', 0) or 0)
                desired_h = int(self.camera_configs[camera_id].get('height', 0) or 0)

            if not cap or not pub:
                self.get_logger().warning(f"THREAD [{camera_id}]: Capture object or publisher missing; stopping.")
                break

            try:
                # Flush old frames in internal buffer; get the newest
                cap.grab()
                ret, frame = cap.retrieve()

                if ret and frame is not None:
                    consecutive_failures = 0
                    with self._lock:
                        self.camera_health_status[camera_id] = True

                    # Optional: enforce output resolution if params set and camera ignored CAP_PROP*
                    if desired_w > 0 and desired_h > 0:
                        h, w = frame.shape[:2]
                        if (w != desired_w) or (h != desired_h):
                            frame = cv2.resize(frame, (desired_w, desired_h), interpolation=cv2.INTER_AREA)

                    # Publish newest frame immediately
                    try:
                        msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
                        msg.header.frame_id = camera_id
                        msg.header.stamp = self.get_clock().now().to_msg()
                        pub.publish(msg)
                    except Exception as e:
                        self.get_logger().error(f"THREAD [{camera_id}]: Error publishing frame: {e}")
                else:
                    consecutive_failures += 1
                    with self._lock:
                        self.camera_health_status[camera_id] = False
                    if consecutive_failures >= max_failures:
                        self.get_logger().error(f"THREAD [{camera_id}]: Too many read failures; marking unhealthy and stopping.")
                        break
                    time.sleep(1)

            except Exception as e:
                consecutive_failures += 1
                with self._lock:
                    self.camera_health_status[camera_id] = False
                self.get_logger().error(f"THREAD [{camera_id}]: Exception in capture loop: {e}")
                time.sleep(1)

            # Throttle to target FPS
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.get_logger().info(f"THREAD [{camera_id}]: Capture loop stopped")

    # -------------------- Internal Camera Operations --------------------
    def _internal_add_camera(self, camera_id: str, camera_url: str) -> Tuple[bool, str]:
        with self._lock:
            if camera_id in self.cameras:
                return False, f"Camera {camera_id} already exists"

            cap = self._open_camera_with_config(camera_url)
            if not cap or not cap.isOpened():
                return False, f"Cannot open camera stream: {camera_url}"

            fps = self.get_parameter('default_fps').get_parameter_value().double_value
            width = self.get_parameter('frame_width').get_parameter_value().integer_value
            height = self.get_parameter('frame_height').get_parameter_value().integer_value

            self.camera_configs[camera_id] = {
                'url': camera_url,
                'fps': fps,
                'width': int(width),
                'height': int(height),
                'added_time': time.time(),
            }

            topic_name = f'/camera/{camera_id}/frames'
            self.cameras[camera_id] = cap
            self.camera_publishers[camera_id] = self.create_publisher(Image, topic_name, 10)
            self.camera_health_status[camera_id] = True

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._camera_capture_loop,
                args=(camera_id, stop_event),
                daemon=True,
                name=f"Camera-{camera_id}"
            )
            self.camera_stop_events[camera_id] = stop_event
            self.camera_threads[camera_id] = thread
            thread.start()

        # Notify via RabbitMQ
        self._publish_camera_event('created', camera_id)

        self.get_logger().info(f"✅ Added camera {camera_id} publishing to: {topic_name}")
        return True, f"Camera {camera_id} added successfully"

    def _internal_remove_camera(self, camera_id: str) -> Tuple[bool, str]:
        with self._lock:
            if camera_id not in self.cameras:
                return False, f"Camera {camera_id} not found"
            self.camera_stop_events[camera_id].set()

        thread = self.camera_threads.get(camera_id)
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                self.get_logger().error(f"Thread for {camera_id} did not stop in time!")

        with self._lock:
            if camera_id in self.cameras:
                self.cameras[camera_id].release()
                del self.cameras[camera_id]
            if camera_id in self.camera_publishers:
                self.destroy_publisher(self.camera_publishers[camera_id])
                del self.camera_publishers[camera_id]
            if camera_id in self.camera_threads:
                del self.camera_threads[camera_id]
            if camera_id in self.camera_stop_events:
                del self.camera_stop_events[camera_id]
            if camera_id in self.camera_health_status:
                del self.camera_health_status[camera_id]
            if camera_id in self.camera_configs:
                del self.camera_configs[camera_id]

        # Notify via RabbitMQ
        self._publish_camera_event('removed', camera_id)

        return True, f"Camera {camera_id} removed successfully"

    def _open_camera_with_config(self, camera_url: str) -> Optional[cv2.VideoCapture]:
        # Open camera/stream
        if camera_url.isdigit():
            cap = cv2.VideoCapture(int(camera_url))
        else:
            cap = cv2.VideoCapture(camera_url)

        if cap.isOpened():
            # Reduce internal buffering and try to apply desired properties
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass

            # Try set width/height/FPS (some sources may ignore)
            w = self.get_parameter('frame_width').get_parameter_value().integer_value
            h = self.get_parameter('frame_height').get_parameter_value().integer_value
            fps = self.get_parameter('default_fps').get_parameter_value().double_value
            if int(w) > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w))
            if int(h) > 0:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h))
            if float(fps) > 0:
                cap.set(cv2.CAP_PROP_FPS, float(fps))

        return cap

    # -------------------- Service Callbacks --------------------
    def add_camera_callback(self, request, response):
        success, message = self._internal_add_camera(request.camera_id, request.camera_url)
        response.success = success
        response.message = message
        return response

    def remove_camera_callback(self, request, response):
        success, message = self._internal_remove_camera(request.camera_id)
        response.success = success
        response.message = message
        return response

    def update_camera_url_callback(self, request, response):
        self._internal_remove_camera(request.camera_id)
        success, message = self._internal_add_camera(request.camera_id, request.new_url)
        response.success = success
        response.message = message
        return response

    # -------------------- Health Monitoring --------------------
    def _health_monitoring_loop(self):
        interval = self.get_parameter('health_check_interval').get_parameter_value().double_value
        reconnect_interval = self.get_parameter('reconnect_interval').get_parameter_value().double_value

        while not self._shutdown_event.is_set():
            with self._lock:
                camera_ids = list(self.cameras.keys())
            for cam_id in camera_ids:
                if self._shutdown_event.is_set():
                    break
                with self._lock:
                    healthy = self.camera_health_status.get(cam_id, False)
                if not healthy:
                    self.get_logger().warning(f"Camera {cam_id} is unhealthy, attempting reconnect...")
                    self._attempt_camera_reconnect(cam_id)
                    time.sleep(reconnect_interval)
            time.sleep(interval)

    def _attempt_camera_reconnect(self, camera_id: str):
        with self._lock:
            config = self.camera_configs.get(camera_id)
        if not config:
            return False
        self._internal_remove_camera(camera_id)
        time.sleep(self.get_parameter('reconnect_interval').get_parameter_value().double_value)
        success, _ = self._internal_add_camera(camera_id, config['url'])
        if success:
            self.get_logger().info(f"Successfully reconnected camera {camera_id}")
        else:
            self.get_logger().error(f"Failed to reconnect camera {camera_id}")
        return success

    # -------------------- Backend Sync --------------------
    def _backend_sync_loop(self):
        interval = self.get_parameter('backend_sync_interval').get_parameter_value().integer_value
        time.sleep(2)
        while not self._shutdown_event.is_set():
            try:
                self.fetch_cameras_from_backend()
            except Exception as e:
                self.get_logger().error(f"Backend sync error: {e}")
            for _ in range(int(interval)):
                if self._shutdown_event.is_set():
                    break
                time.sleep(1)

    def fetch_cameras_from_backend(self):
        url = self.get_parameter('backend_api_url').get_parameter_value().string_value
        try:
            resp = requests.get(url, timeout=10)
            if resp.ok:
                cameras = resp.json()
                with self._lock:
                    existing = set(self.cameras.keys())
                for cam in cameras:
                    cam_id = cam['camera_id']
                    if cam_id not in existing:
                        success, _ = self._internal_add_camera(cam_id, cam['stream_url'])
                        if success:
                            self.get_logger().info(f"Auto-added camera {cam_id} from backend")
            else:
                self.get_logger().warning(f"Backend returned status {resp.status_code}")
        except Exception as e:
            self.get_logger().error(f"Error connecting to backend: {e}")

    # -------------------- Watchdog --------------------
    def _watchdog_loop(self):
        interval = self.get_parameter('watchdog_interval').get_parameter_value().integer_value
        while not self._shutdown_event.is_set():
            threads_to_restart = []
            with self._lock:
                for cam_id, thread in list(self.camera_threads.items()):
                    if not thread.is_alive() or not self.camera_health_status.get(cam_id, False):
                        threads_to_restart.append(cam_id)
            for cam_id in threads_to_restart:
                self._restart_camera_thread(cam_id)
            time.sleep(int(interval))

    def _restart_camera_thread(self, camera_id: str):
        try:
            with self._lock:
                if camera_id not in self.cameras:
                    return
                old_stop_event = self.camera_stop_events.get(camera_id)
                if old_stop_event:
                    old_stop_event.set()
                old_thread = self.camera_threads.get(camera_id)
            if old_thread and old_thread.is_alive():
                old_thread.join(timeout=3.0)
            with self._lock:
                if camera_id in self.cameras:
                    new_stop_event = threading.Event()
                    new_thread = threading.Thread(
                        target=self._camera_capture_loop,
                        args=(camera_id, new_stop_event),
                        daemon=True,
                        name=f"Camera-{camera_id}-Restarted"
                    )
                    self.camera_stop_events[camera_id] = new_stop_event
                    self.camera_threads[camera_id] = new_thread
                    self.camera_health_status[camera_id] = True
                    new_thread.start()
                    self.get_logger().info(f"Watchdog: Restarted thread for {camera_id}")
        except Exception as e:
            self.get_logger().error(f"Error restarting thread for {camera_id}: {e}")

    # -------------------- Cleanup --------------------
    def cleanup_all_resources(self):
        self._shutdown_event.set()
        camera_ids = list(self.cameras.keys())
        for cam_id in camera_ids:
            self._internal_remove_camera(cam_id)
        self.get_logger().info('Cleanup completed')
        if hasattr(self, 'rabbit_connection') and self.rabbit_connection and not self.rabbit_connection.is_closed:
            self.rabbit_connection.close()


def main(args=None):
    rclpy.init(args=args)
    node = CameraManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('Received shutdown signal')
    finally:
        node.cleanup_all_resources()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
