from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='smart_camera_bridge',
            executable='camera_lifecycle_bridge', # Tên entry point trong setup.py
            name='camera_lifecycle_bridge',
            output='screen'
        ),
        Node(
            package='smart_camera_bridge',
            executable='detection_bridge', # Tên entry point trong setup.py
            name='detection_bridge',
            output='screen'
        ),
        Node(
            package='smart_camera_bridge',
            executable='video_stream_bridge', # Tên entry point trong setup.py
            name='video_stream_bridge',
            output='screen'
        ),
    ])