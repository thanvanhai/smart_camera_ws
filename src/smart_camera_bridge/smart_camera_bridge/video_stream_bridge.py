import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import base64
import pika
import json
import time
import threading
from threading import Lock
import requests

class VideoStreamBridge(Node):
    """
    Optimal VideoStreamBridge (full-quality frames, robust):
    1. Sync existing cameras from backend on startup.
    2. Subscribe/unsubscribe dynamically via RabbitMQ events.
    3. High-availability per-camera publisher.
    4. Frames published in original quality (no JPEG compression).
    """

    # ------------------ Khởi Tạo (Initialization) ------------------
    # Nơi thiết lập các tham số, thuộc tính và khởi chạy các luồng nền.
    def __init__(self):
        super().__init__('video_stream_bridge')
        self.declare_parameter('rabbitmq_host', 'localhost')
        self.declare_parameter('backend_api_url', 'http://localhost:8000/api/v1/cameras')

        self.bridge = CvBridge()
        self._sub_lock = Lock()
        self._pub_lock = Lock()
        self.camera_subscribers = {}
        self.camera_channels = {}  # Key: camera_id, Value: (channel, connection)
        self._stop_flag = threading.Event()
        self._sync_completed = threading.Event()

        self.rabbitmq_host = self.get_parameter('rabbitmq_host').get_parameter_value().string_value
        self.backend_api_url = self.get_parameter('backend_api_url').get_parameter_value().string_value

        self.consumer_thread = threading.Thread(target=self._consume_camera_events, daemon=True, name="CameraEventConsumer")
        self.consumer_thread.start()

        self.initial_sync_thread = threading.Thread(target=self._sync_existing_cameras, daemon=True, name="InitialCameraSync")
        self.initial_sync_thread.start()

        self.get_logger().info("✅ VideoStreamBridge initialized.")
        self.get_logger().info(f"🔄 Synchronizing with backend at {self.backend_api_url}")

    # ------------------ Đồng Bộ Hóa Ban Đầu (Initial Sync) ------------------
    # Luồng này chạy một lần khi khởi động để lấy danh sách camera hiện có từ backend,
    # giải quyết vấn đề bỏ lỡ sự kiện nếu node này khởi động sau CameraManager.
    def _sync_existing_cameras(self):
        try:
            time.sleep(2)
            self.get_logger().info("🚀 Starting initial camera sync from backend...")

            response = requests.get(self.backend_api_url, timeout=10)
            response.raise_for_status()

            cameras = response.json()
            if not cameras:
                self.get_logger().info("📭 No existing cameras found on backend.")
            else:
                self.get_logger().info(f"📡 Found {len(cameras)} cameras. Starting streams...")
                for cam in cameras:
                    if self._stop_flag.is_set():
                        break
                    camera_id = cam.get('camera_id')
                    if camera_id:
                        self.start_streaming(str(camera_id))
                        time.sleep(0.1)
        except requests.exceptions.RequestException as e:
            self.get_logger().error(f"❌ Could not sync cameras: {e}")
        except Exception as e:
            self.get_logger().error(f"❌ Error during initial sync: {e}")
        finally:
            self._sync_completed.set()
            self.get_logger().info("✅ Initial camera sync completed.")

    # ------------------ Lắng Nghe Sự Kiện Camera (Camera Events) ------------------
    # Luồng này chạy nền liên tục để lắng nghe các sự kiện "created" và "removed"
    # từ RabbitMQ, giúp hệ thống có thể thêm/xóa camera một cách linh động.
    def _consume_camera_events(self):
        self.get_logger().info("⏳ Waiting for initial sync to complete...")
        self._sync_completed.wait()

        connection = None
        channel = None

        while not self._stop_flag.is_set():
            try:
                if connection is None or connection.is_closed:
                    connection = pika.BlockingConnection(
                        pika.ConnectionParameters(self.rabbitmq_host)
                    )
                    channel = connection.channel()
                    channel.exchange_declare(exchange='camera_events', exchange_type='fanout', durable=True)
                    result = channel.queue_declare(queue='', exclusive=True)
                    queue_name = result.method.queue
                    channel.queue_bind(exchange='camera_events', queue=queue_name)
                    self.get_logger().info("🔗 Real-time event consumer listening...")

                for method, _, body in channel.consume(queue_name, auto_ack=True, inactivity_timeout=2.0):
                    if self._stop_flag.is_set():
                        break
                    if body:
                        try:
                            payload = json.loads(body.decode())
                            self.process_camera_event(payload)
                        except Exception as e:
                            self.get_logger().error(f"Error processing camera event: {e}")
                    if method is None:
                        continue
            except pika.exceptions.AMQPConnectionError as e:
                if not self._stop_flag.is_set():
                    self.get_logger().warn(f"Consumer connection lost: {e}. Retrying in 5s...")
                    connection = None
                    time.sleep(5)
            except Exception as e:
                if not self._stop_flag.is_set():
                    self.get_logger().error(f"Consumer error: {e}. Retrying in 5s...")
                    connection = None
                    time.sleep(5)
            finally:
                if self._stop_flag.is_set() and connection and connection.is_open:
                    try: connection.close()
                    except: pass

        self.get_logger().info("🛑 Consumer thread stopped.")

    def process_camera_event(self, payload: dict):
        try:
            action = payload.get("action")
            camera_id_raw = payload.get("camera_id")
            if not camera_id_raw:
                self.get_logger().warn("Received camera event without camera_id")
                return
            camera_id = str(camera_id_raw)

            if action == "created":
                self.get_logger().info(f"📡 'created' event for camera {camera_id}")
                self.start_streaming(camera_id)
            elif action == "removed":
                self.get_logger().info(f"📡 'removed' event for camera {camera_id}")
                self.stop_streaming(camera_id)
            else:
                self.get_logger().warn(f"Unknown camera event action: {action}")
        except Exception as e:
            self.get_logger().error(f"Error in process_camera_event: {e}")

    # ------------------ Quản Lý Luồng (Start / Stop Stream) ------------------
    # Các hàm để bắt đầu và dừng việc xử lý một luồng camera. Bao gồm việc tạo/hủy
    # subscriber ROS và quản lý các tài nguyên liên quan.
    def start_streaming(self, camera_id: str):
        with self._sub_lock:
            if camera_id in self.camera_subscribers:
                existing_sub = self.camera_subscribers[camera_id]
                if existing_sub is not None:
                    return
                else:
                    return
            self.get_logger().info(f"🚀 Starting stream for {camera_id}")
            self.camera_subscribers[camera_id] = None

        try:
            topic_name = f"/camera/{camera_id}/frames"

            with self._pub_lock:
                channel = self._get_or_create_publisher_channel(camera_id)
                if not channel:
                    raise Exception(f"Failed to create publisher channel for {camera_id}")

            sub = self.create_subscription(
                Image, topic_name,
                lambda msg, cid=camera_id: self.frame_callback(msg, cid),
                10
            )

            with self._sub_lock:
                self.camera_subscribers[camera_id] = sub

            self.get_logger().info(f"✅ Stream started for {camera_id} -> {topic_name}")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to start streaming {camera_id}: {e}")
            with self._sub_lock:
                self.camera_subscribers.pop(camera_id, None)
            with self._pub_lock:
                self.camera_channels.pop(camera_id, None)

    def stop_streaming(self, camera_id: str):
        self.get_logger().info(f"🛑 Stopping stream for {camera_id}")
        with self._sub_lock:
            sub = self.camera_subscribers.pop(camera_id, None)
            if sub:
                try: self.destroy_subscription(sub)
                except Exception as e: self.get_logger().error(f"Error destroying subscription for {camera_id}: {e}")

        with self._pub_lock:
            ch_conn = self.camera_channels.pop(camera_id, None)
            if ch_conn:
                ch, conn = ch_conn
                try:
                    if conn and conn.is_open: conn.close()
                except Exception as e:
                    self.get_logger().error(f"Error closing publisher for {camera_id}: {e}")

    # ------------------ Kênh Gửi Dữ Liệu (Publisher Channel) ------------------
    # Quản lý việc tạo, kết nối lại và tái sử dụng các kênh (channel) riêng biệt
    # cho mỗi camera để gửi dữ liệu tới RabbitMQ một cách tin cậy.
    def _get_or_create_publisher_channel(self, camera_id: str):
        try:
            if camera_id in self.camera_channels:
                channel, connection = self.camera_channels[camera_id]
                if connection.is_open and channel.is_open:
                    return channel

            if camera_id in self.camera_channels:
                _, old_conn = self.camera_channels.pop(camera_id)
                try: 
                    if old_conn and old_conn.is_open: old_conn.close()
                except: pass

            connection = pika.BlockingConnection(pika.ConnectionParameters(self.rabbitmq_host))
            channel = connection.channel()

            # !!! CHÚ THÍCH QUAN TRỌNG VỀ CẤU HÌNH HÀNG ĐỢI !!!
            # Các tham số (durable, auto_delete, arguments) khi khai báo hàng đợi (queue)
            # ở đây PHẢI KHỚP 100% với cấu hình hàng đợi ở phía consumer (backend).
            # Nếu không khớp, RabbitMQ sẽ báo lỗi và kết nối sẽ bị đóng.
            channel.queue_declare(
                queue=f"camera.stream.{camera_id}",
                durable=False, auto_delete=True,
                arguments={'x-max-length': 5, 'x-overflow': 'drop-head', 'x-message-ttl': 2000}
            )
            channel.queue_declare(queue=f"camera.info.{camera_id}", durable=False, auto_delete=True)

            self.camera_channels[camera_id] = (channel, connection)
            return channel
        except Exception as e:
            self.get_logger().error(f"❌ Failed to create publisher channel for {camera_id}: {e}")
            return None

    # ------------------ Xử Lý Frame Ảnh (Frame Callback) ------------------
    # Đây là hàm được gọi cho MỖI frame ảnh nhận được từ ROS. Nó chịu trách nhiệm
    # chuyển đổi, mã hóa và gửi dữ liệu qua RabbitMQ.
    def frame_callback(self, msg: Image, camera_id: str):
        """Xử lý frame với robust error handling và log đầy đủ."""
        try:
            with self._pub_lock:
                channel = self._get_or_create_publisher_channel(camera_id)
                if not channel:
                    self.get_logger().error(f"❌ No publisher channel available for {camera_id}")
                    return

                cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
                _, buffer = cv2.imencode('.jpg', cv_image, [cv2.IMWRITE_JPEG_QUALITY, 100])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')

                payload = {
                    "frame": frame_b64,
                    "timestamp": time.time(),
                    "height": msg.height,
                    "width": msg.width,
                    "fps": 30,
                    "codec": "MJPEG"
                }

                stream_queue = f"camera.stream.{camera_id}"
                channel.basic_publish(
                    exchange='',
                    routing_key=stream_queue,
                    body=json.dumps(payload)
                )
                self.get_logger().info(f"📤 Published frame to {stream_queue}")

                # --- TẠM THỜI VÔ HIỆU HÓA VIỆC GỬI INFO ---
                # info_payload = {k: v for k, v in payload.items() if k != 'frame'}
                # info_queue = f"camera.info.{camera_id}"
                # channel.basic_publish(exchange='', routing_key=info_queue, body=json.dumps(info_payload))
                # self.get_logger().info(f"📤 Published info to {info_queue}")
        except pika.exceptions.AMQPError as e:
            self.get_logger().error(f"🐰 RabbitMQ publish error for {camera_id}: {e}")
            with self._pub_lock:
                if camera_id in self.camera_channels:
                    del self.camera_channels[camera_id]
        except Exception as e:
            self.get_logger().error(f"❌ Frame processing error for {camera_id}: {e}")

    # ------------------ Dọn Dẹp Khi Tắt (Shutdown) ------------------
    # Hàm này được gọi khi node tắt (Ctrl+C) để đảm bảo tất cả các luồng
    # và kết nối được đóng lại một cách an toàn.
    def destroy_node(self):
        self.get_logger().info("🛑 Shutting down VideoStreamBridge...")
        self._stop_flag.set()
        if self.consumer_thread.is_alive(): self.consumer_thread.join(timeout=5)
        if self.initial_sync_thread.is_alive(): self.initial_sync_thread.join(timeout=3)
        for cam_id in list(self.camera_subscribers.keys()):
            self.stop_streaming(cam_id)
        self.get_logger().info("✅ VideoStreamBridge shutdown complete.")
        super().destroy_node()

# ------------------ Điểm Bắt Đầu Thực Thi (Main Execution) ------------------
# Hàm main tiêu chuẩn để khởi tạo và chạy một node ROS2.
def main(args=None):
    rclpy.init(args=args)
    node = VideoStreamBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Received shutdown signal")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()