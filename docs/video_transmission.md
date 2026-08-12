# 视频回传与图传方案

> 状态：已实现 RTSP 拉流 + JPEG 轮询预览；WebRTC 低延迟路线为 roadmap
> 更新日期：2026-08-13

## 1. 架构总览

```
Jetson 机载电脑（真实摄像头）               Windows 地面站
┌──────────────────────────┐   RTSP    ┌──────────────────────────────────┐
│ v4l2src → h264 → rtsp    │──────────▶│ RtspFrameSource (cv2 拉流解码)     │
│ (GStreamer/ffmpeg 推流)  │  (UDP/图传)│ RtspCameraController.capture_image │
└──────────────────────────┘           │      ↓ JPEG bytes                 │
                                       │  _encode_preview_frame（复用）      │
                                       │      ↓                            │
                                       │  /api/camera/preview (90ms 轮询)   │
                                       │      ↓                            │
                                       │  摄像头面板（前端不变）              │
                                       └──────────────────────────────────┘
```

系统只要求"图像源给出最新一帧 BGR numpy"（`src/modules/frame_source.py` 的
`FrameSource` 协议），AirSim RPC 帧和真实 RTSP 流都实现该协议，预览与
拍照管道完全复用，前端零改动。

## 2. 已实现（2026-08-13）

- `FrameSource` 协议 + `AirSimFrameSource` + `RtspFrameSource`（断流自动重连）
- `RtspCameraController`：轻量相机控制器（只支持 scene，JPEG 输出）
- 相机源工厂：`camera settings.source = "rtsp"` 时注册 RTSP 拍照工具
  （复用 `airsim_take_photo` 契约），预览走同一 JPEG 管道
- 前端：图像源选择器新增 `RTSP 摄像头`，显示 RTSP URL 输入框
- 延迟预期：200–500ms（监控级），OpenCV RTSP 后端 + JPEG 轮询

### Jetson 推流示例

```bash
# GStreamer：USB 摄像头 → H.264 → RTSP（rtsp-simple-server / mediamtx）
gst-launch-1.0 v4l2src device=/dev/video0 ! videoconvert ! \
  video/x-raw,width=1280,height=720,framerate=30/1 ! \
  x264enc tune=zerolatency bitrate=4000 ! h264parse ! \
  rtspclientsink location=rtsp://ground-station:8554/cam0

# 或 ffmpeg
ffmpeg -f v4l2 -i /dev/video0 -c:v h264 -preset ultrafast -tune zerolatency \
  -f rtsp rtsp://ground-station:8554/cam0
```

地面站端：摄像头设置 → 图像源 `RTSP 摄像头` → URL
`rtsp://jetson-ip:8554/cam0` → 保存。

## 3. 图传（数字图传）形态

图传链路本质是"机载视频 → 地面站"的传输通道，本方案不关心图传是
Wi-Fi / 4G / 专用数图传模块，只要地面站侧能拿到 RTSP（或 UDP 裸流）：

| 图传形态 | 接入方式 | 状态 |
|---|---|---|
| 机载推 RTSP（GStreamer/ffmpeg） | `RtspFrameSource` | ✅ 已实现 |
| 图传接收机提供 RTSP 输出 | `RtspFrameSource`（同） | ✅ 已实现 |
| 图传 UDP 裸 H.264 流 | 需 `UdpFrameSource`（FrameSource 新实现） | 按需补充 |
| WebRTC 低延迟（<100ms，FPV 级） | 前端信令 + MSE/WebRTC 播放 | Roadmap |

## 4. Roadmap：WebRTC 低延迟图传

- 地面站加 WebRTC 信令服务（如 aiortc），Jetson 侧推 H.264
- 前端 `RTCPeerConnection` + `video` 元素直接播放（替代 90ms JPEG 轮询）
- 收益：延迟 <100ms、码率自适应；成本：信令 + ICE + 前端改造，工作量约一个量级
- 触发条件：FPV 作业、避障级低延迟需求

## 5. 与感知管道的衔接

- RTSP 帧同样可接入 `perception_hub` 的 YOLO 检测（`FrameSource` 统一接口）
- 拍照工具返回 `image_base64`，LLM 多模态分析（`airsim_vlm_analyze_image`
  的 provider 形态）后续可通过 ROS Camera provider 复用同一帧源
- 录制：`src/replay` 当前只录遥测 JSONL，图像录制（jpg 序列/mp4）为后续项
