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
    airsim_port: int = 41452

    # Flight defaults.
    default_takeoff_altitude: float = 3.0
    max_velocity: float = 5.0
    arrival_threshold_m: float = 0.8
    arrival_timeout_s: float = 30.0
    offboard_setpoint_hz: float = 15.0
    heartbeat_hz: float = 2.0

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
    perception_target_class: str = "car"
    perception_confidence: float = 0.25
    perception_update_fps: float = 5.0
    perception_health_timeout_sec: float = 3.0
    perception_rtsp_url: str = ""               # frame_source=rtsp 时的流地址


config = DroneConfig()
