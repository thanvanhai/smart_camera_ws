import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge
import cv2
from ultralytics import YOLO

class YOLONode(Node):
    """
    ROS2 Node: YOLOv8 Object Detection
    Nhận hình ảnh từ /camera/frames và publish detections sang /processor/detections
    """
    def __init__(self):
        super().__init__('yolo_node')

        # Subscription: nhận ảnh từ camera_node
        self.subscription = self.create_subscription(
            Image,
            '/camera/frames',
            self.image_callback,
            10
        )

        # Publisher: gửi kết quả detection
        self.publisher_ = self.create_publisher(String, '/processor/detections', 10)

        # CV Bridge
        self.bridge = CvBridge()

        # Load YOLOv8 model (tải local yolov8n.pt, nếu chưa có sẽ tự download)
        try:
            self.model = YOLO("yolov8n.pt")
            self.get_logger().info("✅ YOLOv8 model loaded successfully")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to load YOLO model: {e}")
            raise

        self.get_logger().info("✅ YOLO Node initialized and subscribed to /camera/frames")

    def image_callback(self, msg: Image):
        """
        Callback ROS2: xử lý ảnh nhận được
        """
        try:
            # Convert ROS Image -> OpenCV image
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')

            # YOLO inference
            results = self.model(cv_image)

            detections = []

            # Parse results
            for r in results:
                boxes = r.boxes.xyxy
                confs = r.boxes.conf
                class_ids = r.boxes.cls
                for cls, conf in zip(class_ids, confs):
                    label = self.model.names[int(cls)]
                    detections.append(f"{label}:{conf:.2f}")

            # Publish detections as comma-separated string
            if detections:
                self.publisher_.publish(String(data=",".join(detections)))
                self.get_logger().info(f"🔎 Detections: {detections}")

        except Exception as e:
            self.get_logger().error(f"Error in image_callback: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = YOLONode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
