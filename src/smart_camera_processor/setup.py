from setuptools import setup
import os
from glob import glob

package_name = 'smart_camera_processor'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'opencv-python', 'cv_bridge', 'torch', 'numpy'],
    zip_safe=True,
    maintainer='haicoi',
    maintainer_email='thanvanhai1021988@gmail.com',
    description='AI processor package for smart camera system',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'yolo_node = smart_camera_processor.yolo_node:main',
            'tracking_node = smart_camera_processor.tracking_node:main',
            'face_recog_node = smart_camera_processor.face_recog_node:main',
        ],
    },
)
