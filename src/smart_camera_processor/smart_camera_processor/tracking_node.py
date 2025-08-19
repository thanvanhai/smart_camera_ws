import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import re
import json
from collections import defaultdict
import time
import cv2

class MultiCameraTrackingNode(Node):
    """
    ROS2 Node: Multi-Camera Object Tracking
    Nhận detections từ YOLO và thực hiện tracking qua nhiều camera
    """
    def __init__(self):
        super().__init__('tracking_node')
        
        # Subscribe vào detections từ YOLO
        self.detections_subscription = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )
        
        # Dictionary để lưu camera subscriptions cho images
        self.camera_image_subscriptions = {}
        
        # Publisher cho tracking results
        self.tracking_publisher = self.create_publisher(String, '/processor/tracking', 10)
        
        # CV Bridge để xử lý images
        self.bridge = CvBridge()
        
        # Tracking data structures
        self.camera_tracks = defaultdict(dict)  # {camera_id: {track_id: track_data}}
        self.global_track_id = 0
        self.track_history = defaultdict(list)  # {track_id: [positions]}
        
        # Timer để discover camera image topics
        self.discovery_timer = self.create_timer(5.0, self.discover_camera_images)
        
        # Timer để cleanup old tracks
        self.cleanup_timer = self.create_timer(10.0, self.cleanup_old_tracks)
        
        self.get_logger().info("✅ Multi-Camera Tracking Node initialized")
        
        # Discover ngay lập tức
        self.discover_camera_images()
    
    def discover_camera_images(self):
        """
        Tự động tìm và subscribe vào tất cả camera image topics để lấy frames cho tracking
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
        Tạo subscription cho camera images (để tracking)
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
            
            self.get_logger().info(f"📹 Subscribed to camera images '{camera_id}' at {topic_name}")
            
        except Exception as e:
            self.get_logger().error(f"Failed to create image subscription for {camera_id}: {e}")
    
    def remove_image_subscription(self, camera_id: str):
        """
        Xóa subscription cho camera không còn hoạt động
        """
        if camera_id in self.camera_image_subscriptions:
            del self.camera_image_subscriptions[camera_id]
            # Xóa tracks của camera này
            if camera_id in self.camera_tracks:
                del self.camera_tracks[camera_id]
            self.get_logger().info(f"📹 Unsubscribed from camera images '{camera_id}'")
    
    def image_callback(self, msg: Image, camera_id: str):
        """
        Callback nhận frames từ camera (để tracking)
        """
        try:
            # Lưu frame mới nhất cho tracking
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            
            self.camera_image_subscriptions[camera_id]['last_frame'] = cv_image
            self.camera_image_subscriptions[camera_id]['frame_time'] = time.time()
            
        except Exception as e:
            self.get_logger().error(f"Error processing image from camera {camera_id}: {e}")
    
    def detections_callback(self, msg: String):
        """
        Callback nhận detections từ YOLO và thực hiện tracking
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
            
            # Parse individual detections
            detections = []
            if detections_str:
                for det in detections_str.split(','):
                    if ':' in det:
                        label, conf = det.split(':', 1)
                        detections.append({
                            'label': label.strip(),
                            'confidence': float(conf),
                            'camera_id': camera_id
                        })
            
            # Thực hiện tracking
            tracked_objects = self.update_tracks(camera_id, detections)
            
            # Publish tracking results
            if tracked_objects:
                self.publish_tracking_results(camera_id, tracked_objects)
            
            self.get_logger().info(f"🎯 Camera {camera_id}: Tracking {len(tracked_objects)} objects")
            
        except Exception as e:
            self.get_logger().error(f"Error in detections_callback: {e}")
    
    def update_tracks(self, camera_id: str, detections: list) -> list:
        """
        Cập nhật tracking cho camera (simplified tracking algorithm)
        """
        current_time = time.time()
        tracked_objects = []
        
        # Lấy frame hiện tại nếu có
        current_frame = None
        if (camera_id in self.camera_image_subscriptions and 
            self.camera_image_subscriptions[camera_id]['last_frame'] is not None):
            current_frame = self.camera_image_subscriptions[camera_id]['last_frame']
        
        # Simplified tracking: assign track IDs based on object type and position
        for det in detections:
            # Tìm track phù hợp hoặc tạo track mới
            track_id = self.assign_track_id(camera_id, det, current_time)
            
            track_data = {
                'track_id': track_id,
                'camera_id': camera_id,
                'label': det['label'],
                'confidence': det['confidence'],
                'timestamp': current_time,
                'frame_available': current_frame is not None
            }
            
            # Lưu vào camera tracks
            self.camera_tracks[camera_id][track_id] = track_data
            
            # Lưu vào history
            self.track_history[track_id].append({
                'camera_id': camera_id,
                'timestamp': current_time,
                'label': det['label']
            })
            
            tracked_objects.append(track_data)
        
        return tracked_objects
    
    def assign_track_id(self, camera_id: str, detection: dict, current_time: float) -> int:
        """
        Assign track ID cho detection (simplified algorithm)
        """
        # Tìm track existing cho object type này
        for track_id, track_data in self.camera_tracks[camera_id].items():
            if (track_data['label'] == detection['label'] and
                current_time - track_data['timestamp'] < 5.0):  # 5 seconds timeout
                return track_id
        
        # Tạo track mới
        self.global_track_id += 1
        return self.global_track_id
    
    def publish_tracking_results(self, camera_id: str, tracked_objects: list):
        """
        Publish tracking results
        """
        try:
            # Format: [camera_id] track_id1:label1:conf1,track_id2:label2:conf2
            tracking_data = []
            for obj in tracked_objects:
                tracking_data.append(f"{obj['track_id']}:{obj['label']}:{obj['confidence']:.2f}")
            
            if tracking_data:
                message = f"[{camera_id}] {','.join(tracking_data)}"
                self.tracking_publisher.publish(String(data=message))
        
        except Exception as e:
            self.get_logger().error(f"Error publishing tracking results: {e}")
    
    def cleanup_old_tracks(self):
        """
        Xóa các tracks cũ không còn hoạt động
        """
        current_time = time.time()
        timeout = 10.0  # 10 seconds
        
        for camera_id in list(self.camera_tracks.keys()):
            tracks_to_remove = []
            
            for track_id, track_data in self.camera_tracks[camera_id].items():
                if current_time - track_data['timestamp'] > timeout:
                    tracks_to_remove.append(track_id)
            
            # Xóa old tracks
            for track_id in tracks_to_remove:
                del self.camera_tracks[camera_id][track_id]
                self.get_logger().info(f"🧹 Cleaned up old track {track_id} from camera {camera_id}")
    
    def get_tracking_stats(self) -> dict:
        """
        Lấy thống kê tracking
        """
        stats = {
            'total_cameras': len(self.camera_tracks),
            'active_tracks': sum(len(tracks) for tracks in self.camera_tracks.values()),
            'track_history_size': len(self.track_history)
        }
        return stats

def main(args=None):
    rclpy.init(args=args)
    node = MultiCameraTrackingNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # In thống kê cuối
        stats = node.get_tracking_stats()
        node.get_logger().info(f"📊 Final stats: {stats}")
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()