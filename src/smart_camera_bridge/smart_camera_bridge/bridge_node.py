import json
import threading
import pika
import rclpy
from rclpy.node import Node
from rclpy.task import Future
from std_msgs.msg import String
from smart_camera_bridge import RabbitMQConfig   # ✅ import class config
from smart_camera_interfaces.srv import AddCamera, RemoveCamera


class BridgeNode(Node):
    """
    Bridge ROS2 <-> RabbitMQ
    - Consume camera.created / camera.removed từ RabbitMQ -> gọi ROS service (DynamicCameraNode)
    - Subscribe ROS /processor/detections -> publish RabbitMQ detections.events
    """

    def __init__(self):
        super().__init__('bridge_node')

        # ROS2 subscription (detections từ processor)
        self.sub_detections = self.create_subscription(
            String,
            '/processor/detections',
            self.detections_callback,
            10
        )

        # ROS2 service clients
        self.cli_add = self.create_client(AddCamera, '/camera/add')
        self.cli_remove = self.create_client(RemoveCamera, '/camera/remove')

        while not self.cli_add.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn("⏳ Waiting for /camera/add service...")
        while not self.cli_remove.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn("⏳ Waiting for /camera/remove service...")
            
        # ✅ Load RabbitMQ config
        cfg = RabbitMQConfig()
        self.rabbitmq_url = cfg.url
        self.exchange_cameras = cfg.exchange_cameras
        self.exchange_detections = cfg.exchange_detections

        # RabbitMQ setup
        # self.rabbitmq_url = 'amqp://guest:guest@localhost:5672/'
        # self.exchange_cameras = 'camera.events'       # gộp cả created + removed
        # self.exchange_detections = 'detections.events'
        self._connect_rabbitmq()

        # Start consumer thread
        self._stop_flag = threading.Event()
        self.consumer_thread = threading.Thread(target=self._consume_camera_events, daemon=True)
        self.consumer_thread.start()

        self.get_logger().info("✅ BridgeNode initialized (ROS <-> RabbitMQ)")

    # -------- RabbitMQ helpers ----------
    def _connect_rabbitmq(self):
        params = pika.URLParameters(self.rabbitmq_url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

        # Declare exchanges
        self.channel.exchange_declare(exchange=self.exchange_cameras, exchange_type='fanout', durable=True)
        self.channel.exchange_declare(exchange=self.exchange_detections, exchange_type='fanout', durable=True)

    def _consume_camera_events(self):
        """Consume camera.created / camera.removed events"""
        result = self.channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue
        self.channel.queue_bind(exchange=self.exchange_cameras, queue=queue_name)

        self.get_logger().info("🔗 Start consuming camera events (created/removed)...")

        for method, properties, body in self.channel.consume(queue_name, inactivity_timeout=1):
            if body is None:
                if self._stop_flag.is_set():
                    break
                continue
            try:
                #kiểm tra dữ liệu nhận được có đúng hay không?
                # self.get_logger().info(f"📦 Raw message: {body}")
                payload = json.loads(body.decode())
                # self.get_logger().info(f"📦 Decoded payload: {payload}")
                action = payload.get("action")
                camera_id = str(payload.get("camera_id"))
                camera_url = payload.get("camera_url")

                if action == "created":
                    self.call_add_camera(camera_id, camera_url)
                elif action == "removed":
                    self.call_remove_camera(camera_id)

                self.channel.basic_ack(method.delivery_tag)
            except Exception as e:
                self.get_logger().error(f"❌ Error processing camera event: {e}")

    # -------- ROS Service calls ----------
    def call_add_camera(self, camera_id: str, camera_url: str):
        req = AddCamera.Request()
        req.camera_id = camera_id
        req.camera_url = camera_url

        future = self.cli_add.call_async(req)
        future.add_done_callback(lambda f: self._handle_response(f, f"AddCamera({camera_id})"))

    def call_remove_camera(self, camera_id: str):
        req = RemoveCamera.Request()
        req.camera_id = camera_id

        future = self.cli_remove.call_async(req)
        future.add_done_callback(lambda f: self._handle_response(f, f"RemoveCamera({camera_id})"))

    def _handle_response(self, future: Future, action: str):
        try:
            resp = future.result()
            if resp.success:
                self.get_logger().info(f"✅ {action} succeeded: {resp.message}")
            else:
                self.get_logger().warn(f"⚠️ {action} failed: {resp.message}")
        except Exception as e:
            self.get_logger().error(f"❌ {action} exception: {e}")

    # -------- ROS → RabbitMQ ----------
    def detections_callback(self, msg: String):
        try:
            raw_text = msg.data
            payload = {"detection": raw_text}

            self.channel.basic_publish(
                exchange=self.exchange_detections,
                routing_key='',
                body=json.dumps(payload).encode('utf-8'),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            self.get_logger().info(f"[Bridge] ROS detections → RabbitMQ: {payload}")
        except Exception as e:
            self.get_logger().error(f"❌ Publish detection failed: {e}")

    def destroy_node(self):
        self._stop_flag.set()
        try:
            if self.connection:
                self.connection.close()
        except:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
