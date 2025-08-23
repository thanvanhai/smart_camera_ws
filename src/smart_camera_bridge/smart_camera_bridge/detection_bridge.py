import json
import time
import pika
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from .rabbitmq_config import RabbitMQConfig

class DetectionBridge(Node):
    """Node chuyển tiếp dữ liệu nhận dạng từ ROS -> RabbitMQ."""
    def __init__(self):
        super().__init__('detection_bridge')
        
        # ROS Subscription
        self.create_subscription(String, '/processor/detections', self.detections_callback, 10)
        
        # RabbitMQ setup
        self.cfg = RabbitMQConfig()
        self.connection = None
        self.channel = None
        self._connect_rabbitmq()
        
        self.get_logger().info("✅ DetectionBridge initialized.")

    def _connect_rabbitmq(self):
        """Thiết lập kết nối RabbitMQ."""
        try:
            params = pika.URLParameters(self.cfg.url)
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()
            self.channel.exchange_declare(
                exchange=self.cfg.exchange_detections,
                exchange_type='fanout',
                durable=True
            )
            self.get_logger().info("✅ RabbitMQ connection established for detections.")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to connect to RabbitMQ: {e}")
            # Có thể thêm logic retry ở đây nếu cần
            
    def detections_callback(self, msg: String):
        """Callback khi nhận được dữ liệu nhận dạng, gửi lên RabbitMQ."""
        if not self.channel or not self.channel.is_open:
            self.get_logger().warn("RabbitMQ connection not available. Attempting to reconnect...")
            self._connect_rabbitmq()
            if not self.channel or not self.channel.is_open:
                self.get_logger().error("Reconnect failed. Skipping message.")
                return

        try:
            payload = {"detection": msg.data, "timestamp": time.time()}
            body = json.dumps(payload).encode('utf-8')
            
            self.channel.basic_publish(
                exchange=self.cfg.exchange_detections,
                routing_key='',
                body=body
            )
            self.get_logger().info(f"📤 Sent detection data to RabbitMQ: {msg.data[:50]}...")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to publish detection: {e}")

    def destroy_node(self):
        self.get_logger().info("🛑 Shutting down DetectionBridge...")
        if self.connection and self.connection.is_open:
            self.connection.close()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = DetectionBridge()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()