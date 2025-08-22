import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge
from smart_camera_interfaces.srv import AddCamera, RemoveCamera, UpdateCameraURL
import threading

class DynamicCameraNode(Node):
    def __init__(self):
        super().__init__('dynamic_camera_node')

        # CV Bridge
        self.bridge = CvBridge()

        # Camera storage
        self.cameras = {}  # {camera_id: cv2.VideoCapture}
        self.camera_publishers = {}  # {camera_id: Publisher}
        self.camera_timers = {}  # {camera_id: Timer}

        # Services
        self.srv_add = self.create_service(AddCamera, '/camera/add', self.add_camera_callback)
        self.srv_remove = self.create_service(RemoveCamera, '/camera/remove', self.remove_camera_callback)
        self.srv_update_url = self.create_service(UpdateCameraURL, '/camera/update_url', self.update_camera_url_callback)

        self.get_logger().info("🎥 Dynamic Camera Node initialized")
        self.get_logger().info("📋 Available services:")
        self.get_logger().info("   - /camera/add")
        self.get_logger().info("   - /camera/remove")
        self.get_logger().info("   - /camera/update_url")

    # -------------------- Add Camera --------------------
    def add_camera_callback(self, request, response):
        camera_id = request.camera_id
        camera_url = request.camera_url

        try:
            if camera_id in self.cameras:
                response.success = False
                response.message = f"❌ Camera {camera_id} already exists"
                return response

            cap = self._open_camera(camera_url)
            if not cap or not cap.isOpened():
                response.success = False
                response.message = f"❌ Cannot open camera: {camera_url}"
                return response

            self.cameras[camera_id] = cap
            topic_name = f'/camera/{camera_id}/frames'
            self.camera_publishers[camera_id] = self.create_publisher(Image, topic_name, 10)
            self.camera_timers[camera_id] = self.create_timer(0.033, lambda cam=camera_id: self.capture_frame(cam))

            response.success = True
            response.message = f"✅ Camera {camera_id} added successfully"
            self.get_logger().info(f"✅ Added camera {camera_id} -> {topic_name}")

        except Exception as e:
            response.success = False
            response.message = f"❌ Error adding camera: {str(e)}"
            self.get_logger().error(f"❌ Error adding camera {camera_id}: {e}")

        return response

    # -------------------- Remove Camera --------------------
    def remove_camera_callback(self, request, response):
        camera_id = request.camera_id

        try:
            if camera_id not in self.cameras:
                response.success = False
                response.message = f"❌ Camera {camera_id} not found"
                return response

            # Stop timer
            if camera_id in self.camera_timers:
                self.camera_timers[camera_id].cancel()
                del self.camera_timers[camera_id]

            # Release camera
            if camera_id in self.cameras:
                self.cameras[camera_id].release()
                del self.cameras[camera_id]

            # Remove publisher
            if camera_id in self.camera_publishers:
                del self.camera_publishers[camera_id]

            response.success = True
            response.message = f"✅ Camera {camera_id} removed successfully"
            self.get_logger().info(f"🗑️ Removed camera {camera_id}")

        except Exception as e:
            response.success = False
            response.message = f"❌ Error removing camera: {str(e)}"
            self.get_logger().error(f"❌ Error removing camera {camera_id}: {e}")

        return response

    # -------------------- Update Camera URL --------------------
    def update_camera_url(self, camera_id: str, new_url: str) -> bool:
        """Cập nhật URL camera mà không remove/add"""
        if camera_id not in self.cameras:
            self.get_logger().warn(f"⚠️ Camera {camera_id} not found")
            return False

        # Release old stream
        old_cap = self.cameras[camera_id]
        if old_cap.isOpened():
            old_cap.release()

        # Open new stream
        cap = self._open_camera(new_url)
        if not cap or not cap.isOpened():
            self.get_logger().error(f"❌ Cannot open new URL: {new_url}")
            return False

        self.cameras[camera_id] = cap
        self.get_logger().info(f"✅ Camera {camera_id} URL updated to {new_url}")
        return True

    def update_camera_url_callback(self, request, response):
        success = self.update_camera_url(request.camera_id, request.new_url)
        response.success = success
        response.message = "✅ URL updated" if success else "❌ Failed to update URL"
        return response

    # -------------------- Capture Frame --------------------
    def capture_frame(self, camera_id):
        if camera_id not in self.cameras:
            return

        try:
            ret, frame = self.cameras[camera_id].read()
            if ret:
                msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
                msg.header.frame_id = camera_id
                msg.header.stamp = self.get_clock().now().to_msg()
                self.camera_publishers[camera_id].publish(msg)
            else:
                self.get_logger().warn(f"⚠️ Failed to read frame from {camera_id}")
        except Exception as e:
            self.get_logger().error(f"❌ Error capturing frame from {camera_id}: {e}")

    # -------------------- Helper --------------------
    def _open_camera(self, camera_url):
        if camera_url.isdigit():
            return cv2.VideoCapture(int(camera_url))
        return cv2.VideoCapture(camera_url)

    def __del__(self):
        for cap in self.cameras.values():
            cap.release()
        self.get_logger().info("🧹 Cameras released")


def main(args=None):
    rclpy.init(args=args)
    node = DynamicCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
