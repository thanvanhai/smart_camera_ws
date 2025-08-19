import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge


class CameraNode(Node):
    def __init__(self):
        super().__init__('camera_node')
        self.publisher_ = self.create_publisher(Image, '/camera/frames', 10)
        self.bridge = CvBridge()

        # Lấy URL RTSP từ parameter
        self.declare_parameter('camera_url', '0')  # default = webcam
        camera_url = self.get_parameter('camera_url').get_parameter_value().string_value

        # Nếu là số thì mở webcam
        self.cap = cv2.VideoCapture(int(camera_url)) if camera_url.isdigit() else cv2.VideoCapture(camera_url)

        if not self.cap.isOpened():
            self.get_logger().error(f"❌ Cannot open camera: {camera_url}")
            return

        self.timer = self.create_timer(0.03, self.timer_callback)  # ~30 FPS

    def timer_callback(self):
        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().warn("⚠️ Failed to read frame")
            return

        msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
        self.publisher_.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
