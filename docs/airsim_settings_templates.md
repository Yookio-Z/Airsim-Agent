# AirSim settings.json 通信模式模板

> AirSim 的通信模式由 `Documents\AirSim\settings.json` 决定。系统提供三份基础
> 模板，UI「通信链路」面板可一键应用（自动备份原文件），切换后重启 AirSim 生效。

## 三份模板对应关系

| 模板 | settings.json 关键配置 | 对应系统后端 | 适用场景 |
|---|---|---|---|
| `airsim_simpleflight_multirotor` | `SimMode: Multirotor` + `VehicleType: SimpleFlight`（3 机） | `airsim` | 本机纯 AirSim 仿真，API 直接控制；3 架机用于多机前端/功能验证 |
| `px4_mavlink_udp_sitl` | `VehicleType: PX4Multirotor` + `UseUdp: true`（UdpPort 14540 / ControlPortLocal 14540 / Remote 14580） | `px4_mavlink` | AirSim 作为 PX4 仿真器，UDP 连本机/WSL 的 PX4 SITL |
| `px4_ros2_tcp_edge` | `VehicleType: PX4Multirotor` + `UseTcp: true`（TcpPort 4560 / ControlIp） | `px4_ros2` | TCP 连 Jetson/边端 PX4 SITL（`ControlIp` 按实际 IP 修改） |

## 使用流程

1. 启动系统 UI（`python -m src` 或 `scripts\start_ui.ps1`）
2. 打开「系统设置 → 通信链路」
3. 在 **AirSim settings.json 模板** 区选择目标模式 → 「应用模板」
4. 系统自动备份当前 `settings.json` 为 `settings.json.bak-<时间戳>`
5. 重启 AirSim（重新打开 Unreal 工程），再在「通信链路」选择对应连接并连接

## 模板文件位置

```
config/airsim_settings/
├── airsim_simpleflight_multirotor.json   # AirSim 纯仿真 · 3 机（SimpleFlight）
├── px4_mavlink_udp_sitl.json             # PX4 SITL · UDP（本机/WSL）
└── px4_ros2_tcp_edge.json                # PX4 SITL · TCP（Jetson/边端）
```

## 自定义

模板文件可直接编辑（如改 `ControlIp`、机架数量、相机参数）。编辑后刷新
「通信链路」面板即生效（无需重启 UI）。
