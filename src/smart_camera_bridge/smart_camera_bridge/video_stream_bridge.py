import json
import threading
import time
import pika
import rclpy
import cv2
import base64
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from .rabbitmq_config import RabbitMQConfig

class VideoStreamBridge(Node):
    """Node stream video từ các topic ROS -> RabbitMQ queues."""
    def __init__(self):
        super().__init__('video_stream_bridge')
        self.bridge = CvBridge()
        
        # RabbitMQ setup
        self.cfg = RabbitMQConfig()
        self.connection = None
        self.channel = None
        self._connect_rabbitmq()
        
        # Quản lý các subscriber
        self.camera_subscribers = {}
        self._sub_lock = threading.Lock()
        
        # Lắng nghe sự kiện thêm/xóa camera để tự động quản lý stream
        self._stop_flag = threading.Event()
        self.consumer_thread = threading.Thread(target=self._consume_camera_events, daemon=True)
        self.consumer_thread.start()
        
        self.get_logger().info("✅ VideoStreamBridge initialized.")

    def _connect_rabbitmq(self):
        """Thiết lập kết nối RabbitMQ."""
        try:
            params = pika.URLParameters(self.cfg.url)
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()
            # Khai báo exchange để lắng nghe sự kiện
            self.channel.exchange_declare(
                exchange=self.cfg.exchange_cameras,
                exchange_type='fanout',
                durable=True
            )
            self.get_logger().info("✅ RabbitMQ connection established for video stream.")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to connect to RabbitMQ: {e}")

    def _consume_camera_events(self):
        """Lắng nghe sự kiện thêm/xóa camera để bắt đầu/dừng stream."""
        while not self._stop_flag.is_set():
            try:
                # Cần một channel riêng cho thread consumer
                params = pika.URLParameters(self.cfg.url)
                conn = pika.BlockingConnection(params)
                ch = conn.channel()
                ch.exchange_declare(exchange=self.cfg.exchange_cameras, exchange_type='fanout', durable=True)
                
                result = ch.queue_declare(queue='', exclusive=True)
                queue_name = result.method.queue
                ch.queue_bind(exchange=self.cfg.exchange_cameras, queue=queue_name)
                
                self.get_logger().info("🔗 VideoStreamer is listening for camera lifecycle events...")
                for method, _, body in ch.consume(queue_name, auto_ack=True):
                    if self._stop_flag.is_set():
                        break
                    if body:
                        self.process_camera_event(body)
            except Exception as e:
                self.get_logger().error(f"❌ VideoStreamer consumer error: {e}. Retrying in 5s...")
                time.sleep(5)

    def process_camera_event(self, body):
        """Xử lý sự kiện, bắt đầu hoặc dừng subscriber."""
        try:
            payload = json.loads(body.decode())
            action = payload.get("action")
            camera_id = str(payload.get("camera_id"))
            
            if action == "created":
                self.start_streaming(camera_id)
            elif action == "removed":
                self.stop_streaming(camera_id)
        except Exception as e:
            self.get_logger().error(f"❌ Error processing camera event for streaming: {e}")

    def start_streaming(self, camera_id: str):
        """Bắt đầu một stream mới."""
        with self._sub_lock:
            if camera_id in self.camera_subscribers:
                self.get_logger().warn(f"Stream for {camera_id} already running.")
                return

            topic_name = f"/camera/{camera_id}/frames"
            callback = lambda msg: self.frame_callback(msg, camera_id)
            
            sub = self.create_subscription(Image, topic_name, callback, 10)
            self.camera_subscribers[camera_id] = sub
            self.get_logger().info(f"🚀 Started streaming for camera {camera_id} on topic {topic_name}")

    def stop_streaming(self, camera_id: str):
        """Dừng một stream."""
        with self._sub_lock:
            if camera_id in self.camera_subscribers:
                sub = self.camera_subscribers.pop(camera_id)
                self.destroy_subscription(sub)
                self.get_logger().info(f"🛑 Stopped streaming for camera {camera_id}")

    def frame_callback(self, msg: Image, camera_id: str):
        """Callback xử lý mỗi frame ảnh."""
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            _, buffer = cv2.imencode('.jpg', cv_image)
            frame_b64 = base64.b64encode(buffer).decode('utf-8')
            
            payload = {
                "frame": frame_b64, "timestamp": time.time(),
                "height": msg.height, "width": msg.width,
                "fps": 30, "codec": "MJPEG" # Có thể lấy từ tham số
            }
            body = json.dumps(payload)
            
            # Publish lên queue stream
            stream_queue = f"{self.cfg.queue_stream_prefix}.{camera_id}"
            self.channel.basic_publish(exchange='', routing_key=stream_queue, body=body)
            
            # Publish metadata lên queue info (có thể publish 1 lần/giây để giảm tải)
            info_queue = f"{self.cfg.queue_info_prefix}.{camera_id}"
            info_payload = {k: v for k, v in payload.items() if k != 'frame'}
            self.channel.basic_publish(exchange='', routing_key=info_queue, body=json.dumps(info_payload))

        except Exception as e:
            self.get_logger().error(f"❌ Frame processing error for {camera_id}: {e}")

    def destroy_node(self):
        self.get_logger().info("🛑 Shutting down VideoStreamBridge...")
        self._stop_flag.set()
        if self.connection and self.connection.is_open:
            self.connection.close()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = VideoStreamBridge()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()