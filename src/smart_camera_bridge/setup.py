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
    ],
    install_requires=['setuptools', 'pika'],  # pika cho RabbitMQ
    zip_safe=True,
    maintainer='haicoi',
    maintainer_email='thanvanhai1021988@gmail.com',
    description='Bridge ROS2 detections to RabbitMQ',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bridge_node = smart_camera_bridge.bridge_node:main',
        ],
    },
     package_data={
        'smart_camera_bridge': ['rabbitmq.json'],  # 👈 thêm dòng này
    },
    include_package_data=True,
)
