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
    target_class: str = "car"
    confidence: float = 0.25
    update_fps: float = 5.0
    health_timeout_sec: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "frame_source": self.frame_source,
            "deploy": self.deploy,
            "remote_url": self.remote_url,
            "target_class": self.target_class,
            "confidence": self.confidence,
            "update_fps": self.update_fps,
            "health_timeout_sec": self.health_timeout_sec,
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
        if getattr(cfg, "perception_target_class", ""):
            merged["target_class"] = cfg.perception_target_class
        if getattr(cfg, "perception_confidence", None) is not None:
            merged["confidence"] = float(cfg.perception_confidence)
        if getattr(cfg, "perception_update_fps", None) is not None:
            merged["update_fps"] = float(cfg.perception_update_fps)
        if getattr(cfg, "perception_health_timeout_sec", None) is not None:
            merged["health_timeout_sec"] = float(cfg.perception_health_timeout_sec)
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