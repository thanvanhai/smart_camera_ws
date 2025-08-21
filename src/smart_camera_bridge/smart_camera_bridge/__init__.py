import os
import json

class RabbitMQConfig:
    def __init__(self, path=None):
        if path is None:
            # Lấy đường dẫn tới file rabbitmq.json trong package
            base_dir = os.path.dirname(__file__)
            path = os.path.join(base_dir, "rabbitmq.json")

        if not os.path.exists(path):
            raise FileNotFoundError(f"❌ RabbitMQ config not found: {path}")

        with open(path, "r") as f:
            data = json.load(f)

        self.url = data.get("url", "amqp://guest:guest@localhost:5672/")
        self.exchange_cameras = data["exchange"]["cameras"]
        self.exchange_detections = data["exchange"]["detections"]
