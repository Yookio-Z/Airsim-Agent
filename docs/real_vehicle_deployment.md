# 真机 / Jetson 机载部署指南

> 本文回答三个问题:
> 1. 地面端怎么连真机(数传/图传各是什么、怎么配)
> 2. Jetson 机载怎么既控制又传画面(MAVLink 直连 + 板载 RTSP 转发)
> 3. 系统现在的接口预留(感知轴帧源、connection template)哪些已经够用、哪里要补

## 0. 三个事实先说清

- **数传 ≠ 图传**: 数传是低速(几 KB/s)MAVLink 控制链路,带宽只够飞行控制+遥测,**不传图像**;图传是独立的高带宽(2~10Mbps)视频流,常 1080p 30fps 走 RTSP。两者**物理上独立**,需要各自接通。
- **ROS 不是必须**: 我们系统的飞行控制、感知、视觉模型都走纯 Python+OpenCV+YOLO(无 ROS 依赖),MAVLink 协议直接发;**Jetson 上不装 ROS 也能完整工作**。只有当多算法并行融合/话题总线成了真需求时再考虑 ROS。
- **感知轴已经抽象成 FrameSource**: `src/modules/frame_source.py` 已实现 AirSim/RTSP/USB 三种帧源(都是同一接口),感知轴按 `frame_source` 字段切。换真机只是配置改 .env,代码零改动。

## 1. 系统当前能用的连接/帧源

### 1.1 控制链路(`connections`)

| 类型 id | 用途 | 关键参数 |
|---|---|---|
| `airsim` | 仿真 | host/port (默认 127.0.0.1:41452) |
| `auto` | 自动探测(局域网 udp:14550) | host/port/remotePort |
| `udp` | PX4 SITL UDP 或真机数传 UDP | host/portNumber/remotePort/realVehicle |
| `serial` | 真机数传 USB 串口(典型 Pixhawk Telem 2 口) | port/baud(115200)/realVehicle=true |
| `px4_ros2` | ROS2 网关(经 HTTP 桥) | url/workspace |

设置面板 → 连接 → 选对应模板填参数 → "连接"。**真机切换就是把后端切到 `px4_ros2` 或 `udp` 或 `serial`**。**代码零改动**。

### 1.2 感知帧源(`.env` 切换)

| `DRONE_PERCEPTION_FRAME_SOURCE` | 帧源 |
|---|---|
| `airsim` | 仿真:从 AirSim 相机 0 拉帧 |
| `rtsp` | 真机:Jetson 转发或图传 RtspFrameSource |
| `usb` | 本地 USB 摄像头(地面端调试用) |

加上 `DRONE_PERCEPTION_RTSP_URL` 即可切到图传(任何支持 RTSP 的设备都能用,SIYI/OpenIPC/DJI OcuSync 等大多数图传都有 RTSP 输出)。

### 1.3 Agent 看到的链路

新加 `/api/link` 端点一次返回飞行控制 + 感知 + 可用能力 + 人话提示:

```json
{
  "flight": {"backend": "px4_mavlink", "connected": true, "mode": "LOITER", "armed": false, ...},
  "perception": {"enabled": true, "online": false, "frame_source": "RtspFrameSource", "fps": 0.0, ...},
  "available": {"flight_control": true, "perception": false, "vlm": true},
  "hint": "飞行控制可用,感知离线(无法获取画面)。先检查感知配置(.env 或设置 → 连接)。"
}
```

设置面板加 "链路" 标签页,可视化呈现以上字段 + 常见连接方式清单。

## 2. 真机典型链路与配置步骤

### 场景 A: 简单"地面站 + 数传 + Jetson 板载"(无图传或 WiFi)

```
Jetson + PX4 ---[数传 USB]--- 地面站 (Windows)
        |                       |
        +--[板载相机 RTSP]-----[局域网 WiFi]-- 地面站
```

配置:
```
# .env
DRONE_PERCEPTION_ENABLED=true
DRONE_PERCEPTION_FRAME_SOURCE=rtsp
DRONE_PERCEPTION_RTSP_URL=rtsp://JETSON_IP:8554/streaming/main/
```

数传连接(在设置 → 连接):
- `type=serial`,port=COM5(查设备管理器),baud=115200,`realVehicle=true`

Jetson 端起板载 RTSP 转发(常见两种):
- `gst-launch-1.0 v4l2src ! ... ! x264enc ! rtph264pay ! udpsink host=GROUND_IP port=8554` 推流
- 或 OpenIPC / SIYI 自带的 RTSP 服务

### 场景 B: 图传 + 数传独立

```
Jetson + PX4 ---[数传 USB]--- 地面站 (Windows)
        |                       |
        +--[吊舱相机]---[图传 SDR]--- 地面站 [RTSP 接收]
        |                               |
        +------- 数传 ------- 控制     +--- 图传接收盒 --- RTSP 拉流
```

图传接收盒一般自带 RTSP 输出(查你图传型号,常见端口 8554/8555):
- 地面端插图传接收盒到 Windows USB
- 在地面站用 `VLC` 或我们的 `RtspFrameSource` 验证拉流:`rtsp://图传IP:端口/路径`
- 拉通后填入 `DRONE_PERCEPTION_RTSP_URL`

