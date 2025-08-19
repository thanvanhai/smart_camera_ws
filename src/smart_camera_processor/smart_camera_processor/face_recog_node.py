import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import numpy as np
import re
import time
import os
from collections import defaultdict

# Optional: Face recognition libraries (uncomment if available)
# import face_recognition
# from deepface import DeepFace

class MultiFaceRecognitionNode(Node):
    """
    ROS2 Node: Multi-Camera Face Recognition
    Nhận detections từ YOLO, lấy frames từ cameras và thực hiện face recognition
    """
    def __init__(self):
        super().__init__('face_recog_node')
        
        # Subscribe vào detections từ YOLO
        self.detections_subscription = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )
        
        # Dictionary để lưu camera image subscriptions
        self.camera_image_subscriptions = {}
        
        # Publisher cho face recognition results
        self.face_publisher = self.create_publisher(String, '/processor/face_recognition', 10)
        
        # CV Bridge
        self.bridge = CvBridge()
        
        # Face recognition data
        self.known_faces = {}  # {name: encoding}
        self.face_cascade = None
        
        # Load face detection cascade
        self.load_face_detector()
        
        # Load known faces database
        self.load_known_faces()
        
        # Timer để discover camera topics
        self.discovery_timer = self.create_timer(5.0, self.discover_camera_images)
        
        # Stats
        self.recognition_stats = defaultdict(int)
        
        self.get_logger().info("✅ Multi-Camera Face Recognition Node initialized")
        
        # Discover ngay lập tức
        self.discover_camera_images()
    
    def load_face_detector(self):
        """
        Load face detection model
        """
        try:
            # Sử dụng OpenCV Haar Cascade (basic but reliable)
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            if os.path.exists(cascade_path):
                self.face_cascade = cv2.CascadeClassifier(cascade_path)
                self.get_logger().info("✅ OpenCV Face Cascade loaded")
            else:
                self.get_logger().warn("⚠️ Face cascade not found, using fallback detection")
                
        except Exception as e:
            self.get_logger().error(f"❌ Failed to load face detector: {e}")
    
    def load_known_faces(self):
        """
        Load database của known faces (có thể load từ file hoặc database)
        """
        try:
            # TODO: Implement loading known faces from database/files
            # Ví dụ structure:
            # self.known_faces = {
            #     'John Doe': face_encoding_array,
            #     'Jane Smith': face_encoding_array,
            # }
            
            # Placeholder - sẽ implement thực tế sau
            self.known_faces = {}
            self.get_logger().info(f"📋 Loaded {len(self.known_faces)} known faces from database")
            
        except Exception as e:
            self.get_logger().error(f"❌ Failed to load known faces: {e}")
    
    def discover_camera_images(self):
        """
        Tự động tìm và subscribe vào tất cả camera image topics
        """
        try:
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
                    if camera_id not in self.camera_image_subscriptions:
                        self.create_image_subscription(camera_id, topic_name)
            
            # Cleanup các subscription không còn cần thiết
            cameras_to_remove = set(self.camera_image_subscriptions.keys()) - current_cameras
            for camera_id in cameras_to_remove:
                self.remove_image_subscription(camera_id)
                
        except Exception as e:
            self.get_logger().error(f"Error in discover_camera_images: {e}")
    
    def create_image_subscription(self, camera_id: str, topic_name: str):
        """
        Tạo subscription cho camera images
        """
        try:
            subscription = self.create_subscription(
                Image,
                topic_name,
                lambda msg, cid=camera_id: self.image_callback(msg, cid),
                10
            )
            
            self.camera_image_subscriptions[camera_id] = {
                'subscription': subscription,
                'last_frame': None,
                'frame_time': None
            }
            
            self.get_logger().info(f"👤 Subscribed to camera '{camera_id}' for face recognition")
            
        except Exception as e:
            self.get_logger().error(f"Failed to create image subscription for {camera_id}: {e}")
    
    def remove_image_subscription(self, camera_id: str):
        """
        Xóa subscription cho camera không còn hoạt động
        """
        if camera_id in self.camera_image_subscriptions:
            del self.camera_image_subscriptions[camera_id]
            self.get_logger().info(f"👤 Unsubscribed from camera '{camera_id}'")
    
    def image_callback(self, msg: Image, camera_id: str):
        """
        Callback nhận frames từ camera
        """
        try:
            # Convert và lưu frame mới nhất
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            
            self.camera_image_subscriptions[camera_id]['last_frame'] = cv_image
            self.camera_image_subscriptions[camera_id]['frame_time'] = time.time()
            
        except Exception as e:
            self.get_logger().error(f"Error processing image from camera {camera_id}: {e}")
    
    def detections_callback(self, msg: String):
        """
        Callback nhận detections và thực hiện face recognition nếu có person detected
        """
        try:
            detection_data = msg.data
            
            # Parse detection data: "[camera_id] object1:conf1,object2:conf2"
            if detection_data.startswith('['):
                # Multi-camera format
                match = re.match(r'\[([^\]]+)\]\s*(.+)', detection_data)
                if match:
                    camera_id = match.group(1)
                    detections_str = match.group(2)
                else:
                    self.get_logger().warn(f"Unable to parse detection format: {detection_data}")
                    return
            else:
                # Single camera format (backward compatibility)
                camera_id = "default"
                detections_str = detection_data
            
            # Kiểm tra có person detection không
            has_person = self.check_person_detection(detections_str)
            
            if has_person:
                # Thực hiện face recognition
                face_results = self.perform_face_recognition(camera_id)
                
                if face_results:
                    self.publish_face_results(camera_id, face_results)
                    self.get_logger().info(f"👤 Camera {camera_id}: Recognized {len(face_results)} faces")
            
        except Exception as e:
            self.get_logger().error(f"Error in detections_callback: {e}")
    
    def check_person_detection(self, detections_str: str) -> bool:
        """
        Kiểm tra có person detection trong string không
        """
        if not detections_str:
            return False
            
        for det in detections_str.split(','):
            if ':' in det:
                label, _ = det.split(':', 1)
                if label.strip().lower() == 'person':
                    return True
        return False
    
    def perform_face_recognition(self, camera_id: str) -> list:
        """
        Thực hiện face recognition trên frame hiện tại
        """
        try:
            # Lấy frame hiện tại
            if (camera_id not in self.camera_image_subscriptions or 
                self.camera_image_subscriptions[camera_id]['last_frame'] is None):
                return []
            
            frame = self.camera_image_subscriptions[camera_id]['last_frame']
            frame_time = self.camera_image_subscriptions[camera_id]['frame_time']
            
            # Kiểm tra frame có quá cũ không (>2 seconds)
            if time.time() - frame_time > 2.0:
                return []
            
            # Detect faces trong frame
            faces = self.detect_faces(frame)
            
            # Recognize faces
            recognized_faces = []
            for face_location in faces:
                identity = self.recognize_face(frame, face_location)
                recognized_faces.append({
                    'identity': identity,
                    'location': face_location,
                    'timestamp': time.time(),
                    'camera_id': camera_id
                })
                
                # Update stats
                self.recognition_stats[identity] += 1
            
            return recognized_faces
            
        except Exception as e:
            self.get_logger().error(f"Error in face recognition for camera {camera_id}: {e}")
            return []
    
    def detect_faces(self, frame):
        """
        Detect faces trong frame
        """
        faces = []
        
        try:
            if self.face_cascade is not None:
                # Sử dụng OpenCV Haar Cascade
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                detected_faces = self.face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(30, 30)
                )
                
                # Convert sang format (top, right, bottom, left)
                for (x, y, w, h) in detected_faces:
                    faces.append((y, x + w, y + h, x))
            
            # TODO: Có thể thêm các face detection models khác
            # if face_recognition module available:
            # faces = face_recognition.face_locations(frame)
            
        except Exception as e:
            self.get_logger().error(f"Error in face detection: {e}")
        
        return faces
    
    def recognize_face(self, frame, face_location):
        """
        Recognize identity của face
        """
        try:
            # TODO: Implement actual face recognition
            # Ví dụ với face_recognition library:
            # face_encoding = face_recognition.face_encodings(frame, [face_location])
            # if face_encoding:
            #     matches = face_recognition.compare_faces(
            #         list(self.known_faces.values()), 
            #         face_encoding[0]
            #     )
            #     if True in matches:
            #         return list(self.known_faces.keys())[matches.index(True)]
            
            # Placeholder implementation
            return "Unknown"
            
        except Exception as e:
            self.get_logger().error(f"Error in face recognition: {e}")
            return "Error"
    
    def publish_face_results(self, camera_id: str, face_results: list):
        """
        Publish face recognition results
        """
        try:
            # Format: [camera_id] identity1,identity2,identity3
            identities = [result['identity'] for result in face_results]
            
            if identities:
                message = f"[{camera_id}] {','.join(identities)}"
                self.face_publisher.publish(String(data=message))
        
        except Exception as e:
            self.get_logger().error(f"Error publishing face results: {e}")
    
    def get_face_stats(self) -> dict:
        """
        Lấy thống kê face recognition
        """
        return {
            'total_cameras': len(self.camera_image_subscriptions),
            'known_faces': len(self.known_faces),
            'recognition_stats': dict(self.recognition_stats)
        }

def main(args=None):
    rclpy.init(args=args)
    node = MultiFaceRecognitionNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # In thống kê cuối
        stats = node.get_face_stats()
        node.get_logger().info(f"👤 Final face recognition stats: {stats}")
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()