"""Project configuration using Pydantic settings.

Values are loaded from environment variables with the DRONE_ prefix and from a
local .env file when present.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DroneConfig(BaseSettings):
    """Global runtime configuration for simulation, PX4, and providers."""

    model_config = SettingsConfigDict(
        env_prefix="DRONE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PX4 / MAVLink.
    # PX4 SITL normally exposes the GCS MAVLink endpoint on UDP 14550.
    # For real vehicles, use a listener such as udpin:0.0.0.0:14550.
    px4_connection_string: str = "udp:127.0.0.1:14550"
    px4_source_system: int = 1
    outdoor_mode: bool = False

    # ROS provider bridge. The Windows runtime calls this local HTTP bridge
    # instead of importing rclpy or handling DDS discovery directly.
    ros_bridge_url: str = "http://127.0.0.1:8766"
    ros_bridge_timeout_sec: float = 2.0
    ros_workspace_path: str = "$HOME/ws_px4"

    # AirSim.
    airsim_ip: str = "127.0.0.1"
    # AirSim's own default. This was 41452, which silently broke the camera: the
    # running simulator listens on whatever ApiServerPort its settings.json says
    # (41451 by default), so a mismatch makes every capture fail with no obvious
    # error in the UI. Keep this equal to the live settings.json, or set
    # DRONE_AIRSIM_PORT explicitly.
    airsim_port: int = 41451

    # Flight defaults.
    default_takeoff_altitude: float = 3.0
    max_velocity: float = 5.0
    arrival_threshold_m: float = 0.8
    arrival_timeout_s: float = 30.0
    offboard_setpoint_hz: float = 15.0
    heartbeat_hz: float = 2.0

    # 偏航（机头朝向）策略。取值对齐两套主流飞控的既有语义，避免在这个项目里
    # 另造一套概念——仿真和真机的表现必须一致，否则虚实映射就失去意义。
    #   face_waypoint —— 飞向下一航点前先转正机头。等于 PX4 的
    #                    MPC_YAW_MODE=0（towards waypoint），也是 ArduPilot
    #                    多旋翼 WP_YAW_BEHAVIOR=2 的默认语义。两家的默认值
    #                    都是这一档，不是"固定机头"。
    #   hold          —— 保持当前机头不转。等于 PX4 的 MPC_YAW_MODE=5
    #                    （1.16 才加的植保机特例）/ ArduPilot
    #                    WP_YAW_BEHAVIOR=0。真机上机头物理固定、或需要人工
    #                    接管偏航时用这一档。
    #   course        —— 对齐瞬时航迹方向。等于 ArduPilot
    #                    WP_YAW_BEHAVIOR=3。**不是** PX4 航点任务的默认
    #                    语义：PX4 的 along_trajectory 在纯航点任务下留了
    #                    TODO 交给子类实现，所以仿真和真机会对不上。留这一档
    #                    仅为与 ArduPilot 对齐，默认不用。
    yaw_mode: str = "face_waypoint"
    # 转正判据：机头与目标方位夹角小于此值就不再转，避免在目标正上方来回抖。
    # PX4 的 MIS_YAW_ERR 默认 12°，这里与之对齐。
    yaw_tolerance_deg: float = 12.0
    # 自主模式下机头角速度上限。PX4 的 MPC_YAWRAUTO_MAX 是 45~60 deg/s，
    # 取 45 是保守侧。真机上限速很重要：机头突然甩动会改变视觉伺服和
    # 遥控器操作员的空间参照。
    yaw_max_rate_dps: float = 45.0

    # Flight safety envelope (SafetyValidator / FlightConstraint).
    # These were hard-coded in ToolRuntime.__init__, which meant the declared
    # limits could not be matched to a deployment (e.g. a long-range survey
    # still got the 100 m fence, and max_velocity here was ignored by the
    # validator). Raise geofence_m deliberately: every position command, path
    # waypoint, mission item and velocity instruction is checked against it.
    safety_max_altitude_m: float = 50.0
    safety_min_altitude_m: float = 0.5
    safety_max_velocity_mps: float = 8.0
    safety_geofence_m: float = 100.0

    # No-fly zones enforced by the safety validator: a JSON array of circles,
    # [{"x": 30.0, "y": 0.0, "radius": 15.0, "name": "..."}, ...] in NED metres
    # from the local origin. Until this existed, FlightConstraint.no_fly_zones
    # was always empty and the segment/circle intersection check in
    # SafetyValidator could never fire in a real flight.
    # Only circles are supported for now (the existing geometry is circular);
    # rectangles would need new intersection maths.
    safety_no_fly_zones_json: str = ""

    # Continuous flight-envelope watchdog thresholds.
    # The watchdog samples telemetry every second and, after 3 consecutive
    # breaches, aborts the run and lands: it exists for the case where the
    # vehicle drifts or the position estimate diverges WITHOUT any command
    # being issued (nothing else would notice).
    # Two profiles, because the two task families have very different envelopes:
    #   * close-range visual work (identify/track/inspect) flies at 2-3 m in a
    #     small area — an excursion means loss of control, so its ceiling is
    #     deliberately tight;
    #   * general flight uses the same limits as the safety validator, with a
    #     margin so the watchdog acts as a backstop behind the per-command gate
    #     rather than duplicating it (the watchdog measures horizontal drift
    #     from the takeoff point, not from the NED origin).
    close_range_envelope_altitude_m: float = 8.0
    close_range_envelope_horizontal_m: float = 70.0
    envelope_margin_ratio: float = 1.15

    # Search defaults.
    search_altitude: float = 15.0
    search_overlap: float = 0.3
    camera_hfov_deg: float = 80.0
    spiral_radius_step: float = 8.0
    spiral_max_radius: float = 50.0

    # Logging.
    log_level: str = "INFO"
    log_json_file: str = ""

    # Detection and tracking.
    yolo_model: str = "yolov8n.pt"
    yolo_confidence: float = 0.5
    tracking_max_velocity: float = 1.0

    # Perception axis (orthogonal to the flight backend).
    # When enabled, the perception service runs and exposes perception_status;
    # frame_source/deploy determine where detection runs (local module vs a
    # remote Jetson HTTP service). See docs/perception_axis_design.md.
    perception_enabled: bool = False
    perception_profile: str = "sim_local"       # sim_local | jetson_remote | rtsp_local
    perception_frame_source: str = "airsim"     # airsim | rtsp | usb
    perception_deploy: str = "local"            # local | remote
    perception_remote_url: str = ""             # deploy=remote 时: http://<ip>:<port>
    perception_camera_name: str = "0"            # AirSim camera name (settings.json key)
    perception_fov_h: float = 90.0              # must equal FOV_Degrees in settings.json
    perception_depth_sample_every: int = 15     # sample depth every Nth detection
    perception_target_class: str = "car"
    perception_confidence: float = 0.25
    perception_update_fps: float = 5.0
    perception_detect_fps: float = 2.0          # YOLO inference rate, decoupled from update_fps
    perception_imgsz: int = 0                   # 0 = detector default (1280); measured, see dormant/yolo_detection._resolve_imgsz
    perception_health_timeout_sec: float = 3.0
    perception_model: str = "auto"              # auto | world(YOLO-World) | coco
    perception_rtsp_url: str = ""               # frame_source=rtsp 时的流地址


config = DroneConfig()
