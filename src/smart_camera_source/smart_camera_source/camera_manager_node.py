# Tên file: camera_manager_node.py

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge
from smart_camera_interfaces.srv import AddCamera, RemoveCamera, UpdateCameraURL
import threading
import time
import requests
import queue
from typing import Dict, Tuple, Optional


class CameraManagerNode(Node):
    """
    Node quản lý camera phiên bản tối ưu tích hợp:
    - Health monitoring + auto-reconnect
    - Backend sync với duplicate prevention
    - Frame buffer + frame dropping
    - Configurable FPS và max buffer
    - Cleanup resources đầy đủ
    """

    def __init__(self):
        super().__init__('camera_manager_node')

        # ROS Parameters
        self.declare_parameter('backend_api_url', 'http://localhost:8000/api/v1/cameras')
        self.declare_parameter('default_fps', 30.0)
        self.declare_parameter('max_buffer_size', 10)
        self.declare_parameter('reconnect_interval', 5.0)
        self.declare_parameter('health_check_interval', 10.0)
        self.declare_parameter('backend_sync_interval', 60)
        self.declare_parameter('watchdog_interval', 10)

        # CvBridge
        self.bridge = CvBridge()

        # Thread-safe storage
        self.cameras: Dict[str, cv2.VideoCapture] = {}
        self.camera_publishers: Dict[str, rclpy.publisher.Publisher] = {}
        self.camera_threads: Dict[str, threading.Thread] = {}
        self.camera_stop_events: Dict[str, threading.Event] = {}
        self.camera_health_status: Dict[str, bool] = {}
        self.camera_configs: Dict[str, dict] = {}  # url, fps, max_buffer_size
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()

        # ROS Services
        self.srv_add = self.create_service(AddCamera, '/camera/add', self.add_camera_callback)
        self.srv_remove = self.create_service(RemoveCamera, '/camera/remove', self.remove_camera_callback)
        self.srv_update_url = self.create_service(UpdateCameraURL, '/camera/update_url', self.update_camera_url_callback)

        self.get_logger().info("🚀Camera Manager Node initialized.")

        # Start background threads
        self._start_background_services()

    # -------------------- Background Services --------------------
    def _start_background_services(self):
        """Start health monitor, backend sync, watchdog threads"""
        threading.Thread(target=self._health_monitoring_loop, daemon=True, name="HealthMonitor").start()
        threading.Thread(target=self._backend_sync_loop, daemon=True, name="BackendSync").start()
        threading.Thread(target=self._watchdog_loop, daemon=True, name="Watchdog").start()

    # -------------------- Camera Capture Loop --------------------
    def _camera_capture_loop(self, camera_id: str, stop_event: threading.Event):
        config = self.camera_configs[camera_id]
        fps = config.get("fps", 30.0)
        max_buffer_size = config.get("max_buffer_size", 10)
        frame_interval = 1.0 / fps

        frame_buffer = queue.Queue(maxsize=max_buffer_size)
        consecutive_failures = 0
        max_failures = 10

        self.get_logger().info(f"THREAD [{camera_id}]: Capture loop started (FPS: {fps})")

        while not stop_event.is_set() and not self._shutdown_event.is_set():
            loop_start = time.time()
            with self._lock:
                cap = self.cameras.get(camera_id)
                pub = self.camera_publishers.get(camera_id)

            if not cap or not pub:
                break

            try:
                ret, frame = cap.read()
                if ret and frame is not None:
                    consecutive_failures = 0
                    with self._lock:
                        self.camera_health_status[camera_id] = True

                    # Frame dropping
                    if frame_buffer.full():
                        try:
                            frame_buffer.get_nowait()
                        except queue.Empty:
                            pass
                    try:
                        frame_buffer.put_nowait(frame)
                    except queue.Full:
                        pass

                    # Publish
                    if not frame_buffer.empty():
                        try:
                            current_frame = frame_buffer.get_nowait()
                            msg = self.bridge.cv2_to_imgmsg(current_frame, encoding="bgr8")
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
                        self.get_logger().error(f"THREAD [{camera_id}]: Too many failures, marking unhealthy")
                        break
                    time.sleep(1)

            except Exception as e:
                consecutive_failures += 1
                with self._lock:
                    self.camera_health_status[camera_id] = False
                self.get_logger().error(f"THREAD [{camera_id}]: Exception in capture loop: {e}")
                time.sleep(1)

            # Maintain FPS
            elapsed = time.time() - loop_start
            sleep_time = max(0, frame_interval - elapsed)
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

            self.camera_configs[camera_id] = {
                'url': camera_url,
                'fps': self.get_parameter('default_fps').get_parameter_value().double_value,
                'max_buffer_size': self.get_parameter('max_buffer_size').get_parameter_value().integer_value,
                'added_time': time.time()
            }

            topic_name = f'/camera/{camera_id}/frames'
            self.cameras[camera_id] = cap
            self.camera_publishers[camera_id] = self.create_publisher(Image, topic_name, 10)
            self.camera_health_status[camera_id] = True

            stop_event = threading.Event()
            thread = threading.Thread(target=self._camera_capture_loop, args=(camera_id, stop_event),
                                      daemon=True, name=f"Camera-{camera_id}")
            self.camera_stop_events[camera_id] = stop_event
            self.camera_threads[camera_id] = thread
            thread.start()

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

        return True, f"Camera {camera_id} removed successfully"

    def _open_camera_with_config(self, camera_url: str) -> Optional[cv2.VideoCapture]:
        if camera_url.isdigit():
            cap = cv2.VideoCapture(int(camera_url))
        else:
            cap = cv2.VideoCapture(camera_url)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
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
            for _ in range(interval):
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
            time.sleep(interval)

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
                    new_thread = threading.Thread(target=self._camera_capture_loop,
                                                  args=(camera_id, new_stop_event),
                                                  daemon=True,
                                                  name=f"Camera-{camera_id}-Restarted")
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
        self.get_logger().info("Cleanup completed")


def main(args=None):
    rclpy.init(args=args)
    node = CameraManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Received shutdown signal")
    finally:
        node.cleanup_all_resources()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
