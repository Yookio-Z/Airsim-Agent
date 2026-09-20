# 相机防抖：AirSim 原生云台（Gimbal）配置

## 问题

飞机加减速时机体必然俯仰（向前加速要低头、刹车要抬头），相机固定在机体上时画面会跟着上下扫动，目标容易移出视野，检测框跟不上画面。这不是控制参数问题，而是相机与机体姿态耦合的问题，需要把相机姿态从机体姿态里解耦出来。

## 方案：用 AirSim 引擎内云台，而不是软件补偿

`settings.json` 里每个相机可以带一个 `Gimbal` 块。核对本机 AirSim 源码
（`Unreal/Plugins/AirSim/Source/PIPCamera.cpp`）确认了它的语义：

- `Tick()` 每渲染帧读取相机当前世界旋转，把**世界** pitch / roll 直接替换为 `Gimbal.Pitch` / `Gimbal.Roll`，然后 `SetActorRotation`；yaw 只有在 `Gimbal.Yaw` 不是 NaN 时才被替换。
- 所以 `"Stabilization": 1` + `Pitch: -15` + `Roll: 0` 表示：相机世界俯仰恒定 -15°、世界横滚恒定 0°，yaw 仍然跟着机头转。
- 稳定性来自引擎 tick（渲染帧级），不受我们 4~5 FPS 的采集频率限制。

同一份源码里 `setCameraPose()`（我们 RPC 侧的 `simSetCameraPose`）在云台开启时**写入的是 gimbal 目标角**，也就是会用我们按采集帧采样算出来的姿态覆盖引擎云台目标，并把 yaw 从 NaN 覆盖成数值。两种机制会互相打架，因此：

- **原生云台是首选路径**，配置在 settings.json；
- 软件补偿（`AirSimController.configure_camera_stabilization`，按采集帧采样机体姿态后调 `simSetCameraPose`）降级为 opt-in，默认关闭，仅在没有 `Gimbal` 块的环境下需要时用 `DRONE_CAMERA_STABILIZATION=1` 打开。

## 当前配置（`%USERPROFILE%\Documents\AirSim\settings.json`）

```json
"CameraImage": {
  "X": 0, "Y": 0, "Z": -1,
  "Pitch": -15, "Roll": 0, "Yaw": 0,
  "Gimbal": { "Stabilization": 1, "Pitch": -15, "Roll": 0 }
},
"CameraDepth": {
  "X": 0, "Y": 0, "Z": -1,
  "Pitch": -15, "Roll": 0, "Yaw": 0,
  "Gimbal": { "Stabilization": 1, "Pitch": -15, "Roll": 0 }
}
```

要点：

- **不要写 `"Yaw": "NaN"`**。`Settings::getFloat` 内部是 `doc_[name].get<float>()`，字符串会抛 `type_error`。而 `GimbalSetting::rotation` 默认就是 `nanRotation()`，所以**省略 `Yaw` 字段**即可得到 NaN（yaw 跟随机体），这也是我们想要的行为。
- `Pitch` / `Roll` 必须显式写，否则默认 NaN，会变成"三个轴都不锁定"，云台等于没开。
- `CameraDepth` 的挂载 `Pitch` 由 0 改为 -15，与 `CameraImage` 保持一致（两者云台目标都是世界 -15°，画面朝向一致）。
- `CaptureSettings` 里新增的 `{"ImageType": -1}` 是 AirSim 的合法写法（`CaptureSettingsMap` 是 `std::map<int, CaptureSetting>`，初始化时保留 -1 键），作用是配置相机组件本身而不是某个取像类型；这里没有写任何参数，属于空操作，不影响画面输出。

## 生效条件

AirSim 只在启动时读取 settings.json，**没有运行时重载设置**的 RPC（AirLib / UE 插件里都没有 `LoadSettings`）。改完需要重启 AirSim（UE 编辑器里 Stop → Play）。PX4 SITL 会重连 TCP 4560。

## 验证方法

`scripts/probe_camera_gimbal.py` 只发只读 RPC（`simGetVehiclePose` / `simGetCameraInfo`），在飞机前后移动时采样机体与相机的世界姿态：

```
.venv/Scripts/python.exe scripts/probe_camera_gimbal.py --seconds 40 --hz 10
```

判据：机体 pitch 变化明显（> 2°）而相机世界 pitch 变化很小（< 机体变化的 35%）→ 云台生效。若相机 pitch 与机体 pitch 同步变化，说明 `Gimbal` 块没生效（配置未重启加载，或字段写错）。

## 已知局限

- 云台只稳定**姿态**，不消除平移视差：飞机横向/前后移动时近处目标仍会在画面里移动。
- 只做姿态稳定，不是"目标锁定"：相机不会主动指向目标，目标跑出视野后不会自动追回来。主动指向需要单独的云台/瞄准控制。
- 俯仰稳定后相机世界 pitch 恒为 -15°，但 `perception_airsim_adapter.build_depth_fn` 的深度投影仍只使用机体 yaw 与位置（未把 -15° 俯仰纳入外参）。这是既有近似，云台让它从"随俯仰变化"变成"恒定偏差"，如果之后要做精确的世界坐标定位，应把相机俯仰角一并代入投影。
- 软件补偿路径（`DRONE_CAMERA_STABILIZATION=1`）启用时，深度世界坐标投影会被主动抑制（`camera_stabilization_status(...)["enabled"]` 为真时返回 `valid: False`），因为按采集帧采样出来的外参不足以支撑世界坐标计算。
