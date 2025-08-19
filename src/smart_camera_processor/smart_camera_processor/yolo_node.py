import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge
import cv2
from ultralytics import YOLO
import re
import time
from threading import Timer

class MultiCameraYOLONode(Node):
    """
    ROS2 Node: YOLOv8 Object Detection cho nhiều camera
    Tự động discover và subscribe vào tất cả /camera/{id}/frames
    """
    def __init__(self):
        super().__init__('yolo_node')
        
        # Dictionary để lưu subscriptions
        self.camera_subscriptions = {}
        
        # Publisher: gửi kết quả detection
        self.publisher_ = self.create_publisher(String, '/processor/detections', 10)
        
        # CV Bridge
        self.bridge = CvBridge()
        
        # Load YOLOv8 model
        try:
            self.model = YOLO("yolov8n.pt")
            self.get_logger().info("✅ YOLOv8 model loaded successfully")
        except Exception as e:
            self.get_logger().error(f"❌ Failed to load YOLO model: {e}")
            raise
        
        # Timer để periodically discover camera topics
        self.discovery_timer = self.create_timer(5.0, self.discover_camera_topics)
        
        self.get_logger().info("✅ Multi-Camera YOLO Node initialized")
        
        # Discover ngay lập tức
        self.discover_camera_topics()
    
    def discover_camera_topics(self):
        """
        Tự động tìm và subscribe vào tất cả camera topics
        """
        try:
            # Lấy danh sách tất cả topics
            topic_names_and_types = self.get_topic_names_and_types()
            
            # Pattern để match /camera/{id}/frames
            camera_pattern = re.compile(r'^/camera/([^/]+)/frames$')
            
            current_cameras = set()
            
            for topic_name, topic_types in topic_names_and_types:
                match = camera_pattern.match(topic_name)
                if match and 'sensor_msgs/msg/Image' in topic_types:
                    camera_id = match.group(1)
                    current_cameras.add(camera_id)
                    
                    # Nếu chưa subscribe thì tạo subscription mới
                    if camera_id not in self.camera_subscriptions:
                        self.create_camera_subscription(camera_id, topic_name)
            
            # Cleanup các subscription không còn cần thiết
            cameras_to_remove = set(self.camera_subscriptions.keys()) - current_cameras
            for camera_id in cameras_to_remove:
                self.remove_camera_subscription(camera_id)
                
        except Exception as e:
            self.get_logger().error(f"Error in discover_camera_topics: {e}")
    
    def create_camera_subscription(self, camera_id: str, topic_name: str):
        """
        Tạo subscription cho một camera
        """
        try:
            subscription = self.create_subscription(
                Image,
                topic_name,
                lambda msg, cid=camera_id: self.image_callback(msg, cid),
                10
            )
            
            self.camera_subscriptions[camera_id] = subscription
            self.get_logger().info(f"🔗 Subscribed to camera '{camera_id}' at {topic_name}")
            
        except Exception as e:
            self.get_logger().error(f"Failed to create subscription for {camera_id}: {e}")
    
    def remove_camera_subscription(self, camera_id: str):
        """
        Xóa subscription cho camera không còn hoạt động
        """
        if camera_id in self.camera_subscriptions:
            # ROS2 tự động cleanup subscription khi node destroy
            del self.camera_subscriptions[camera_id]
            self.get_logger().info(f"🔌 Unsubscribed from camera '{camera_id}'")
    
    def image_callback(self, msg: Image, camera_id: str):
        """
        Callback xử lý ảnh từ camera
        """
        try:
            # Convert ROS Image -> OpenCV image
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            
            # YOLO inference
            results = self.model(cv_image)
            
            detections = []
            
            # Parse results
            for r in results:
                if r.boxes is not None:
                    boxes = r.boxes.xyxy
                    confs = r.boxes.conf
                    class_ids = r.boxes.cls
                    
                    for cls, conf in zip(class_ids, confs):
                        label = self.model.names[int(cls)]
                        detections.append(f"{label}:{conf:.2f}")
            
            # Publish detections với camera_id
            if detections:
                detection_msg = f"[{camera_id}] {','.join(detections)}"
                self.publisher_.publish(String(data=detection_msg))
                self.get_logger().info(f"🔎 Camera {camera_id}: {detections}")
                
        except Exception as e:
            self.get_logger().error(f"Error processing image from camera {camera_id}: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = MultiCameraYOLONode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()