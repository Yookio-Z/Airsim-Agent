# 视觉管道与单源架构(2026-08-31 定稿)

> 本文记录当前系统的视觉链路：图像来源、检测、前端展示、Agent 消费，
> 以及"感知轴单源"架构的设计与现状。配套代码入口见文末清单。

## 1. 架构：单个取帧源，两个消费者

```
AirSim 相机0(前视 640×480, Pitch -15°, FOV 90°)
    │  1Hz 唯一取帧(RPC simGetImages)
    ▼
感知轴 LocalPerceptionEngine(UI 进程后台线程)
    ├─ YOLO-World v2 检测(开放词汇, car 词表扩展)
    ├─ 标注帧缓存 annotated_frame()  ──> 前端相机面板(读缓存, 零 AirSim 请求)
    └─ 目标快照/事件                    ──> Agent / LLM(perception_status 工具)
```

- **感知轴是唯一从 AirSim 取图像的消费者**：前端面板 700ms 轮询只读缓存，
  不直接请求 AirSim；Agent 查询同一快照。面板显示与 Agent 确认严格一致。
- 感知轴不可用时，preview 路径**回退**到 AirSim 直连(服务端取帧+检测+画框)，
  保证功能可用。

## 2. 各环节实现

### 图像源
- `settings.json` 相机 0：前视，`Pitch: -15`(从 -45 调平，俯视视角 YOLO 检出弱)、
  `X: 0.45`(前移避开桨叶遮挡)、FOV 90°、640×480。
- 取帧走 `simGetImages(Scene)`，RPC 约 250~350ms；**AirSim 图像管线只能承受低频
  请求**：>=1Hz 持续取帧会导致 UE 冻结(实测连续第二个请求即可悬挂)。
  这是环境硬限制，单源+1Hz 是当前安全值。

### 检测
- `yolo_detection.py`：YOLO-World v2(`models/yolov8s-worldv2.pt`)，
  词表按目标类别扩展(`car → car/vehicle/minivan/cab/truck/suv`)，阈值 0.20~0.25。
- 跨线程推理锁 `_yolo_infer_lock`：感知线程、检测路径并发调用同一模型实例会
  死锁，必须串行。
- 实测：斜视角车 0.64、近距 0.33~0.69；俯视车顶检出弱(已调相机缓解)。

### 感知轴(单源核心)
- `perception_axis.py`：`LocalPerceptionEngine` 1Hz 取帧→检测→更新快照
  `{targets, primary, events}`，并**缓存标注帧**(`_cache_annotated`：
  画框+黑底黄字标签 → JPEG 82% 质量)。
- 启动失败自动重试(30s)等待 AirSim/Jetson 晚起；健康日志每 50 帧输出一次。
- 已知问题(环境级)：**UI 进程内取帧偶发挂起**(独立进程正常)；AirSim 重启后
  需重启 UI 重建客户端。下一步：感知轴独立子进程化(绕开挂起，见 §5)。

### 前端相机面板
- 请求 `/api/camera/preview?source=airsim&detect=1`，优先读感知轴缓存；
  回退直连路径服务端取帧+检测+画框。
- 面板功能：四边/四角拖拽缩放(下/左/右+两角)、点击画面放大(1.6×/2.4×)、
  检测框黑底黄字、检测 HUD(`🎯 car 0.33`)悬浮画内、700ms 轮询。
- 前端轮询间隔曾为 90ms(每秒 11 次的 AirSim 请求)——**这是多次 UE 崩溃的元凶**，
  已改 700ms。

### Agent 消费
- `perception_status` 工具：健康 + 目标快照 + 事件；LLM 只读结果，不碰像素。
- 终端使用逻辑：用户在面板看到目标 → 指挥 Agent"靠近那辆车确认" →
  Agent 查询相同快照确认 → 选择性跟踪。

## 3. 相机参数注意事项

- 相机车辆名必须是 AirSim 真实名(`Drone1`)，面板曾用 MAVLink 名 `px4_sys1`
  (无效)导致请求异常，已修正为自动回退默认车辆。
- 修改 `settings.json`(相机位置/角度)需重启 AirSim 生效。

## 4. 关键文件

| 文件 | 职责 |
|---|---|
| `src/modules/perception_axis.py` | 单源引擎：取帧/检测/缓存/事件/自愈 |
| `src/modules/frame_source.py` | AirSim/RTSP/USB 帧源协议 |
| `src/modules/yolo_detection.py` | YOLO 封装 + 推理锁 |
| `src/agent/tool_executor.py` | preview 缓存优先路径 + 检测画框回退 |
| `src/ui/static/` | 相机面板前端(缩放/放大/HUD/轮询) |

## 5. Roadmap

1. **感知轴独立子进程**(绕开 UI 进程内取帧挂起；AirSim 单源在运行中真正成立)
2. 移动目标追踪闭环(感知→目标位置→底层速度伺服，Agent 只决策)
3. RTSP/Jetson 真机图像源接入(同一帧检测链路，只换帧源)
4. 深度定位启用(bbox+深度图→NED，现有代码已备)