import rclpy
from rclpy.node import Node
from std_msgs.msg import String

class FaceRecogNode(Node):
    def __init__(self):
        super().__init__('face_recog_node')
        self.subscription = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )
        self.get_logger().info("✅ Face Recognition Node initialized and subscribed to /processor/detections")

    def detections_callback(self, msg: String):
        # TODO: implement face recognition on detected bounding boxes
        self.get_logger().info(f"Face Recognition on: {msg.data}")


def main(args=None):
    rclpy.init(args=args)
    node = FaceRecogNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
