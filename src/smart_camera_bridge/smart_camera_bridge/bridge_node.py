import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import pika
import json
import time

class BridgeNode(Node):
    def __init__(self):
        super().__init__('bridge_node')

        # ROS2 Subscription
        self.subscription = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )

        # RabbitMQ connection
        try:
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(host='localhost')
            )
            self.channel = self.connection.channel()
            self.channel.queue_declare(queue='smart_camera_events')
            self.get_logger().info("✅ RabbitMQ initialized")
        except Exception as e:
            self.get_logger().error(f"❌ RabbitMQ init error: {e}")
            raise

    def detections_callback(self, msg: String):
        payload = {
            "timestamp": time.time(),
            "detections": msg.data
        }
        self.channel.basic_publish(
            exchange='',
            routing_key='smart_camera_events',
            body=json.dumps(payload)
        )
        self.get_logger().info(f"📡 Published to RabbitMQ: {payload}")


def main(args=None):
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
