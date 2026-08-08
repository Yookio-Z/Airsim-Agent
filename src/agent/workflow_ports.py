"""Provider ports for advanced UAV workflows.

These protocols define the integration boundary for ROS and real vehicles. ROS
topics, services, and actions should adapt into these ports before skills use
them. The Agent Loop should choose skills, not raw ROS topic operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    status: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "data": dict(self.data),
        }


class ImageSource(Protocol):
    def capture(self, params: dict[str, Any]) -> ProviderResult:
        """Capture one image frame."""


class DetectionSource(Protocol):
    def detect(self, params: dict[str, Any]) -> ProviderResult:
        """Run bounded object detection over the current frame or image."""


class DepthSource(Protocol):
    def depth(self, params: dict[str, Any]) -> ProviderResult:
        """Read depth data or a point-cloud-derived distance summary."""


class PathPlanner(Protocol):
    def plan_path(self, params: dict[str, Any]) -> ProviderResult:
        """Return a bounded path or mission draft."""


class ObstacleProvider(Protocol):
    def obstacle_summary(self, params: dict[str, Any]) -> ProviderResult:
        """Return the current local obstacle or costmap summary."""

    def validate_motion(self, params: dict[str, Any]) -> ProviderResult:
        """Validate whether a proposed local motion is currently safe."""


class TrackingProvider(Protocol):
    def start_tracking(self, params: dict[str, Any]) -> ProviderResult:
        """Start a bounded tracking session."""

    def status(self, task_id: str) -> ProviderResult:
        """Read tracking status."""

    def cancel(self, task_id: str) -> ProviderResult:
        """Cancel tracking."""
