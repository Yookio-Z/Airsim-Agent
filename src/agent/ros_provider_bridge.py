"""HTTP client for ROS-backed provider services.

The Windows Agent runtime should not import rclpy or subscribe to DDS topics
directly. ROS nodes can run in WSL and expose a small local provider bridge;
this client turns those HTTP responses into provider results for skills and
atomic provider tools.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.agent.workflow_ports import ProviderResult


@dataclass(frozen=True)
class RosProviderBridgeConfig:
    base_url: str = ""
    timeout_sec: float = 2.0

    @classmethod
    def from_env(cls) -> "RosProviderBridgeConfig":
        try:
            from src.config import config
        except Exception:  # pragma: no cover - defensive during early imports
            config = None  # type: ignore[assignment]

        base_url = (
            os.environ.get("AIRSIM_AGENT_ROS_BRIDGE_URL")
            or os.environ.get("DRONE_ROS_BRIDGE_URL")
            or (getattr(config, "ros_bridge_url", "") if config is not None else "")
            or ""
        ).strip()
        timeout_raw = (
            os.environ.get("AIRSIM_AGENT_ROS_BRIDGE_TIMEOUT_SEC")
            or os.environ.get("DRONE_ROS_BRIDGE_TIMEOUT_SEC")
            or (str(getattr(config, "ros_bridge_timeout_sec", "")) if config is not None else "")
            or "2.0"
        )
        try:
            timeout_sec = max(0.2, float(timeout_raw))
        except ValueError:
            timeout_sec = 2.0
        return cls(base_url=base_url, timeout_sec=timeout_sec)


class RosProviderBridgeClient:
    """Small HTTP adapter implementing provider-style calls."""

    def __init__(self, config: RosProviderBridgeConfig | None = None) -> None:
        self.config = config or RosProviderBridgeConfig.from_env()

    @classmethod
    def from_env(cls) -> "RosProviderBridgeClient":
        return cls(RosProviderBridgeConfig.from_env())

    @property
    def enabled(self) -> bool:
        return bool(self.config.base_url)

    def health(self) -> ProviderResult:
        return self._request("GET", "/health")

    def providers(self) -> ProviderResult:
        return self._request("GET", "/providers")

    def px4_status(self) -> ProviderResult:
        return self._request("GET", "/providers/px4/status")

    def px4_arm(self, arm: bool = True) -> ProviderResult:
        endpoint = "/providers/px4/arm" if arm else "/providers/px4/disarm"
        return self._request("POST", endpoint, {})

    def px4_takeoff(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/takeoff",
            params,
            timeout_sec=self._blocking_timeout(params),
        )

    def px4_land(self, params: dict[str, Any] | None = None) -> ProviderResult:
        return self._request("POST", "/providers/px4/land", params or {})

    def px4_hold(self, params: dict[str, Any] | None = None) -> ProviderResult:
        return self._request("POST", "/providers/px4/hold", params or {})

    def px4_set_mode(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/px4/set_mode", params)

    def px4_local_setpoint(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/setpoint/local_ned",
            params,
            timeout_sec=self._blocking_timeout(params),
        )

    def px4_move_relative(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/move_relative",
            params,
            timeout_sec=self._blocking_timeout(params),
        )

    def px4_velocity(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/velocity",
            params,
            timeout_sec=self._blocking_timeout(params),
        )

    def px4_path(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/path",
            params,
            timeout_sec=self._blocking_timeout(params, default_sec=120.0),
        )

    def px4_rotate_to(self, params: dict[str, Any]) -> ProviderResult:
        return self._request(
            "POST",
            "/providers/px4/rotate_to",
            params,
            timeout_sec=self._blocking_timeout(params),
        )

    def px4_start_task(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/px4/task/start", params)

    def px4_task_status(self, task_id: str = "") -> ProviderResult:
        if task_id:
            query = urllib.parse.urlencode({"task_id": task_id})
            return self._request("GET", f"/providers/px4/task/status?{query}")
        return self._request("GET", "/providers/px4/task/status")

    def px4_cancel_task(self, task_id: str = "") -> ProviderResult:
        return self._request("POST", "/providers/px4/task/cancel", {"task_id": task_id} if task_id else {})

    def capture(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/image/capture", params)

    def detect(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/detection/detect", params)

    def depth(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/depth/query", params)

    def plan_path(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/path_planner/plan", params)

    def obstacle_summary(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/obstacle/summary", params)

    def validate_motion(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/obstacle/validate_motion", params)

    def safety_status(self) -> ProviderResult:
        return self._request("GET", "/providers/safety/status")

    def configure_no_fly_zones(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/safety/geofence", params)

    def start_tracking(self, params: dict[str, Any]) -> ProviderResult:
        return self._request("POST", "/providers/tracking/start", params)

    def status(self, task_id: str) -> ProviderResult:
        query = urllib.parse.urlencode({"task_id": task_id})
        return self._request("GET", f"/providers/tracking/status?{query}")

    def cancel(self, task_id: str) -> ProviderResult:
        return self._request("POST", "/providers/tracking/cancel", {"task_id": task_id})

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout_sec: float | None = None,
    ) -> ProviderResult:
        if not self.enabled:
            return ProviderResult(
                ok=False,
                status="not_configured",
                message="Set AIRSIM_AGENT_ROS_BRIDGE_URL or DRONE_ROS_BRIDGE_URL.",
            )

        url = self.config.base_url.rstrip("/") + endpoint
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            timeout = float(timeout_sec if timeout_sec is not None else self.config.timeout_sec)
            with urllib.request.urlopen(request, timeout=max(0.2, timeout)) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw.strip() else {}
                return self._coerce_result(parsed)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return ProviderResult(
                ok=False,
                status="http_error",
                message=f"ROS provider bridge HTTP {exc.code}: {detail}",
                data={"url": url},
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ProviderResult(
                ok=False,
                status="bridge_unavailable",
                message=str(exc),
                data={"url": url},
            )
        except json.JSONDecodeError as exc:
            return ProviderResult(
                ok=False,
                status="invalid_json",
                message=str(exc),
                data={"url": url},
            )

    def _coerce_result(self, parsed: Any) -> ProviderResult:
        if not isinstance(parsed, dict):
            return ProviderResult(ok=True, status="ok", data={"value": parsed})

        if "ok" in parsed or "status" in parsed:
            raw_data = parsed.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
            extras = {
                key: value
                for key, value in parsed.items()
                if key not in {"ok", "status", "message", "data"}
            }
            data.update(extras)
            return ProviderResult(
                ok=bool(parsed.get("ok", parsed.get("status") in {"ok", "ready", "safe"})),
                status=str(parsed.get("status") or ("ok" if parsed.get("ok") else "error")),
                message=str(parsed.get("message") or ""),
                data=data,
            )

        return ProviderResult(ok=True, status="ok", data=parsed)

    def _blocking_timeout(self, payload: dict[str, Any] | None, default_sec: float | None = None) -> float:
        """HTTP timeout for commands that intentionally wait for PX4 motion."""

        baseline = float(default_sec if default_sec is not None else self.config.timeout_sec)
        if isinstance(payload, dict):
            try:
                requested = float(payload.get("timeout_sec") or payload.get("timeout") or 0.0)
                if requested > 0:
                    baseline = max(baseline, requested + 5.0)
            except (TypeError, ValueError):
                pass
            try:
                duration = float(payload.get("duration_sec") or payload.get("duration") or 0.0)
                if duration > 0:
                    baseline = max(baseline, duration + 8.0)
            except (TypeError, ValueError):
                pass
        return max(float(self.config.timeout_sec), baseline)
