"""Perception axis profile: configuration for the perception capability.

The perception axis is orthogonal to the flight backend. A profile selects
the frame source and where the perception algorithms run (local module inside
the ground station process, or a remote Jetson HTTP service). Agent, tools,
and skills consume only the axis state -- they never care about details.

See docs/perception_axis_design.md for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PerceptionProfile:
    """One named perception configuration."""

    profile: str = "sim_local"
    frame_source: str = "airsim"     # airsim | rtsp | usb
    deploy: str = "local"            # local | remote
    remote_url: str = ""             # deploy=remote: http://<jetson_ip>:<port>
    # AirSim camera name to capture from. The simulator names cameras in
    # settings.json; ours used to hardcode "0" everywhere, which breaks the
    # moment a config names them (e.g. CameraImage/CameraDepth).
    camera_name: str = "0"
    # Horizontal FOV of that camera, in degrees. MUST match FOV_Degrees in the
    # simulator settings: the depth projection converts pixel offsets to angles
    # with it, and AirSim does not report the FOV over RPC, so a mismatch is
    # silent. Vertical FOV is derived from it using the image aspect ratio.
    fov_h: float = 90.0
    # Sample depth every Nth detection. Distance is a slow variable, and the
    # depth RPC shares the controller's RPC mutex with frame capture -- at
    # ~580 ms per sample, sampling too often starves capture and collapses the
    # effective detection rate. Measured: 1-in-5 left detect_fps at ~0.7 Hz
    # with ~46% of displayed boxes being extrapolated rather than measured.
    depth_sample_every: int = 15
    target_class: str = "car"
    confidence: float = 0.25
    update_fps: float = 5.0
    # YOLO inference rate, decoupled from capture. This used to be hardcoded at
    # 2.0 inside the engine with no way to change it, while capture ran at
    # update_fps -- so the detection snapshot could lag the picture by up to half
    # a second, which shows up as flicker. Measured inference is ~120 ms on CPU
    # for a 720p frame, so values above ~6 Hz buy little.
    detect_fps: float = 2.0
    # Inference resolution. 0 = detector default (1280, measured to give a
    # far larger confidence margin than 640 on live AirSim frames).
    imgsz: int = 0
    health_timeout_sec: float = 3.0
    # 检测模型选择：auto（COCO 类别用固定类模型）| world（YOLO-World 开放词表）| coco
    model: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "frame_source": self.frame_source,
            "deploy": self.deploy,
            "remote_url": self.remote_url,
            "camera_name": self.camera_name,
            "fov_h": self.fov_h,
            "depth_sample_every": self.depth_sample_every,
            "target_class": self.target_class,
            "confidence": self.confidence,
            "update_fps": self.update_fps,
            "detect_fps": self.detect_fps,
            "imgsz": self.imgsz,
            "health_timeout_sec": self.health_timeout_sec,
            "model": self.model,
        }

    @classmethod
    def from_config(cls, cfg: Any) -> "PerceptionProfile":
        """Build a profile from the runtime config, falling back to built-ins.

        Explicit config fields win over the named profile's defaults so a
        single override (e.g. only remote_url) does not require a new profile.
        """
        base = BUILTIN_PROFILES.get(str(getattr(cfg, "perception_profile", "sim_local")).lower())
        merged: dict[str, Any] = dict((base if base is not None else BUILTIN_PROFILES["sim_local"]).to_dict())
        if getattr(cfg, "perception_frame_source", ""):
            merged["frame_source"] = cfg.perception_frame_source
        if getattr(cfg, "perception_deploy", ""):
            merged["deploy"] = cfg.perception_deploy
        if getattr(cfg, "perception_remote_url", ""):
            merged["remote_url"] = cfg.perception_remote_url
        if getattr(cfg, "perception_depth_sample_every", None) is not None:
            merged["depth_sample_every"] = int(cfg.perception_depth_sample_every)
        if getattr(cfg, "perception_fov_h", None) is not None:
            merged["fov_h"] = float(cfg.perception_fov_h)
        if getattr(cfg, "perception_camera_name", ""):
            merged["camera_name"] = str(cfg.perception_camera_name)
        if getattr(cfg, "perception_target_class", ""):
            merged["target_class"] = cfg.perception_target_class
        if getattr(cfg, "perception_confidence", None) is not None:
            merged["confidence"] = float(cfg.perception_confidence)
        if getattr(cfg, "perception_update_fps", None) is not None:
            merged["update_fps"] = float(cfg.perception_update_fps)
        if getattr(cfg, "perception_detect_fps", None) is not None:
            merged["detect_fps"] = float(cfg.perception_detect_fps)
        if getattr(cfg, "perception_imgsz", None) is not None:
            merged["imgsz"] = int(cfg.perception_imgsz)
        if getattr(cfg, "perception_health_timeout_sec", None) is not None:
            merged["health_timeout_sec"] = float(cfg.perception_health_timeout_sec)
        if getattr(cfg, "perception_model", ""):
            merged["model"] = str(cfg.perception_model)
        merged["profile"] = str(getattr(cfg, "perception_profile", "sim_local"))
        return PerceptionProfile(**merged)


BUILTIN_PROFILES: dict[str, PerceptionProfile] = {
    # Sim: AirSim camera + depth, detection inside the ground station process.
    "sim_local": PerceptionProfile(
        profile="sim_local",
        frame_source="airsim",
        deploy="local",
        target_class="car",
        confidence=0.25,
        update_fps=5.0,
    ),
    # Real-machine form: perception runs on the Jetson, exposed over HTTP.
    "jetson_remote": PerceptionProfile(
        profile="jetson_remote",
        frame_source="none",
        deploy="remote",
        target_class="car",
        confidence=0.25,
        update_fps=5.0,
    ),
    # Real-machine form: gimbal pod video returns to the ground station via
    # RTSP; detection runs locally on that stream.
    "rtsp_local": PerceptionProfile(
        profile="rtsp_local",
        frame_source="rtsp",
        deploy="local",
        target_class="car",
        confidence=0.25,
        update_fps=5.0,
    ),
}


def resolve_profile(cfg: Any) -> PerceptionProfile:
    """Resolve the active profile; disabled when cfg.perception_enabled is false."""
    return PerceptionProfile.from_config(cfg) if getattr(cfg, "perception_enabled", False) else None