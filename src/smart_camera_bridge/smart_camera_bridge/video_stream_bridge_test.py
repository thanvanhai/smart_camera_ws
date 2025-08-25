import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import base64
import pika
import json
import time  # Thêm thư viện time


class VideoStreamBridgeTest(Node):
    def __init__(self, camera_id: str):
        super().__init__('video_stream_bridge_test')
        self.camera_id = camera_id
        self.bridge = CvBridge()

        # ROS2 subscriber
        self.subscription = self.create_subscription(
            Image,
            f'/camera/{camera_id}/frames',
            self.listener_callback,
            10
        )

        # RabbitMQ connection
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
            self.channel = connection.channel()
            self.queue_name = f'camera.stream.{camera_id}'
            
            # === Khai báo queue giống cấu hình backend ===
            self.channel.queue_declare(
                queue=self.queue_name,
                durable=False,
                auto_delete=True,
                arguments={
                    'x-max-length': 5,
                    'x-overflow': 'drop-head',
                    'x-message-ttl': 2000
                }
            )
            self.get_logger().info(f"✅ Connected to RabbitMQ, queue: {self.queue_name}")
        except Exception as e:
            self.get_logger().error(f"❌ Could not connect to RabbitMQ: {e}")
            raise

    def listener_callback(self, msg: Image):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            _, buffer = cv2.imencode('.jpg', frame)
            encoded_frame = base64.b64encode(buffer).decode('utf-8')

            # === Payload chuẩn JSON ===
            payload = {
                "frame": encoded_frame,  # Gửi toàn bộ frame
                "timestamp": time.time(),
                "height": msg.height,
                "width": msg.width,
                "fps": 30,  # Có thể thay bằng giá trị thực
                "codec": "MJPEG"
            }

            self.channel.basic_publish(
                exchange='',
                routing_key=self.queue_name,
                body=json.dumps(payload)  # Chuyển dict thành chuỗi JSON
            )

            self.get_logger().info(f"📤 Published frame to {self.queue_name}")
        except Exception as e:
            self.get_logger().error(f"❌ Error processing frame: {e}")


def main(args=None):
    rclpy.init(args=args)
    camera_id = "c9bafd1e7c0464c6ca0512b1470eeca3a"  # Thay bằng camera ID của bạn
    bridge = VideoStreamBridgeTest(camera_id)
    try:
        rclpy.spin(bridge)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
