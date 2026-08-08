# ROS Provider Gateway

> 文档状态：当前 PX4 ROS2 Gateway 的运行和接口文档。它继续作为 `px4_ros2` 的确定性控制边界；边侧 Agent 通过其调用飞控，不能绕过它直接发布 PX4 控制 Topic。完整升级路线见 [`system_upgrade_plan.md`](system_upgrade_plan.md)。

The ROS Provider Gateway is the standard boundary between the Windows ground
station and ROS/PX4 running in WSL or on an onboard companion computer.

There are separate processes because they are separate runtime boundaries:

- `8765`: Windows UI and Agent runtime. This is the operator-facing ground
  station.
- `8766`: ROS Provider Gateway. This must run where ROS 2 and `px4_msgs` are
  installed, normally WSL during development or a companion computer onboard.
- PX4 SITL or PX4 firmware: the autopilot.
- Micro-XRCE-DDS-Agent: the PX4-to-ROS2 transport for `/fmu/*` topics.

For development, after PX4 SITL and Micro-XRCE-DDS-Agent are already running in
WSL, Windows can start the gateway and UI together:

```powershell
.\scripts\start_px4_ros2_dev.ps1
```

To verify only the ROS Gateway without starting the UI:

```powershell
.\scripts\start_px4_ros2_dev.ps1 -GatewayOnly
```

## Runtime Shape

```text
Windows ground station
  -> Agent UI / memory / orchestration
  -> px4_ros2 backend over HTTP

WSL or onboard companion computer
  -> airsim_agent_ros gateway_node
  -> ROS2 graph
  -> px4_msgs topics
  -> third-party ROS algorithms

Future distributed Agent mode
  Windows ground station
    -> Edge Task Protocol
      -> edge_agent on WSL/Jetson
        -> local gateway_node over http://127.0.0.1:8766
        -> local ROS2 capability adapters

PX4
  -> uXRCE-DDS client
  -> Micro-XRCE-DDS-Agent
```

Do not connect Python application code directly to the Micro XRCE-DDS UDP port.
That port is the PX4 client-to-agent transport. Application code should use the
ROS2 graph exposed after Micro XRCE-DDS is running.

## What Is Implemented

Windows side:

- `px4_ros2` backend in `src.agent.backends`.
- `RosGatewayController`, implementing the existing `FlightController` API.
- Existing atomic tools such as `drone_connect`, `drone_arm`,
  `drone_takeoff`, `drone_fly_to`, `drone_move_relative`, `drone_hover`, and
  `drone_land` can now execute through ROS2.
- Provider tools still exist for diagnostics and algorithm integration:
  `provider_bridge_health`, `provider_obstacle_summary`,
  `provider_validate_motion`.

ROS side:

- ROS2 package: `ros2/airsim_agent_ros`.
- Node executable: `gateway_node`.
- Publishes PX4 control topics:
  - `/fmu/in/offboard_control_mode`
  - `/fmu/in/trajectory_setpoint`
  - `/fmu/in/vehicle_command`
- Subscribes PX4 telemetry topics:
  - `/fmu/out/vehicle_status`
  - `/fmu/out/vehicle_local_position`
  - `/fmu/out/vehicle_attitude`
- Exposes HTTP provider endpoints on port `8766` by default. The UI commonly
  uses `8765`, so the gateway intentionally uses a separate port.
- Optional LaserScan obstacle adapter for quick obstacle package integration.
- The gateway remains the deterministic PX4 control boundary when an edge Agent
  is added. The edge Agent should call the local gateway for flight commands;
  it should not publish `/fmu/in/*` directly from model output.
- PX4 `/fmu/*` subscriptions and publications use PX4-compatible best-effort
  QoS. A ROS warning about incompatible `RELIABILITY` means the gateway process
  is stale and should be restarted after rebuilding.

## WSL Setup

Your PX4 log already shows that `uxrce_dds_client` is connected to
Micro-XRCE-DDS-Agent on UDP port `8888`. On the ROS side, first verify PX4
topics are visible:

```bash
ros2 topic list | grep /fmu/out
ros2 topic echo /fmu/out/vehicle_local_position --once
ros2 topic echo /fmu/out/vehicle_status --once
```

Build and run the gateway from WSL:

```bash
cd /mnt/c/Users/26494/Desktop/airsim_agent
bash scripts/start_ros_gateway_wsl.sh
```

