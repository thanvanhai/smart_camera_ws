from setuptools import setup

package_name = 'smart_camera_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/bridge.launch.py']),  # ← thêm dòng này
    ],
    install_requires=['setuptools', 'pika'],  # pika cho RabbitMQ
    zip_safe=True,
    maintainer='haicoi',
    maintainer_email='thanvanhai1021988@gmail.com',
    description='Bridge ROS2 detections to RabbitMQ',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={#nếu tạo file thực thi lauch nhớ thêm các node dưới đây vào ../src/smart_camera_bridge/launch/bridge.launch.py
        'console_scripts': [
            'bridge_node = smart_camera_bridge.bridge_node:main',
            'camera_lifecycle_bridge = smart_camera_bridge.camera_lifecycle_bridge:main',
            'detection_bridge = smart_camera_bridge.detection_bridge:main',
            'video_stream_bridge = smart_camera_bridge.video_stream_bridge:main',
        ],
    },
     package_data={
        'smart_camera_bridge': ['rabbitmq.json'],  # 👈 thêm dòng này
    },
    include_package_data=True,
)
