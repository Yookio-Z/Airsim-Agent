"""Perception service contract.

``docs/perception_axis_design.md`` §4 specifies a ``PerceptionService``
protocol as the boundary between the Agent/UI and whatever is doing the
detection -- the local engine in this process, or a service running on the
onboard computer. The protocol is declared here so the contract is explicit and
testable, and so a second implementation (a ROS image-topic bridge, a remote
Jetson service) knows exactly what surface to provide.

The Agent side only ever needs read-only state: health, a snapshot dict, and
consumable events. Nothing here exposes pixels, the detector, or the frame
source -- consumers that need an image use ``annotated_frame`` on the concrete
axis, which is a presentation concern rather than part of this contract.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PerceptionService(Protocol):
    """Read-only state surface for a running perception capability."""

    @property
    def enabled(self) -> bool:
        """True when a profile is configured (the service may still be offline)."""

    def is_online(self) -> bool:
        """True when fresh detections are arriving within the health window."""

    def health(self) -> dict[str, Any]:
        """Liveness/diagnostics: online flag, fps, source, error, last update."""

    def snapshot(self) -> dict[str, Any]:
        """Latest detection state.

        Required keys:

        - ``targets``: list of detections, each with ``class``, ``confidence``,
          ``bbox``, ``track_id``, and -- when 3D localisation is available --
          ``world_pos`` / ``depth_m`` / ``distance``.
        - ``primary``: the single most relevant target, or None.
        - ``frame_width`` / ``frame_height``: pixel size of the frame the
          bboxes refer to, so consumers can normalise without decoding an image.
        - ``total_frames``, ``fps``, ``timestamp``, ``source``.
        """

    def pop_events(self) -> list[dict[str, Any]]:
        """Return and clear queued perception events (consumed, not broadcast)."""

    def start(self) -> bool:
        """Start the service; returns False when it could not come up."""

    def stop(self) -> None:
        """Stop the service; safe to call repeatedly."""