The script defaults to your ROS workspace at `$HOME/ws_px4`, symlinks this
repository's ROS package into `$HOME/ws_px4/src`, builds only the gateway when
`px4_msgs` is already installed there, then sources `$HOME/ws_px4/install/setup.bash`
and serves the gateway on `8766`. It expects `ros2`, `colcon`, and `px4_msgs` to
be available. If `px4_msgs` is not installed in your ROS environment, clone a
version compatible with your PX4-Autopilot build and pass it through
`PX4_MSGS_SRC`:

```bash
cd ~
git clone https://github.com/PX4/px4_msgs.git
PX4_MSGS_SRC=$HOME/px4_msgs \
bash /mnt/c/Users/26494/Desktop/airsim_agent/scripts/start_ros_gateway_wsl.sh
```

If your ROS setup path differs:

```bash
ROS_SETUP=/opt/ros/jazzy/setup.bash \
GATEWAY_WS=$HOME/ws_px4 \
REPO_ROOT=/mnt/c/Users/26494/Desktop/airsim_agent \
bash scripts/start_ros_gateway_wsl.sh
```

Run with ROS parameters, for example enabling a LaserScan obstacle adapter:

```bash
bash scripts/start_ros_gateway_wsl.sh --ros-args \
  -p obstacle_scan_topic:=/scan \
  -p obstacle_front_angle_deg:=60.0 \
  -p obstacle_safety_margin_m:=1.0
```

## Windows Ground Station

Because your WSL and Windows use mirrored networking, localhost should work:

```powershell
$env:AIRSIM_AGENT_BACKEND = "px4_ros2"
$env:AIRSIM_AGENT_ROS_BRIDGE_URL = "http://127.0.0.1:8766"
python -m src.ui.server --backend px4_ros2
```

Smoke-test the gateway without moving the vehicle:

```powershell
python scripts/ros_gateway_smoke.py --url http://127.0.0.1:8766
```

Then the existing Agent tools use ROS2:

```text
drone_connect
drone_get_status
drone_arm
drone_takeoff
drone_fly_to
drone_move_relative
drone_hover
drone_land
```

## HTTP API

Health:

```http
GET /health
GET /providers
GET /providers/px4/status
```

PX4 control:

```http
POST /providers/px4/arm
POST /providers/px4/disarm
POST /providers/px4/takeoff
POST /providers/px4/land
POST /providers/px4/hold
POST /providers/px4/set_mode
POST /providers/px4/setpoint/local_ned
POST /providers/px4/move_relative
POST /providers/px4/velocity
POST /providers/px4/path
POST /providers/px4/rotate_to
```

Obstacle provider:

```http
POST /providers/obstacle/summary
POST /providers/obstacle/validate_motion
```

Example local setpoint:

```json
{
  "x": 10.0,
  "y": 0.0,
  "z": -5.0,
  "velocity": 2.0,
  "wait": true
}
```

Example obstacle validation:

```json
{
  "motion": {
    "forward_m": 2.0,
    "right_m": 0.0,
    "up_m": 0.0,
    "velocity": 1.0
  },
  "max_age_sec": 1.0
}
```

## Adding Third-Party ROS Packages

A third-party ROS package should stay in ROS. The gateway should only adapt its
outputs into stable provider surfaces.

Obstacle package:

```text
third-party ROS node publishes /scan or /local_costmap
  -> gateway adapter summarizes nearest obstacle
  -> provider_validate_motion
  -> Agent blocks or allows drone_move_relative
```

Planner package:

```text
third-party planner service/action
  -> PathPlanner adapter
  -> skill/provider returns waypoint proposal
  -> Agent executes atomic drone_fly_path or drone_fly_to steps
```

Detection package:

```text
third-party detector publishes vision_msgs detections
  -> DetectionProvider adapter
  -> search/track skills use semantic detections
```

The platform goal is that new ROS algorithms require an adapter, not Agent loop
changes.

## Safety Notes

- PX4 offboard requires continuous setpoint streaming. The gateway streams
  `OffboardControlMode` and `TrajectorySetpoint` at `setpoint_hz`.
- The ground station should remain the operator interface and approval layer.
- High-rate perception, obstacle avoidance, and control loops should run in WSL
  or onboard, close to ROS/PX4.
- For real vehicles, set the backend profile to require operator approval before
  high-risk commands.
