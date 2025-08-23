import os

class RabbitMQConfig:
    def __init__(self):
        self.user = os.getenv('RABBITMQ_USER', 'guest')
        self.password = os.getenv('RABBITMQ_PASS', 'guest')
        self.host = os.getenv('RABBITMQ_HOST', 'localhost')
        self.port = int(os.getenv('RABBITMQ_PORT', 5672))
        self.url = f"amqp://{self.user}:{self.password}@{self.host}:{self.port}/"
        
        # Exchanges
        self.exchange_cameras = 'cameras.events'
        self.exchange_detections = 'detections.events'
        
        # Queues (dùng cho stream)
        self.queue_stream_prefix = 'camera.stream'
        self.queue_info_prefix = 'camera.info'
