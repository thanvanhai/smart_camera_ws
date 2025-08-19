from setuptools import setup
import os
from glob import glob

package_name = 'smart_camera_source'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),   # đăng ký package
        ('share/' + package_name, ['package.xml']),  # copy package.xml
    ],
    install_requires=['setuptools', 'opencv-python', 'cv_bridge'],
    zip_safe=True,
    maintainer='haicoi',
    maintainer_email='thanvanhai1021988@gmail.com',
    description='Camera source package for smart camera system',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'dynamic_camera_node = smart_camera_source.dynamic_camera_node:main',
        ],
    },
)
