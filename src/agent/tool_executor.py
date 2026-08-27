"""Local execution facade over the existing MCP tool registrations.

相机与快照拆至 tool_runtime_camera/tool_runtime_snapshot；本文件保留 ToolRuntime 组合类（执行/校验/连接管理/编队）与全部符号 re-export。"""

from __future__ import annotations

import inspect
import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .backends import BackendProfile, BackendRegistry, create_builtin_backend_registry
from .llm_protocol import validate_json_schema
from src.modules.formation import FLIGHT_ACTIONS, FormationController
from src.modules.safety_validator import FlightConstraint, SafetyValidator
from src.tools.manifest import manifest_metadata, list_tool_manifest



# Output shape checks for the highest-value tools. Schemas carry no `required`
# fields on purpose: present fields are type-checked, missing fields are left to
# the normalizers, so validation acts as a diagnostic net rather than a gate.
TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "drone_get_status": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "connected": {"type": "boolean"},
            "flying": {"type": "boolean"},
            "armed": {"type": "boolean"},
            "has_collided": {"type": "boolean"},
            "position_ned": {"type": "object"},
            "velocity_ned": {"type": "object"},
            "attitude_rad": {"type": "object"},
        },
    },
    "airsim_vlm_confirm_target": {
        "type": "object",
        "properties": {
            "target_found": {"type": "boolean"},
            "confidence": {"type": "number"},
            "status": {"type": "string"},
            "recommended_next_action": {"type": "string"},
            "summary_zh": {"type": "string"},
            "target_label": {"type": "string"},
        },
    },
    "airsim_vlm_analyze_image": {
        "type": "object",
        "properties": {
            "summary_zh": {"type": "string"},
            "message": {"type": "string"},
            "navigation_hint": {"type": "string"},
            "visible_objects": {"type": "array"},
            "target_candidates": {"type": "array"},
        },
    },
    "airsim_task_status": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "task_id": {"type": "string"},
            "terminal": {"type": "boolean"},
        },
    },
    "formation_command": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "mode": {"type": "string"},
            "stable": {"type": "boolean"},
            "drones": {"type": "array"},
            "progress": {"type": "object"},
        },
    },
}


@dataclass
class ToolCallResult:
    tool: str
    params: dict[str, Any]
    ok: bool
    data: dict[str, Any]
    started_at: float
    finished_at: float
    safety: dict[str, Any] | None = None
    terminal: bool = True
    task_id: str = ""
    # Structured failure classification: "" | BLOCKED | SAFETY_BLOCKED |
    # NOT_CONNECTED | LINK_STALE | TIMEOUT | CONNECTION | INVALID_PARAMS |
    # UNKNOWN_TOOL | INVALID_ASYNC_RESPONSE | CANCELLED | TOOL_ERROR |
    # RUNTIME_UNAVAILABLE | INVALID_TOOL_OUTPUT
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "params": self.params,
            "ok": self.ok,
            "data": self.data,
            "duration_ms": round((self.finished_at - self.started_at) * 1000, 1),
            "safety": self.safety,
            "terminal": self.terminal,
            "task_id": self.task_id,
            "error_code": self.error_code,
            "outcome": "succeeded" if self.ok and self.terminal else ("accepted" if self.ok else "failed"),
        }


