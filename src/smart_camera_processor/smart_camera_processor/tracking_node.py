import rclpy
from rclpy.node import Node
from std_msgs.msg import String

class TrackingNode(Node):
    def __init__(self):
        super().__init__('tracking_node')
        self.subscription = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )
        self.get_logger().info("✅ Tracking Node initialized and subscribed to /processor/detections")

    def detections_callback(self, msg: String):
        # TODO: implement tracking algorithm (DeepSort / SORT)
        self.get_logger().info(f"Tracking detections: {msg.data}")


def main(args=None):
    rclpy.init(args=args)
    node = TrackingNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