数传单独走:
- 数传 USB 接 Windows → 在连接面板选 `serial` → port/baud
- 或数传带以太网 → 选 `udp`,`realVehicle=true`

### 场景 C: 全 Jetson 在板(数传 UDP + Jetson 转发 MAVLink)

当数传是 IP 模式(常见 192.168.144.x):
- Jetson 通过数传与 PX4 通信(同机器)
- Jetson 装 `mavlink-router` 或 `mavros`,把 MAVLink 通过 UDP 转发到地面站 IP
- 地面站 `udp:JETSON_IP:14550` 收 MAVLink,`rtsp://JETSON_IP:8554/...` 收图

设置连接:
- `type=udp`,`host=JETSON_IP`,`portNumber=14550`,`realVehicle=true`

## 3. Jetson 机载推荐配置

Jetson 上需要起的两个服务:
1. **MAVLink 桥**(把 PX4 数传喂到地面站): `mavlink-router` 或 `pymavlink` 简单脚本
2. **相机推 RTSP**: `gstreamer` 或 `libcamera/v4l2` + RTSP 服务

参考容器化(可选):
```bash
# Jetson 上,一次性 mavlink-router 配置
mavlink-routerd &
# Jetson 上,gstreamer 推流
gst-launch-1.0 nvarguscamerasrc ! 'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' ! nvvidconv ! x264enc ! rtph264pay config-interval=1 pt-dynamic ! udpsink host=GROUND_IP port=8554
```

## 4. 工具面在真机下的对应

| 工具 | 仿真可用 | 真机可用 | 说明 |
|---|---|---|---|
| `drone_arm/disarm/takeoff/land/fly_to/...` | ✓ | ✓ | PX4 协议通用 |
| `drone_get_status` | ✓ | ✓ | 遥测通用 |
| `drone_rotate_to` | ✓ | ✓ | 含本次修复的 hdg 单位/yaw 速度掩码 |
| `perception_status` | ✓ | ✓ | 自动跟随感知轴帧源(AirSim/RTSP/USB) |
| `inspect_current_frame` | ✓ | ✓ | 同上 + fallback 直连帧源 |
| `airsim_take_photo` | 限定 airsim | ✗ 真机 | 真机改用 `perception_status` 取最后一帧 |
| `airsim_get_depth_map` | 限定 airsim | ✗ 真机 | 真机用单目估算(在 roadmap) |
| `airsim_vlm_*` | 别名 → inspect | ✓ | 别名自动转发,真机也工作 |

**结论: 飞行/感知/视觉 三大类工具在真机下都能正常工作**。airsim 命名的工具在前端 UI 上可见、但 LLM manifest 明确标注 backend 限制(已经在 `tools/manifest.py` 维护)。

## 5. 待你提供以提供精确配置

为了直接给你能跑的 .env 和 connection profile,需要你确认:
1. **数传型号与协议**: 常见 Siyi/赫星 Herelink/USB 数传/Pixhawk 原生 Telem
2. **图传型号与 RTSP 入口**: 常见 SIYI HM30/OpenIPC/DJI OcuSync/普通 5.8G 模拟图传
3. **Jetson 接吊舱方式**: CSI(MIPI)/USB/Gigabit Ethernet
4. **Jetson 推 RTSP 方式**: gstreamer(标准) / 商业图传自带 / Jetson 自带 RTSP 服务
5. **是否需要多机**: 文档里只写单机的真机流程;多机会更复杂,需要先确认

拿到这 5 个答案,我会直接出 `.env` 模板 + 推荐的 connection profile + Jetson 启动脚本,你只要刷入和接入硬件就能用。

## 6. 系统内部落地清单(本文档发布时已完成)

- [x] FrameSource 抽象(AirSim / RTSP / USB)
- [x] 感知轴可选帧源(感知轴代码已支持 RTSP/USB/AirSim,选什么帧源走 `frame_source` 配置)
- [x] inspect_current_frame 工具永远注册(vlm_provider 启用即暴露)
- [x] /api/link 端点:统一返回飞行/感知/可用状态 + 人话提示
- [x] 设置面板 "链路" 标签页:可视化所有链路状态 + 常见连接方式清单
- [x] Agent 默认模型切到 minimax-m3(支持图像)

待你给具体硬件型号后,本指南会出"X 数传 + Y 图传 + Jetson Z 接口"的具体 `.env` 与 Jetson 启动脚本。

## 7. ROS 的价值(未来选)

当前不建议加 ROS。**当出现以下任一情况时**,加 ROS 才划算:
- 多算法并行(检测+跟踪+避障+SLAM 都跑)需共享同一路相机和同一份遥测
- 现有话题总线(rclpy topics/services)更便于第三方模块接入
- 已有 ROS 组件必须集成(如某些行业模块)

届时可加的最小集合: `MAVROS`(MAVLink ↔ ROS topics)+ `image_transport`(RTSP ↔ ROS image topic),把感知轴和 MAVLink 适配成 ROS 节点,**不改变现有 Agent 与工具契约**。