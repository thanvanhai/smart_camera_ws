import json
import threading
import time
import pika
import rclpy
from rclpy.node import Node
from rclpy.task import Future
from std_msgs.msg import String
from smart_camera_bridge import RabbitMQConfig
from smart_camera_interfaces.srv import AddCamera, RemoveCamera


class BridgeNode(Node):
    """
    Bridge ROS2 <-> RabbitMQ with improved connection management
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

        # RabbitMQ connection management
        self.connection = None
        self.channel = None
        self.publisher_connection = None  # Separate connection for publishing
        self.publisher_channel = None
        
        # Threading controls
        self._stop_flag = threading.Event()
        self._connection_lock = threading.Lock()
        
        # Initialize connections
        self._setup_rabbitmq_connections()

        # Start consumer thread
        self.consumer_thread = threading.Thread(target=self._consume_camera_events, daemon=True)
        self.consumer_thread.start()

        self.get_logger().info("✅ BridgeNode initialized (ROS <-> RabbitMQ)")

    def _setup_rabbitmq_connections(self):
        """Setup separate connections for consumer and publisher"""
        try:
            # Consumer connection
            params = pika.URLParameters(self.rabbitmq_url)
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()

            # Publisher connection (separate to avoid threading issues)
            self.publisher_connection = pika.BlockingConnection(params)
            self.publisher_channel = self.publisher_connection.channel()

            # Declare exchanges on both connections
            for channel in [self.channel, self.publisher_channel]:
                channel.exchange_declare(
                    exchange=self.exchange_cameras, 
                    exchange_type='fanout', 
                    durable=True
                )
                channel.exchange_declare(
                    exchange=self.exchange_detections, 
                    exchange_type='fanout', 
                    durable=True
                )
                
            self.get_logger().info("✅ RabbitMQ connections established")
            
        except Exception as e:
            self.get_logger().error(f"❌ Failed to setup RabbitMQ connections: {e}")
            raise

    def _reconnect_publisher(self):
        """Reconnect publisher connection if needed"""
        with self._connection_lock:
            try:
                if self.publisher_connection and not self.publisher_connection.is_closed:
                    self.publisher_connection.close()
            except:
                pass
                
            try:
                params = pika.URLParameters(self.rabbitmq_url)
                self.publisher_connection = pika.BlockingConnection(params)
                self.publisher_channel = self.publisher_connection.channel()
                
                # Re-declare exchanges
                self.publisher_channel.exchange_declare(
                    exchange=self.exchange_detections, 
                    exchange_type='fanout', 
                    durable=True
                )
                
                self.get_logger().info("✅ Publisher connection reconnected")
                return True
                
            except Exception as e:
                self.get_logger().error(f"❌ Failed to reconnect publisher: {e}")
                return False

    def _consume_camera_events(self):
        """Consume camera.created / camera.removed events with retry logic"""
        max_retries = 5
        retry_count = 0
        
        while not self._stop_flag.is_set() and retry_count < max_retries:
            try:
                # Setup consumer queue
                result = self.channel.queue_declare(queue='', exclusive=True)
                queue_name = result.method.queue
                self.channel.queue_bind(exchange=self.exchange_cameras, queue=queue_name)

                self.get_logger().info("🔗 Start consuming camera events (created/removed)...")
                retry_count = 0  # Reset retry count on successful connection

                # Consume messages
                for method, properties, body in self.channel.consume(
                    queue_name, 
                    inactivity_timeout=1,
                    auto_ack=False
                ):
                    if self._stop_flag.is_set():
                        break
                        
                    if body is None:
                        continue
                        
                    try:
                        payload = json.loads(body.decode())
                        action = payload.get("action")
                        camera_id = str(payload.get("camera_id"))
                        camera_url = payload.get("camera_url")

                        self.get_logger().info(f"📦 Received camera event: {action} for camera {camera_id}")

                        if action == "created":
                            self.call_add_camera(camera_id, camera_url)
                        elif action == "removed":
                            self.call_remove_camera(camera_id)
                        else:
                            self.get_logger().warn(f"⚠️ Unknown action: {action}")

                        # Acknowledge message
                        if method:
                            self.channel.basic_ack(method.delivery_tag)
                            
                    except json.JSONDecodeError as e:
                        self.get_logger().error(f"❌ Invalid JSON in camera event: {e}")
                        if method:
                            self.channel.basic_nack(method.delivery_tag, requeue=False)
                    except Exception as e:
                        self.get_logger().error(f"❌ Error processing camera event: {e}")
                        if method:
                            self.channel.basic_nack(method.delivery_tag, requeue=True)

            except pika.exceptions.AMQPConnectionError as e:
                retry_count += 1
                self.get_logger().error(f"❌ RabbitMQ connection lost ({retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    time.sleep(min(retry_count * 2, 30))  # Exponential backoff
                    try:
                        self._setup_rabbitmq_connections()
                    except:
                        continue
                        
            except Exception as e:
                retry_count += 1
                self.get_logger().error(f"❌ Consumer thread error ({retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    time.sleep(5)
                else:
                    break
                    
        self.get_logger().warn("🔄 Consumer thread stopped")

    def call_add_camera(self, camera_id: str, camera_url: str):
        """Call ROS service to add camera"""
        req = AddCamera.Request()
        req.camera_id = camera_id
        req.camera_url = camera_url

        future = self.cli_add.call_async(req)
        future.add_done_callback(
            lambda f: self._handle_response(f, f"AddCamera({camera_id})")
        )

    def call_remove_camera(self, camera_id: str):
        """Call ROS service to remove camera"""
        req = RemoveCamera.Request()
        req.camera_id = camera_id

        future = self.cli_remove.call_async(req)
        future.add_done_callback(
            lambda f: self._handle_response(f, f"RemoveCamera({camera_id})")
        )

    def _handle_response(self, future: Future, action: str):
        """Handle ROS service response"""
        try:
            resp = future.result()
            if resp.success:
                self.get_logger().info(f"✅ {action} succeeded: {resp.message}")
            else:
                self.get_logger().warn(f"⚠️ {action} failed: {resp.message}")
        except Exception as e:
            self.get_logger().error(f"❌ {action} exception: {e}")

    def detections_callback(self, msg: String):
        """Publish ROS detections to RabbitMQ with retry logic"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                with self._connection_lock:
                    # Check if publisher connection is healthy
                    if (not self.publisher_connection or 
                        self.publisher_connection.is_closed or
                        not self.publisher_channel or 
                        self.publisher_channel.is_closed):
                        
                        if not self._reconnect_publisher():
                            continue
                    
                    raw_text = msg.data
                    payload = {
                        "detection": raw_text,
                        "timestamp": time.time()
                    }

                    self.publisher_channel.basic_publish(
                        exchange=self.exchange_detections,
                        routing_key='',
                        body=json.dumps(payload).encode('utf-8'),
                        properties=pika.BasicProperties(
                            delivery_mode=2,  # Persistent message
                            timestamp=int(time.time())
                        )
                    )
                    
                    self.get_logger().info(f"📤 [Bridge] ROS detections → RabbitMQ: {raw_text[:100]}...")
                    return  # Success, exit retry loop
                    
            except pika.exceptions.AMQPConnectionError as e:
                self.get_logger().warn(f"⚠️ Connection error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                    
            except Exception as e:
                self.get_logger().error(f"❌ Publish detection failed on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                    
        self.get_logger().error("❌ Failed to publish detection after all retries")

    def destroy_node(self):
        """Cleanup resources"""
        self.get_logger().info("🛑 Shutting down BridgeNode...")
        
        # Stop consumer thread
        self._stop_flag.set()
        
        # Wait for consumer thread to finish
        if hasattr(self, 'consumer_thread') and self.consumer_thread.is_alive():
            self.consumer_thread.join(timeout=5)
            
        # Close connections
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
        except Exception as e:
            self.get_logger().warn(f"Error closing consumer connection: {e}")
            
        try:
            if self.publisher_connection and not self.publisher_connection.is_closed:
                self.publisher_connection.close()
        except Exception as e:
            self.get_logger().warn(f"Error closing publisher connection: {e}")
            
        super().destroy_node()
        self.get_logger().info("✅ BridgeNode shutdown complete")


def main(args=None):
    rclpy.init(args=args)
    node = BridgeNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("🛑 Received interrupt signal")
    except Exception as e:
        node.get_logger().error(f"❌ Unexpected error: {e}")
    finally:
        try:
            node.destroy_node()
        except:
            pass
        rclpy.shutdown()


if __name__ == '__main__':
    main()