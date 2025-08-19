import rclpy
from rclpy.node import Node

# ĐÚNG → import từ package interfaces
from smart_camera_interfaces.srv import AddCamera, RemoveCamera


class CameraManagerNode(Node):
    def __init__(self):
        super().__init__('camera_manager_node')
        self.cameras = {}  # Lưu {camera_id: camera_url}

        self.srv_add = self.create_service(AddCamera, '/camera/add', self.add_camera_callback)
        self.srv_remove = self.create_service(RemoveCamera, '/camera/remove', self.remove_camera_callback)

    def add_camera_callback(self, request, response):
        if request.camera_id in self.cameras:
            response.success = False
            response.message = f"Camera {request.camera_id} đã tồn tại"
            return response

        self.cameras[request.camera_id] = request.camera_url
        self.get_logger().info(f"✅ Added camera {request.camera_id}: {request.camera_url}")
        response.success = True
        response.message = f"Added camera {request.camera_id}"
        return response

    def remove_camera_callback(self, request, response):
        if request.camera_id not in self.cameras:
            response.success = False
            response.message = f"Camera {request.camera_id} không tồn tại"
            return response

        del self.cameras[request.camera_id]
        self.get_logger().info(f"🗑️ Removed camera {request.camera_id}")
        response.success = True
        response.message = f"Removed camera {request.camera_id}"
        return response


def main(args=None):
    rclpy.init(args=args)
    node = CameraManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