@dataclass
class ToolSpec:
    name: str
    category: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class ToolCollector:
    """Small FastMCP-compatible collector used to reuse @mcp.tool functions."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., str]] = {}

    def tool(self):
        def decorator(fn: Callable[..., str]) -> Callable[..., str]:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _agent_subtask_tool_card() -> dict[str, Any]:
    from .tool_cards import ToolCard

    return ToolCard(
        name="agent_subtask",
        purpose="Delegate an open-ended interpretation subtask (multi-target confirmation, ambiguous goal analysis) to a bounded sub-agent that returns a structured report.",
        when_to_use="When the goal needs several rounds of focused analysis that would consume the parent loop's step budget, or when an independent check of an ambiguous requirement is useful.",
        inputs={
            "goal": "one-sentence focused subtask for the sub-agent",
            "constraints": "optional constraints (altitude limits, target list, no-fly hints)",
            "max_steps": "sub-agent step budget (default 6)",
            "model_id": "optional model id for the sub-agent",
        },
        outputs="structured report {status, summary, steps, findings}.",
        risk="low",
        kind="atomic",
        execution_mode="immediate",
    ).to_dict()


def _agent_memory_tool_cards() -> list[dict[str, Any]]:
    """Agent-level memory tool cards appended to the regular tool set so the
    LLM can actively store and recall durable facts (agenticros-style)."""
    from .tool_cards import ToolCard

    return [
        ToolCard(
            name="memory_recall",
            purpose="Recall previously stored facts, missions, lessons, and run transcripts from long-term memory.",
            when_to_use="When the operator refers to an earlier task, fact, or lesson, or when past experience can inform the current decision.",
            inputs={"query": "natural language search text", "limit": "max results (default 5)"},
            outputs="matching memory records with relevance scores.",
            risk="low",
            kind="atomic",
        ).to_dict(),
        ToolCard(
            name="memory_remember",
            purpose="Store a durable fact about the mission or environment for future runs.",
            when_to_use="When the operator states a persistent fact (target area, vehicle id, learned preference) that future tasks should know.",
            inputs={"key": "short fact key", "value": "fact content", "tags": "optional comma-separated tags"},
            outputs="stored confirmation.",
            risk="low",
            kind="atomic",
        ).to_dict(),
    ]

from .tool_runtime_camera import ToolRuntimeCameraMixin
from .tool_runtime_snapshot import ToolRuntimeSnapshotMixin


class ToolRuntime(
    ToolRuntimeCameraMixin,
    ToolRuntimeSnapshotMixin,
):
    """Executes backend tools locally with safety validation."""

    READ_ONLY_TOOLS = {
        "drone_get_status",
        "drone_get_firmware_info",
        "drone_get_parameters",
        "drone_list_vehicles",
        "drone_download_mission",
        "drone_get_mission_progress",
        "airsim_take_photo",
        "airsim_get_sensors",
        "airsim_get_depth_map",
        "airsim_detect_objects",
        "airsim_vlm_confirm_target",
        "airsim_vlm_analyze_image",
        "provider_bridge_health",
        "provider_obstacle_summary",
        "provider_validate_motion",
    }

    CONTROL_TOOLS = {
        "drone_arm",
        "drone_disarm",
        "drone_takeoff",
        "drone_dispatch_takeoff",
        "drone_land",
        "drone_hover",
        "drone_fly_to",
        "drone_fly_velocity",
        "drone_move_relative",
        "drone_fly_path",
        "drone_dispatch_path",
        "drone_dispatch_land",
        "drone_dispatch_return_land",
        "drone_upload_mission",
        "drone_clear_mission",
        "drone_start_mission",
        "drone_rotate_to",
        "drone_set_mode",
    }

    # Idempotent read-only tools that may be retried once on a transient
    # TIMEOUT. Flight tools are deliberately excluded: a control call may have
    # partially executed before the timeout, and blind retries could double a
    # move; link-loss is handled by the reconnect path instead.
    _RETRYABLE_READ_TOOLS = READ_ONLY_TOOLS | {"airsim_task_status", "airsim_task_cancel"}

    CONNECTION_ERROR_MARKERS = (
        "not connected",
        "connection",
        "connect timed out",
        "timeout",
        "timed out",
        "rpc",
        "airsim",
        "winerror",
        "refused",
        "reset",
        "broken pipe",
        "\u8d85\u65f6",
        "\u8fde\u63a5",
        "\u672a\u8fde\u63a5",
        "\u62d2\u7edd",
    )
    CAMERA_SOURCE_TOOLS = {"airsim_take_photo", "airsim_get_depth_map"}

    def __init__(
        self,
        backend_id: str | None = None,
        backend_registry: BackendRegistry | None = None,
        camera_settings_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.backend_registry = backend_registry or create_builtin_backend_registry()
        self.backend_id = self.backend_registry.resolve_id(backend_id)
        self.backend_profile: BackendProfile | None = None
        self.controller: Any | None = None
        self.collector: ToolCollector | None = None
        self.camera_settings_provider = camera_settings_provider
        self.camera_controller: Any | None = None
        self.camera_collector: ToolCollector | None = None
        self.camera_key = ""
        self.camera_error = ""
        self._camera_lock = threading.RLock()
        self.available = False
        self.init_error = ""
        self._lock = threading.RLock()
        self._last_status_snapshot: dict[str, Any] = {}
        self._last_connect_params: dict[str, Any] = {}
        self._real_vehicle = False
        # Multi-vehicle formation controller (AirSim backend only), created
        # lazily the first time a formation command runs.
        self._formation: FormationController | None = None
        self._formation_stop_provider: Callable[[], bool] | None = None
        # External stop/cancel signal for blocking single-vehicle flight
        # commands (emergency stop / task cancel preemption).
        self._flight_stop_provider: Callable[[], bool] | None = None
        self.safety = SafetyValidator(
            FlightConstraint(
                max_altitude=50.0,
                min_altitude=0.5,
                max_velocity=8.0,
                max_distance_from_home=100.0,
            )
        )

    def ensure_ready(self) -> bool:
        if self.available and self.collector is not None:
            return True
        try:
            from src.tools.core import register_core_tools

            self.backend_profile = self.backend_registry.require(self.backend_id)
            capabilities = self.backend_profile.capabilities
            self.controller = self.backend_profile.create_controller()
            if hasattr(self.controller, "set_stop_provider"):
                self.controller.set_stop_provider(self._flight_stop_provider)
            self.collector = ToolCollector()

            def fmt(data: dict[str, Any]) -> str:
                return json.dumps(data, ensure_ascii=False, indent=2)

            register_core_tools(self.collector, self.controller, fmt)

            # Optional tool groups: failures must not block core tools.
            if capabilities.image_capture or capabilities.object_detection:
                try:
                    from src.tools.perception import register_perception_tools
                    register_perception_tools(self.collector, self.controller, fmt)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(f"perception tools skipped: {exc}")
            if capabilities.depth_perception:
                try:
                    from src.tools.vision import register_vision_tools
                    register_vision_tools(self.collector, self.controller, fmt)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(f"vision tools skipped: {exc}")
            if capabilities.ros2_topics:
                try:
                    from src.tools.providers import register_provider_tools

                    register_provider_tools(self.collector, self.controller, fmt)
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning(f"provider tools skipped: {exc}")

            self._ensure_formation_tools()

            self.available = True
            self.init_error = ""
            return True
        except Exception as e:
            self.available = False
            self.init_error = str(e)
            return False

    def _ensure_formation_tools(self) -> tuple[ToolCollector | None, str]:
        """Register the formation_command tool on formation-capable backends.

        Currently AirSim (SimpleFlight multirotor) and PX4 MAVLink (single link
        with multiple systems, via the duck-typed velocity-control protocol).
        The "at least 2 vehicles" requirement is enforced at call time
        (connections may not exist yet during registration).
        """
        if self.backend_id not in {"airsim", "px4_mavlink"} or self.controller is None:
            return None, "formation requires the airsim or px4_mavlink backend"
        external = getattr(self.controller, "_uses_external_px4_controller", None)
        if callable(external):
            try:
                if external(""):
                    return None, "airsim backend uses an external PX4 flight controller; formation unavailable"
            except Exception:
                pass
        collector = self.collector
        if collector is None:
            return None, "tool collector unavailable"
        if "formation_command" in collector.tools:
            return collector, ""

        @collector.tool()
        def formation_command(
            action: str = "status",
            formation_type: str = "line",
            spacing: float = 5.0,
            altitude: float = 10.0,
            x: float = 0.0,
            y: float = 0.0,
            z: float | None = None,
            angle_deg: float = 0.0,
            scale_factor: float = 1.0,
            area_shape: str = "rectangle",
            area_width: float = 100.0,
            area_height: float = 100.0,
            area_radius: float = 25.0,
            area_x: float = 0.0,
            area_y: float = 0.0,
            area_altitude: float = 10.0,
            resolution: float = 5.0,
            partition: str = "balanced",
            path_algo: str = "boustrophedon",
            coverage_speed: float = 3.0,
            vehicle_ids: str = "",
        ) -> str:
            """Multi-vehicle formation and coverage control (AirSim backend).

            The deterministic 10Hz control loop maintains the formation; this
            tool only issues high-level intents. Poll action=status until
            stable=true.
            """
            fc = self._formation_controller()
            if fc is None:
                return json.dumps(
                    {"status": "error", "message": "formation controller unavailable for this backend"},
                    ensure_ascii=False,
                )
            if action in {"takeoff", "coverage_start"} and len(fc._list_vehicles()) < 2:
                return json.dumps(
                    {
                        "status": "error",
                        "message": "formation requires at least 2 vehicles on the airsim or px4_mavlink backend",
                        "vehicles": fc._list_vehicles(),
                    },
                    ensure_ascii=False,
                )
            if action == "status":
                return json.dumps(fc.status(), ensure_ascii=False)
            if action == "set_drones":
                ids = [part.strip() for part in str(vehicle_ids or "").split(",") if part.strip()]
                return json.dumps(fc.set_drones(ids), ensure_ascii=False)
            if action == "set_formation":
                return json.dumps(fc.set_formation(formation_type, spacing), ensure_ascii=False)
            if action == "takeoff":
                return json.dumps(fc.takeoff(altitude), ensure_ascii=False)
            if action == "move_center":
                return json.dumps(fc.move_center(x, y, z), ensure_ascii=False)
            if action == "rotate":
                return json.dumps(fc.rotate(angle_deg), ensure_ascii=False)
            if action == "scale":
                return json.dumps(fc.scale(scale_factor), ensure_ascii=False)
            if action == "coverage_plan":
                area: dict[str, Any] = {"shape": area_shape, "altitude": area_altitude}
                if area_shape == "circle":
                    area.update({"radius": area_radius, "x": area_x, "y": area_y})
                else:
                    area.update({"width": area_width, "height": area_height, "x": area_x, "y": area_y})
                return json.dumps(fc.coverage_plan(area, resolution, partition, path_algo, coverage_speed), ensure_ascii=False)
            if action == "coverage_start":
                return json.dumps(fc.coverage_start(), ensure_ascii=False)
            if action == "hover_all":
                return json.dumps(fc.hover_all(), ensure_ascii=False)
            if action == "land_all":
                return json.dumps(fc.land_all(), ensure_ascii=False)
            if action == "stop":
                fc.shutdown("operator_stop")
                return json.dumps(fc.status(), ensure_ascii=False)
            return json.dumps(
                {
                    "status": "error",
                    "message": f"unknown action: {action}",
                    "valid_actions": [
                        "set_drones", "set_formation", "takeoff", "move_center", "rotate", "scale",
                        "coverage_plan", "coverage_start", "hover_all", "land_all", "stop", "status",
                    ],
                },
                ensure_ascii=False,
            )

        return collector, ""

    def _formation_controller(self) -> FormationController | None:
        if self._formation is None and self.controller is not None:
            self._formation = FormationController(self.controller)
            self._formation.should_stop = self._formation_stop_provider
        return self._formation

    def formation_active(self) -> bool:
        """True while a formation/coverage control loop is commanding vehicles."""
        formation = getattr(self, "_formation", None)
        return bool(formation and formation.mode != "idle")

    def formation_shutdown(self, reason: str) -> bool:
        """Hover all formation drones and stop the control thread.

        Called on run end, backend switches, and emergency stop. Returns True
        when a mission was actually active.
        """
        formation = getattr(self, "_formation", None)
        if formation is None:
            return False
        return formation.shutdown(reason)

    def formation_set_stop_provider(self, provider: Callable[[], bool] | None) -> None:
        self._formation_stop_provider = provider
        if self._formation is not None:
            self._formation.should_stop = provider

    def set_flight_stop_provider(self, provider: Callable[[], bool] | None) -> None:
        """Wire an external stop/cancel signal into blocking flight commands.

        Emergency stop / task cancel must preempt an in-flight single-vehicle
        move (the blocking loops poll this provider and exit cleanly)."""
        self._flight_stop_provider = provider
        controller = self.controller
        if controller is not None and hasattr(controller, "set_stop_provider"):
            try:
                controller.set_stop_provider(provider)
            except Exception:
                pass

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        collector = self.collector
        backend_profile = self.backend_profile
        if not collector:
            return []
        specs = []
        for name, fn in sorted(collector.tools.items()):
            specs.append(self._spec_for(name, fn).__dict__)
        if self._camera_source_enabled():
            for camera_tool in sorted(self.CAMERA_SOURCE_TOOLS):
                if camera_tool not in collector.tools:
                    camera_spec = self._camera_tool_spec(camera_tool)
                    if camera_spec is not None:
                        specs.append(camera_spec.__dict__)
        specs.append(
            ToolSpec(
                name="memory_store",
                category="memory",
                description="Write the current mission summary into long-term memory.",
                parameters={"source": {"default": "mission", "annotation": "str"}},
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="memory_recall",
                category="memory",
                description="Recall previously stored facts, missions, lessons, and run transcripts from long-term memory.",
                parameters={
                    "query": {"default": None, "annotation": "str", "required": True},
                    "limit": {"default": 5, "annotation": "int"},
                },
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="memory_remember",
                category="memory",
                description="Store a durable fact about the mission or environment for future runs.",
                parameters={
                    "key": {"default": None, "annotation": "str", "required": True},
                    "value": {"default": None, "annotation": "str", "required": True},
                    "tags": {"default": None, "annotation": "str"},
                },
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="agent_subtask",
                category="agent",
                description="Delegate an open-ended interpretation subtask to a bounded sub-agent that returns a structured report.",
                parameters={
                    "goal": {"default": None, "annotation": "str", "required": True},
                    "constraints": {"default": None, "annotation": "str"},
                    "max_steps": {"default": 6, "annotation": "int"},
                    "model_id": {"default": None, "annotation": "str"},
                },
            ).__dict__
        )
        capabilities = self._camera_capabilities(
            backend_profile.capabilities.to_dict() if backend_profile else {}
        )
        if capabilities.get("image_capture"):
            specs.append(
                ToolSpec(
                    name="airsim_vlm_confirm_target",
                    category="perception",
                    description="Use the selected multimodal model to confirm whether the latest image contains the requested target.",
                    parameters={
                        "target_description": {"default": "", "annotation": "str"},
                        "source": {"default": "last_image", "annotation": "str"},
                        "image_base64": {"default": "", "annotation": "str"},
                    },
                ).__dict__
            )
            specs.append(
                ToolSpec(
                    name="airsim_vlm_analyze_image",
                    category="perception",
                    description="Use the selected multimodal model to describe the latest captured image.",
                    parameters={
                        "question": {"default": "", "annotation": "str"},
                        "source": {"default": "last_image", "annotation": "str"},
                        "image_base64": {"default": "", "annotation": "str"},
                    },
                ).__dict__
            )
        for spec in specs:
            if isinstance(spec, dict):
                spec["manifest"] = manifest_metadata(str(spec.get("name") or ""))
        return specs

    def list_tool_manifest(self) -> list[dict[str, Any]]:
        return list_tool_manifest()

    def list_tool_cards(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        collector = self.collector
        backend_profile = self.backend_profile
        if not collector or not backend_profile:
            return []
        available = set(collector.tools)
        available.add("memory_store")
        if "formation_command" in collector.tools:
            available.add("formation_command")
        capabilities = self._camera_capabilities(backend_profile.capabilities.to_dict())
        if self._camera_source_enabled():
            available.add("airsim_take_photo")
            available.add("airsim_get_depth_map")
        if capabilities.get("image_capture"):
            available.add("airsim_vlm_confirm_target")
            available.add("airsim_vlm_analyze_image")
        from .tool_cards import cards_for_capabilities

        cards = cards_for_capabilities(capabilities, available)
        cards.extend(_agent_memory_tool_cards())
        cards.append(_agent_subtask_tool_card())
        return cards

    def reset_connection(self) -> None:
        """Drop the current controller/tool registry so the next call starts fresh."""
        # stop the formation control loop first: it holds a reference to the
        # controller being dropped and must never keep commanding it
        self.formation_shutdown("reset_connection")
        self._formation = None
        controller = self.controller
        if controller is not None:
            # Generic disconnect: close MAVLink sockets or AirSim links and release session resources.
            try:
                controller.disconnect()
            except Exception:
                pass
            # AirSim also needs its RPC runtime reset to release stuck locks.
            if hasattr(controller, "_reset_rpc_runtime"):
                try:
                    controller._reset_rpc_runtime()
                except Exception:
                    pass
        self.controller = None
        self.collector = None
        self.available = False
        self._last_status_snapshot = {}

    def set_backend(self, backend_id: str | None) -> ToolCallResult:
        """Switch to another registered backend without restarting the process."""
        with self._lock:
            new_id = self.backend_registry.resolve_id(backend_id)
            if new_id == self.backend_id and self.backend_profile is not None:
                return ToolCallResult(
                    "set_backend",
                    {"backend": new_id},
                    True,
                    {"status": "ok", "message": f"already on {new_id}"},
                    time.time(),
                    time.time(),
                )
            self.reset_connection()
            self.backend_id = new_id
            self._last_connect_params = {}
            self._real_vehicle = False
            try:
                self.backend_profile = self.backend_registry.require(new_id)
            except Exception as e:
                return ToolCallResult(
                    "set_backend",
                    {"backend": new_id},
                    False,
                    {"status": "error", "message": str(e)},
                    time.time(),
                    time.time(),
                )
            return ToolCallResult(
                "set_backend",
                {"backend": new_id},
                True,
                {"status": "ok", "message": f"switched to {new_id}"},
                time.time(),
                time.time(),
            )

    def reconnect(
        self,
        ip: str | None = None,
        port: int | None = None,
        url: str = "",
        fallback_url: str = "",
        remote_host: str = "",
        remote_port: int = 0,
        real_vehicle: bool = False,
    ) -> ToolCallResult:
        with self._lock:
            self.reset_connection()
            profile = self.backend_profile or self.backend_registry.require(self.backend_id)
            params = dict(profile.default_connect_params)
            if self.backend_id in {"px4_mavlink", "px4_ros2"}:
                if url:
                    params["url"] = url
                    if fallback_url:
                        params["fallback_url"] = fallback_url
                    if remote_host:
                        params["remote_host"] = remote_host
                    if remote_port:
                        params["remote_port"] = int(remote_port)
                    params["real_vehicle"] = bool(real_vehicle)
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            else:
                if ip is not None or port is not None:
                    params.update({
                        "ip": ip or params.get("ip", "127.0.0.1"),
                        "port": int(port or params.get("port", 41452)),
                    })
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            self._last_connect_params = dict(params)
            self._real_vehicle = bool(params.get("real_vehicle", False))
            return self.execute(
                "drone_connect",
                params,
                dry_run=False,
                allow_reconnect=False,
            )

    def execute(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        blocked_by_supervisor: bool = False,
        allow_reconnect: bool = True,
    ) -> ToolCallResult:
        params = dict(params or {})
        started = time.time()

        if name == "memory_store":
            return ToolCallResult(
                name,
                params,
                True,
                {"status": "ok", "message": "memory handled by runtime"},
                started,
                time.time(),
            )

        if dry_run:
            try:
                safety = self.validate(name, params)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return ToolCallResult(
                    name,
                    params,
                    False,
                    {"status": "error", "message": f"invalid tool parameters: {exc}"},
                    started,
                    time.time(),
                    error_code="INVALID_PARAMS",
                )
            return ToolCallResult(
                name,
                params,
                safety.get("level") != "danger",
                {"status": "planned", "message": "dry run only"},
                started,
                time.time(),
                safety=safety,
            )

        if name in self.CAMERA_SOURCE_TOOLS and self.backend_id != "airsim":
            return self._execute_camera_tool(name, params, started)

        if blocked_by_supervisor and name not in {"drone_hover", "drone_land", "drone_get_status"}:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "blocked", "message": "supervisor emergency stop is active"},
                started,
                time.time(),
                error_code="BLOCKED",
            )

        # Formation conflict guard lives at the executor level so EVERY caller
        # (agent loop, skills, GCS panel) is covered: while the deterministic
        # formation/coverage loop commands vehicles, single-vehicle flight tools
        # must not fight it for the same drone. Hover/land/status/connect stay
        # available as safe recovery actions.
        if (
            self.formation_active()
            and name in self.CONTROL_TOOLS
            and name not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect", "airsim_task_cancel"}
        ):
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "blocked", "message": "a formation/coverage mission is active; use formation_command(action=hover_all) or land_all before single-vehicle control"},
                started,
                time.time(),
                error_code="BLOCKED",
            )

        if not self.ensure_ready() or self.collector is None:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": self.init_error or "tool runtime unavailable"},
                started,
                time.time(),
                error_code="RUNTIME_UNAVAILABLE",
            )

        self._lock.acquire()
        try:
            if (
                (name in self.CONTROL_TOOLS or name == "formation_command")
                and self.controller is not None
                and not bool(getattr(self.controller, "is_connected", False))
            ):
                result = ToolCallResult(
                    name,
                    params,
                    False,
                    {
                        "status": "error",
                        "message": "not connected",
                        "backend": getattr(self.controller, "backend_name", self.backend_id),
                    },
                    started,
                    time.time(),
                    error_code="NOT_CONNECTED",
                )
                if allow_reconnect:
                    return self._retry_after_reconnect(name, params, blocked_by_supervisor, None, result)
                return result

            if name in self.CONTROL_TOOLS and self.backend_id == "px4_mavlink" and self.controller is not None:
                try:
                    get_cached_status = getattr(self.controller, "get_cached_status", None)
                    status = get_cached_status() if callable(get_cached_status) else self.controller.get_status()
                    status_data = status.to_dict()
                except Exception as exc:
                    status_data = {"link_stale": True, "connection_error": str(exc)}
                if self._status_is_stale(status_data):
                    heartbeat_age = status_data.get("heartbeat_age_s")
                    age_text = f" (last heartbeat {heartbeat_age}s ago)" if heartbeat_age is not None else ""
                    result = ToolCallResult(
                        name,
                        params,
                        False,
                        {
                            "status": "error",
                            "message": f"PX4 MAVLink heartbeat is lost{age_text}; reconnect the flight controller before running {name}.",
                            "connection_error": "stale MAVLink heartbeat",
                            "heartbeat_age_s": heartbeat_age,
                        },
                        started,
                        time.time(),
                        error_code="LINK_STALE",
                    )
                    if allow_reconnect:
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, None, result)
                    return result

            try:
                safety = self.validate(name, params)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return ToolCallResult(
                    name,
                    params,
                    False,
                    {"status": "error", "message": f"invalid tool parameters: {exc}"},
                    started,
                    time.time(),
                    error_code="INVALID_PARAMS",
                )
            if safety.get("level") == "danger" and not safety.get("corrected_params"):
                return ToolCallResult(
                    name, params, False,
                    {"status": "blocked", "message": "flight command blocked by safety layer", "violations": safety.get("violations", [])},
                    started, time.time(), safety=safety, error_code="SAFETY_BLOCKED",
                )
            if safety.get("corrected_params"):
                params.update(safety["corrected_params"])

            fn = self.collector.tools.get(name)
            if not fn:
                return ToolCallResult(
                    name, params, False,
                    {"status": "error", "message": f"unknown tool: {name}"},
                    started, time.time(), safety=safety, error_code="UNKNOWN_TOOL",
                )

            # Bounded retry for transient timeouts on idempotent read-only tools.
            # Flight-control tools are never retried here (a move may have
            # partially executed; the reconnect path handles link loss instead).
            max_attempts = 2 if name in self._RETRYABLE_READ_TOOLS else 1
            attempts = 0
            while True:
                attempts += 1
                try:
                    raw = fn(**params)
                    data = json.loads(raw) if isinstance(raw, str) else {"status": "ok", "result": raw}
                    status = str(data.get("status", "ok")).strip().lower()
                    ok = status not in {"error", "blocked", "failed", "cancelled", "canceled"}
                    if name == "drone_connect" and data.get("connected") is False:
                        ok = False
                    if name == "drone_connect" and self.backend_id == "px4_mavlink" and ok:
                        self._real_vehicle = bool(
                            data.get("real_vehicle", self._real_vehicle)
                            or str(data.get("url") or "").startswith("serial:")
                        )
                        self._last_connect_params["real_vehicle"] = self._real_vehicle
                    task_id = str(data.get("task_id") or "")
                    terminal = status not in {"accepted", "started", "pending", "queued", "running", "in_progress"}
                    async_invalid = False
                    if not terminal and not task_id:
                        ok = False
                        terminal = True
                        async_invalid = True
                        data = {
                            **data,
                            "status": "error",
                            "message": "async tool returned a non-terminal status without task_id",
                        }
                    error_code = "INVALID_ASYNC_RESPONSE" if async_invalid else self._error_code_for(name, data, ok)
                    result = ToolCallResult(
                        name,
                        params,
                        ok,
                        data,
                        started,
                        time.time(),
                        safety=safety,
                        terminal=terminal,
                        task_id=task_id,
                        error_code=error_code,
                    )
                    if allow_reconnect and self._should_retry_after_reconnect(name, result):
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
                    if attempts < max_attempts and error_code == "TIMEOUT":
                        time.sleep(0.4 * attempts)
                        continue
                    if name in TOOL_OUTPUT_SCHEMAS:
                        violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS[name])
                        if violations:
                            result.ok = False
                            result.error_code = "INVALID_TOOL_OUTPUT"
                            result.data = {**data, "validation_errors": violations}
                    return result
                except Exception as e:
                    message = str(e)
                    error_code = self._classify_exception(name, message)
                    result = ToolCallResult(
                        name, params, False,
                        {"status": "error", "message": message},
                        started, time.time(), safety=safety, error_code=error_code,
                    )
                    if allow_reconnect and self._should_retry_after_reconnect(name, result):
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
                    if attempts < max_attempts and error_code == "TIMEOUT":
                        time.sleep(0.4 * attempts)
                        continue
                    return result
        finally:
            self._lock.release()

    def validate(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        corrected: dict[str, Any] = {}
        violations: list[str] = []
        level = "safe"

        def merge(result) -> None:
            nonlocal level
            if result.violations:
                violations.extend(result.violations)
            if result.level == "danger":
                level = "danger"
            elif result.level == "warning" and level != "danger":
                level = "warning"

        if name == "drone_takeoff":
            altitude = abs(float(params.get("altitude", 3.0)))
            result = self.safety.validate_position(0.0, 0.0, -altitude)
            merge(result)
            if result.corrected and "z" in result.corrected:
                corrected["altitude"] = abs(float(result.corrected["z"]))

        elif name == "drone_dispatch_takeoff":
            altitude = abs(float(params.get("altitude", 3.0)))
            result = self.safety.validate_position(0.0, 0.0, -altitude)
            merge(result)
            if result.corrected and "z" in result.corrected:
                corrected["altitude"] = abs(float(result.corrected["z"]))

        elif name == "drone_fly_to":
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))
            z = float(params.get("z", -3.0))
            result = self.safety.validate_position(x, y, z)
            merge(result)
            if result.corrected:
                for key in ("x", "y", "z"):
                    if key in result.corrected:
                        corrected[key] = result.corrected[key]
            velocity = float(params.get("velocity", 2.0))
            vel = self.safety.validate_velocity(velocity, 0.0, 0.0)
            merge(vel)
            if vel.corrected:
                corrected["velocity"] = abs(float(vel.corrected["vx"]))

        elif name == "drone_fly_velocity":
            result = self.safety.validate_velocity(
                float(params.get("vx", 0.0)),
                float(params.get("vy", 0.0)),
                float(params.get("vz", 0.0)),
            )
            merge(result)
            if result.corrected:
                corrected.update(result.corrected)

        elif name == "drone_move_relative":
            forward_m = float(params.get("forward_m", 0.0))
            right_m = float(params.get("right_m", 0.0))
            up_m = float(params.get("up_m", 0.0))
            velocity = float(params.get("velocity", 2.0))
            if self.controller is not None and getattr(self.controller, "is_connected", False):
                status = self.controller.get_status()
                pos = status.position_ned or {"x": 0.0, "y": 0.0, "z": 0.0}
                heading_value = getattr(status, "extra", {}).get("heading_deg")
                try:
                    heading_deg = float(heading_value)
                except (TypeError, ValueError):
                    heading_deg = math.nan
                if not math.isfinite(heading_deg):
                    attitude = getattr(status, "attitude_rad", None)
                    yaw_value = attitude.get("yaw") if isinstance(attitude, dict) else None
                    try:
                        yaw = float(yaw_value)
                    except (TypeError, ValueError):
                        yaw = 0.0
                    heading_deg = math.degrees(yaw) if math.isfinite(yaw) else 0.0
                heading_deg %= 360.0
                heading_rad = math.radians(heading_deg)
                x = float(pos.get("x", 0.0)) + math.cos(heading_rad) * forward_m + math.cos(heading_rad + math.pi / 2) * right_m
                y = float(pos.get("y", 0.0)) + math.sin(heading_rad) * forward_m + math.sin(heading_rad + math.pi / 2) * right_m
                z = float(pos.get("z", 0.0)) - up_m
                result = self.safety.validate_position(x, y, z)
                merge(result)
            else:
                violations.append("relative movement requires a connection and a current position readback")
                if level == "safe":
                    level = "warning"
            vel = self.safety.validate_velocity(velocity, 0.0, 0.0)
            merge(vel)
            if vel.corrected:
                corrected["velocity"] = abs(float(vel.corrected["vx"]))

        elif name == "drone_fly_path":
            try:
                waypoints = json.loads(str(params.get("waypoints_json", "[]")))
                changed = False
                safe_waypoints = []
                for wp in waypoints:
                    x = float(wp.get("x", 0.0))
                    y = float(wp.get("y", 0.0))
                    z = float(wp.get("z", -3.0))
                    result = self.safety.validate_position(x, y, z)
                    merge(result)
                    if result.corrected:
                        x = float(result.corrected.get("x", x))
                        y = float(result.corrected.get("y", y))
                        z = float(result.corrected.get("z", z))
                        changed = True
                    safe_waypoints.append({"x": x, "y": y, "z": z})
                if changed:
                    corrected["waypoints_json"] = json.dumps(safe_waypoints, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"waypoint JSON could not be parsed: {e}")

        elif name == "drone_dispatch_path":
            try:
                waypoints = json.loads(str(params.get("waypoints_json", "[]")))
                changed = False
                safe_waypoints = []
                for wp in waypoints:
                    x = float(wp.get("x", 0.0))
                    y = float(wp.get("y", 0.0))
                    z = float(wp.get("z", -3.0))
                    result = self.safety.validate_position(x, y, z)
                    merge(result)
                    if result.corrected:
                        x = float(result.corrected.get("x", x))
                        y = float(result.corrected.get("y", y))
                        z = float(result.corrected.get("z", z))
                        changed = True
                    safe_waypoints.append({"x": x, "y": y, "z": z})
                if changed:
                    corrected["waypoints_json"] = json.dumps(safe_waypoints, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"waypoint JSON could not be parsed: {e}")

        elif name == "drone_dispatch_return_land":
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))
            z = float(params.get("z", -3.0))
            result = self.safety.validate_position(x, y, z)
            merge(result)
            if result.corrected:
                for key in ("x", "y", "z"):
                    if key in result.corrected:
                        corrected[key] = result.corrected[key]

        elif name == "drone_upload_mission":
            try:
                payload = json.loads(str(params.get("waypoints_json", "[]")))
                if isinstance(payload, dict):
                    raw_items = payload.get("items") or payload.get("waypoints") or []
                else:
                    raw_items = payload
                if not isinstance(raw_items, list):
                    raise ValueError("mission items must be a list")
                changed = False
                safe_items = []
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    safe_item = dict(item)
                    if all(safe_item.get(axis) is not None for axis in ("x", "y", "z")):
                        x = float(safe_item.get("x", 0.0))
                        y = float(safe_item.get("y", 0.0))
                        z = float(safe_item.get("z", -3.0))
                        result = self.safety.validate_position(x, y, z)
                        merge(result)
                        if result.corrected:
                            x = float(result.corrected.get("x", x))
                            y = float(result.corrected.get("y", y))
                            z = float(result.corrected.get("z", z))
                            safe_item.update({"x": x, "y": y, "z": z, "alt_m": abs(z)})
                            changed = True
                    elif "alt_m" in safe_item:
                        altitude = abs(float(safe_item.get("alt_m", 3.0) or 3.0))
                        result = self.safety.validate_position(0.0, 0.0, -altitude)
                        merge(result)
                        if result.corrected and "z" in result.corrected:
                            safe_item["alt_m"] = abs(float(result.corrected["z"]))
                            changed = True
                    safe_items.append(safe_item)
                if changed:
                    if isinstance(payload, dict):
                        payload["items"] = safe_items
                        corrected["waypoints_json"] = json.dumps(payload, ensure_ascii=False)
                    else:
                        corrected["waypoints_json"] = json.dumps(safe_items, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"mission JSON could not be parsed: {e}")

        elif name == "formation_command":
            action = str(params.get("action") or "status")
            if action == "takeoff":
                altitude = abs(float(params.get("altitude", 10.0)))
                result = self.safety.validate_position(0.0, 0.0, -altitude)
                merge(result)
                if result.corrected and "z" in result.corrected:
                    corrected["altitude"] = abs(float(result.corrected["z"]))
            elif action == "move_center":
                x = float(params.get("x", 0.0))
                y = float(params.get("y", 0.0))
                z = float(params.get("z", -10.0)) if params.get("z") is not None else -10.0
                result = self.safety.validate_position(x, y, z)
                merge(result)
                if result.corrected:
                    for key in ("x", "y", "z"):
                        if key in result.corrected:
                            corrected[key] = result.corrected[key]
            elif action == "coverage_plan":
                shape = str(params.get("area_shape") or "rectangle")
                area_x = float(params.get("area_x", 0.0))
                area_y = float(params.get("area_y", 0.0))
                area_altitude = abs(float(params.get("area_altitude", 10.0)))
                if shape == "circle":
                    radius = abs(float(params.get("area_radius", 25.0)))
                    if radius > 500.0:
                        level = "danger"
                        violations.append(f"coverage area radius {radius:.0f}m exceeds 500m limit")
                    # geofence: the farthest points of the circle must stay inside
                    for dx, dy in ((radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius)):
                        result = self.safety.validate_position(area_x + dx, area_y + dy, -area_altitude)
                        merge(result)
                else:
                    width = abs(float(params.get("area_width", 100.0)))
                    height = abs(float(params.get("area_height", 100.0)))
                    if width > 500.0 or height > 500.0:
                        level = "danger"
                        violations.append(f"coverage area {width:.0f}x{height:.0f}m exceeds 500m limit")
                    # geofence: every corner of the rectangle must stay inside
                    half_w, half_h = width / 2.0, height / 2.0
                    for cx, cy in (
                        (area_x + half_w, area_y + half_h),
                        (area_x - half_w, area_y + half_h),
                        (area_x - half_w, area_y - half_h),
                        (area_x + half_w, area_y - half_h),
                    ):
                        result = self.safety.validate_position(cx, cy, -area_altitude)
                        merge(result)
                result = self.safety.validate_position(0.0, 0.0, -area_altitude)
                merge(result)
                if result.corrected and "z" in result.corrected:
                    corrected["area_altitude"] = abs(float(result.corrected["z"]))
            elif action == "coverage_start" and "coverage_speed" in params:
                speed = float(params.get("coverage_speed", 3.0))
                vel = self.safety.validate_velocity(speed, 0.0, 0.0)
                merge(vel)
                if vel.corrected and "vx" in vel.corrected:
                    corrected["coverage_speed"] = abs(float(vel.corrected["vx"]))

        return {
            "level": level,
            "violations": violations,
            "corrected_params": corrected,
            "constraints": {
                "max_altitude": self.safety.constraints.max_altitude,
                "min_altitude": self.safety.constraints.min_altitude,
                "max_velocity": self.safety.constraints.max_velocity,
                "geofence_radius": self.safety.constraints.max_distance_from_home,
            },
        }

    @staticmethod
    def _status_is_stale(drone_status: dict[str, Any] | None) -> bool:
        if not isinstance(drone_status, dict):
            return False
        return bool(drone_status.get("connection_error") or drone_status.get("link_stale"))

    def _should_retry_after_reconnect(self, name: str, result: ToolCallResult) -> bool:
        if result.ok or not self._requires_vehicle_connection(name):
            return False
        return self._is_connection_error(result.data)

    def _retry_after_reconnect(
        self,
        name: str,
        params: dict[str, Any],
        blocked_by_supervisor: bool,
        safety: dict[str, Any] | None,
        failed_result: ToolCallResult,
    ) -> ToolCallResult:
        reconnect = self.reconnect()
        if not reconnect.ok:
            failed_result.data["reconnect"] = reconnect.to_dict()
            return failed_result

        if name in self.CONTROL_TOOLS or name == "formation_command":
            # Safety: never blindly re-dispatch a flight-control command after a
            # link loss. The command may have partially executed before the
            # connection dropped, and re-sending could double a move. Return the
            # failure with a clear recovery hint instead.
            failed_result.data["auto_reconnect"] = {
                "attempted": True,
                "ok": reconnect.ok,
                "redispatched": False,
                "reason": "flight-control command not auto-redispatched after reconnect; operator must confirm state before re-issuing",
                "before_retry": failed_result.to_dict(),
            }
            return failed_result

        retry = self.execute(
            name,
            params,
            dry_run=False,
            blocked_by_supervisor=blocked_by_supervisor,
            allow_reconnect=False,
        )
        retry.safety = retry.safety or safety
        retry.data["auto_reconnect"] = {
            "attempted": True,
            "ok": reconnect.ok,
            "redispatched": True,
            "before_retry": failed_result.to_dict(),
        }
        return retry

    def _requires_vehicle_connection(self, name: str) -> bool:
        if name in {"memory_store", "drone_connect", "drone_disconnect"}:
            return False
        return name.startswith("drone_") or name.startswith("airsim_")

    def _is_connection_error(self, data: dict[str, Any]) -> bool:
        # 只扫错误语义字段。payload 里恒有的结构化字段（backend="AirSim"、
        # 载具名等）会让 "airsim" 这类标记误命中——例如后端不支持某工具时
        # 返回的 "not supported" 也带 backend 字段，曾被误判成连接错误，
        # 触发不必要的断开重连（UI 表现为 AirSim OFFLINE 一下又重连）。
        fields: list[str] = []
        for key in ("message", "error", "error_detail", "path_error"):
            value = data.get(key)
            if isinstance(value, str):
                fields.append(value)
            elif isinstance(value, dict):
                fields.append(json.dumps(value, ensure_ascii=False, default=str))
        text = " ".join(fields).lower()
        return any(marker in text for marker in self.CONNECTION_ERROR_MARKERS)

    @staticmethod
    def _error_code_for(name: str, data: dict[str, Any], ok: bool) -> str:
        """Classify a failed tool result into a structured error code."""
        if ok:
            return ""
        status = str(data.get("status") or "").strip().lower()
        message = str(data.get("message") or "").lower()
        if status == "blocked":
            return "BLOCKED"
        if status in {"cancelled", "canceled"}:
            return "CANCELLED"
        if "timeout" in message or "timed out" in message or "超时" in message:
            return "TIMEOUT"
        if any(marker in message for marker in ("not connected", "连接失败", "未连接")):
            return "NOT_CONNECTED"
        return "TOOL_ERROR"

    @staticmethod
    def _classify_exception(name: str, message: str) -> str:
        """Classify a raised exception from a tool call."""
        lowered = str(message or "").lower()
        if any(marker in lowered for marker in ("timeout", "timed out", "超时")):
            return "TIMEOUT"
        if any(marker in lowered for marker in ("connect", "connection", "refused", "reset", "broken pipe", "winerror", "未连接", "连接")):
            return "CONNECTION"
        return "TOOL_ERROR"

    def _spec_for(self, name: str, fn: Callable[..., str]) -> ToolSpec:
        doc = inspect.getdoc(fn) or ""
        first_line = doc.splitlines()[0] if doc else name
        params: dict[str, Any] = {}
        signature = inspect.signature(fn)
        for key, param in signature.parameters.items():
            if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            default = None if param.default is inspect._empty else param.default
            annotation = ""
            if param.annotation is not inspect._empty:
                annotation = getattr(param.annotation, "__name__", str(param.annotation))
            params[key] = {
                "default": default,
                "annotation": annotation,
                "required": param.default is inspect._empty,
            }
        return ToolSpec(
            name=name,
            category=self._category_for(name),
            description=first_line,
            parameters=params,
        )

    def _category_for(self, name: str) -> str:
        if name.startswith("drone_"):
            if "mission" in name:
                return "mission"
            if name in self.READ_ONLY_TOOLS:
                return "state"
            return "flight"
        if "photo" in name or "sensor" in name or "depth" in name or "detect" in name:
            return "perception"
        if "search" in name or "track" in name or "approach" in name or "task" in name:
            return "mission"
        return "tool"
