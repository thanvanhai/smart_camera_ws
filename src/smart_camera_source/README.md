# Smart Camera Source

📷 **Mục tiêu**: Cung cấp nguồn hình ảnh từ RTSP, file video, hoặc USB camera vào ROS 2.

## Nodes
- `camera_node` → Lấy stream từ camera, publish topic `/camera/frames`.
- `camera_manager_node` → Quản lý danh sách camera động (add/remove camera) qua Service.

## ROS 2 Interfaces
- Topics:
  - `/camera/frames` (sensor_msgs/Image)
- Services:
  - `/camera/add` (std_srvs/Trigger hoặc custom AddCamera.srv)
  - `/camera/remove`

## Chạy thử
```bash
ros2 run smart_camera_source camera_node
ros2 run smart_camera_source camera_manager_node

#Cấu trúc
...
smart_camera_ws/
└─ src/
   └─ smart_camera_source/
      ├─ package.xml
      ├─ setup.py
      ├─ setup.cfg
      ├─ README.md
      └─ smart_camera_source/
         ├─ __init__.py
         ├─ camera_node.py
         └─ camera_manager_node.py
