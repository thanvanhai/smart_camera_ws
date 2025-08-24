import json
import threading
import time
import pika
import rclpy
from rclpy.node import Node
from .rabbitmq_config import RabbitMQConfig
from smart_camera_interfaces.srv import AddCamera, RemoveCamera

class CameraLifecycleBridge(Node):
    """Node quản lý vòng đời camera: Lắng nghe RabbitMQ và gọi service ROS."""

    def __init__(self):
        super().__init__('camera_lifecycle_bridge')
        
        # ROS Service Clients
        self.cli_add = self.create_client(AddCamera, '/camera/add')
        self.cli_remove = self.create_client(RemoveCamera, '/camera/remove')
        while not self.cli_add.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn("⏳ Waiting for /camera/add service...")
        while not self.cli_remove.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn("⏳ Waiting for /camera/remove service...")

        # RabbitMQ setup
        self.cfg = RabbitMQConfig()
        self.connection = None
        self.channel = None
        self._stop_flag = threading.Event()
        
        # Start consumer thread
        self.consumer_thread = threading.Thread(
            target=self._consume_camera_events, 
            daemon=True
        )
        self.consumer_thread.start()
        self.get_logger().info("✅ CameraLifecycleBridge initialized.")

    def _connect_rabbitmq(self):
        """Thiết lập kết nối đến RabbitMQ."""
        params = pika.URLParameters(self.cfg.url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        self.channel.exchange_declare(
            exchange=self.cfg.exchange_cameras, 
            exchange_type='fanout', 
            durable=True
        )
        self.get_logger().info("✅ RabbitMQ connection established for lifecycle consumer.")

    def _consume_camera_events(self):
        """Vòng lặp tiêu thụ message thêm/xóa camera với cơ chế retry."""
        queue_name = f"camera_lifecycle.{self.get_name()}"
        while not self._stop_flag.is_set():
            try:
                self._connect_rabbitmq()
                # Khai báo queue riêng, durable để tồn tại ngay cả khi node tắt
                self.channel.queue_declare(queue=queue_name, durable=True, auto_delete=False)
                self.channel.queue_bind(exchange=self.cfg.exchange_cameras, queue=queue_name)
                
                self.get_logger().info(f"🔗 Start consuming camera lifecycle events on queue [{queue_name}]...")
                for method, _, body in self.channel.consume(queue_name, auto_ack=True):
                    if self._stop_flag.is_set():
                        break
                    if body:
                        self.process_camera_event(body)
            except Exception as e:
                self.get_logger().error(f"❌ Consumer error: {e}. Retrying in 5s...")
                time.sleep(5)

    def process_camera_event(self, body):
        """Xử lý một message sự kiện camera."""
        try:
            payload = json.loads(body.decode())
            action = payload.get("action")
            camera_id = str(payload.get("camera_id"))
            camera_url = payload.get("camera_url")

            self.get_logger().info(f"📦 Received event: {action} for camera {camera_id}")
            if action == "created":
                self.call_add_camera(camera_id, camera_url)
            elif action == "removed":
                self.call_remove_camera(camera_id)
        except Exception as e:
            self.get_logger().error(f"❌ Error processing camera event: {e}")

    def call_add_camera(self, camera_id, camera_url):
        req = AddCamera.Request()
        req.camera_id = camera_id
        req.camera_url = camera_url
        self.cli_add.call_async(req)

    def call_remove_camera(self, camera_id):
        req = RemoveCamera.Request()
        req.camera_id = camera_id
        self.cli_remove.call_async(req)

    def destroy_node(self):
        self.get_logger().info("🛑 Shutting down CameraLifecycleBridge...")
        self._stop_flag.set()
        if self.connection and self.connection.is_open:
            self.connection.close()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = CameraLifecycleBridge()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
