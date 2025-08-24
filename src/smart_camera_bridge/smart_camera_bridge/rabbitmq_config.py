import os

class RabbitMQConfig:
    def __init__(self):
        self.user = os.getenv('RABBITMQ_USER', 'guest')
        self.password = os.getenv('RABBITMQ_PASS', 'guest')
        self.host = os.getenv('RABBITMQ_HOST', 'localhost')
        self.port = int(os.getenv('RABBITMQ_PORT', 5672))
        self.url = f"amqp://{self.user}:{self.password}@{self.host}:{self.port}/"
        
        # Exchanges phải khớp 100% với bên ở backend ../smart_camera_backend/app/services/rabbitmq_service.py
        self.exchange_cameras = "camera.events"
        self.exchange_detections = "detections.events"
        
        # Queues (dùng cho stream)
        self.queue_stream_prefix = 'camera.stream'
        self.queue_info_prefix = 'camera.info'
