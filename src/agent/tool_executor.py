"""Local execution facade over the existing MCP tool registrations."""

from __future__ import annotations

import inspect
import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .backends import BackendProfile, BackendRegistry, create_builtin_backend_registry
from src.modules.safety_validator import FlightConstraint, SafetyValidator
from src.tools.manifest import manifest_metadata, list_tool_manifest


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


class ToolRuntime:
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
        "drone_land",
        "drone_hover",
        "drone_fly_to",
        "drone_fly_velocity",
        "drone_move_relative",
        "drone_fly_path",
        "drone_upload_mission",
        "drone_clear_mission",
        "drone_start_mission",
        "drone_rotate_to",
        "drone_set_mode",
    }

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

            self.available = True
            self.init_error = ""
            return True
        except Exception as e:
            self.available = False
            self.init_error = str(e)
            return False

    def _camera_settings(self) -> dict[str, Any]:
        if self.camera_settings_provider is None:
            return {}
        try:
            settings = self.camera_settings_provider() or {}
            return settings if isinstance(settings, dict) else {}
        except Exception as exc:
            self.camera_error = str(exc)
            return {}

    def _camera_source_enabled(self) -> bool:
        settings = self._camera_settings()
        return str(settings.get("source") or "").lower() in {"airsim", "rtsp", "local"}

    def _camera_capabilities(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        merged = dict(capabilities or {})
        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        if source == "airsim":
            merged["image_capture"] = True
            merged["depth_perception"] = True
            merged["camera_source"] = "airsim"
            merged["camera_host"] = settings.get("host", "127.0.0.1")
            merged["camera_port"] = settings.get("port", 41452)
            merged["image_capture_via"] = "airsim_camera_source"
        elif source == "rtsp":
            merged["image_capture"] = True
            merged["depth_perception"] = False
            merged["camera_source"] = "rtsp"
            merged["camera_url"] = settings.get("url", "")
            merged["image_capture_via"] = "rtsp_stream"
        elif source == "local":
            merged["image_capture"] = True
            merged["depth_perception"] = False
            merged["camera_source"] = "local"
            merged["camera_index"] = settings.get("camera_name", "0")
            merged["image_capture_via"] = "local_camera"
        return merged

    def _camera_tool_spec(self, name: str) -> ToolSpec | None:
        settings = self._camera_settings()
        if name == "airsim_take_photo":
            return ToolSpec(
                name="airsim_take_photo",
                category="perception",
                description="Capture a PNG frame from the configured AirSim camera source.",
                parameters={
                    "camera_name": {"default": settings.get("camera_name", "0"), "annotation": "str"},
                    "vehicle_name": {"default": settings.get("vehicle_name", ""), "annotation": "str"},
                    "image_type": {"default": settings.get("image_type", "scene"), "annotation": "str"},
                    "auto_save": {"default": settings.get("auto_save", False), "annotation": "bool"},
                    "timeout_sec": {"default": settings.get("timeout_sec", 30.0), "annotation": "float"},
                },
            )
        if name == "airsim_get_depth_map":
            return ToolSpec(
                name="airsim_get_depth_map",
                category="perception",
                description="Read a depth image from the configured AirSim camera source.",
                parameters={
                    "camera_name": {"default": settings.get("camera_name", "0"), "annotation": "str"},
                    "vehicle_name": {"default": settings.get("vehicle_name", ""), "annotation": "str"},
                    "return_vis": {"default": False, "annotation": "bool"},
                    "query_points": {"default": "", "annotation": "str"},
                },
            )
        return None

    def _ensure_camera_tools(self) -> tuple[ToolCollector | None, str]:
        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        if source == "rtsp":
            return self._ensure_rtsp_camera_tools(settings)
        if source == "local":
            return self._ensure_local_camera_tools(settings)
        if source != "airsim":
            return None, "camera source is not AirSim"

        host = str(settings.get("host") or "127.0.0.1")
        try:
            port = int(settings.get("port") or 41452)
        except (TypeError, ValueError):
            port = 41452
        key = f"airsim:{host}:{port}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.airsim_controller import AirSimController
            from src.tools.perception import register_perception_tools
            from src.tools.vision import register_vision_tools

            controller = AirSimController(ip=host, port=port)
            info = controller.connect(ip=host, port=port)
            if not info.connected:
                details = getattr(info, "details", {}) or {}
                message = details.get("message") if isinstance(details, dict) else ""
                self.camera_error = message or "AirSim camera source is not connected"
                return None, self.camera_error

            collector = ToolCollector()

            def fmt(data: dict[str, Any]) -> str:
                return json.dumps(data, ensure_ascii=False, indent=2)

            register_perception_tools(collector, controller, fmt)
            register_vision_tools(collector, controller, fmt)
            if "airsim_take_photo" not in collector.tools:
                self.camera_error = "AirSim camera tool registration failed"
                controller.disconnect()
                return None, self.camera_error

            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    def _ensure_rtsp_camera_tools(self, settings: dict[str, Any]) -> tuple[ToolCollector | None, str]:
        """Camera tool set for a real onboard camera pushed over RTSP."""
        url = str(settings.get("url") or settings.get("rtsp_url") or "").strip()
        if not url:
            self.camera_error = "rtsp camera source requires a stream URL"
            return None, self.camera_error
        key = f"rtsp:{url}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.rtsp_camera_controller import RtspCameraController

            controller = RtspCameraController(url)
            info = controller.connect()
            if not info.connected:
                self.camera_error = controller.last_error or "rtsp camera source is not connected"
                return None, self.camera_error

            collector = ToolCollector()
            self._register_rtsp_camera_tools(collector, controller)
            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    def _ensure_local_camera_tools(self, settings: dict[str, Any]) -> tuple[ToolCollector | None, str]:
        """Camera tool set for a local webcam / USB camera (pipeline testing)."""
        try:
            index = int(settings.get("camera_name") or 0)
        except (TypeError, ValueError):
            index = 0
        key = f"local:{index}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.rtsp_camera_controller import LocalCameraController

            controller = LocalCameraController(index)
            info = controller.connect()
            if not info.connected:
                self.camera_error = controller.last_error or "local camera is not available"
                return None, self.camera_error

            collector = ToolCollector()
            self._register_rtsp_camera_tools(collector, controller)
            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    @staticmethod
    def _register_rtsp_camera_tools(collector: ToolCollector, controller: Any) -> None:
        """Single photo tool for RTSP sources (real cameras: scene only)."""

        @collector.tool()
        def airsim_take_photo(
            camera_name: str = "0",
            image_type: str = "scene",
            vehicle_name: str = "",
            auto_save: bool = False,
            timeout_sec: float = 30.0,
        ) -> str:
            """Capture a JPEG frame from the RTSP camera source."""
            import base64

            raw = controller.capture_image(
                camera_name=camera_name,
                image_type=image_type,
                vehicle_name=vehicle_name,
                timeout=float(timeout_sec or 30.0),
            )
            if raw is None:
                return json.dumps(
                    {
                        "status": "error",
                        "backend": controller.backend_name,
                        "message": controller.last_error or "frame capture failed",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "ok",
                    "backend": controller.backend_name,
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                    "format": "jpeg",
                    "source": "rtsp",
                    "message": "frame captured from RTSP camera",
                },
                ensure_ascii=False,
            )

    def _camera_params(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        settings = self._camera_settings()
        merged = dict(params or {})
        if name == "airsim_take_photo":
            merged["camera_name"] = str(merged.get("camera_name") or settings.get("camera_name") or "0")
            merged["vehicle_name"] = str(merged.get("vehicle_name") or settings.get("vehicle_name") or "")
            merged["image_type"] = str(merged.get("image_type") or settings.get("image_type") or "scene")
            if "auto_save" not in merged:
                merged["auto_save"] = bool(settings.get("auto_save", False))
            if "timeout_sec" not in merged:
                merged["timeout_sec"] = float(settings.get("timeout_sec") or 30.0)
        elif name == "airsim_get_depth_map":
            merged["camera_name"] = str(merged.get("camera_name") or settings.get("camera_name") or "0")
            merged["vehicle_name"] = str(merged.get("vehicle_name") or settings.get("vehicle_name") or "")
        return merged

    def _execute_camera_tool(self, name: str, params: dict[str, Any], started: float) -> ToolCallResult:
        if name not in self.CAMERA_SOURCE_TOOLS:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": f"unknown camera tool: {name}"},
                started,
                time.time(),
            )

        collector, error = self._ensure_camera_tools()
        if collector is None:
            return ToolCallResult(
                name,
                params,
                False,
                {
                    "status": "error",
                    "message": f"AirSim camera source unavailable: {error or 'unknown error'}",
                    "camera_source": self._camera_settings(),
                },
                started,
                time.time(),
            )

        fn = collector.tools.get(name)
        if not fn:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": f"camera tool not registered: {name}"},
                started,
                time.time(),
            )

        safe_params = self._camera_params(name, params)
        try:
            safety = self.validate(name, safe_params)
            with self._camera_lock:
                raw = fn(**safe_params)
            data = json.loads(raw) if isinstance(raw, str) else {"status": "ok", "result": raw}
            status = str(data.get("status", "ok")).strip().lower()
            ok = status not in {"error", "blocked", "failed", "cancelled", "canceled"}
            return ToolCallResult(
                name,
                safe_params,
                ok,
                data,
                started,
                time.time(),
                safety=safety,
            )
        except Exception as exc:
            return ToolCallResult(
                name,
                safe_params,
                False,
                {"status": "error", "message": str(exc), "camera_source": self._camera_settings()},
                started,
                time.time(),
            )

    def capture_camera_preview(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight preview frame for the UI.

        Preview frames intentionally bypass the full airsim_take_photo tool
        path: no stationary check, no retry/cooldown loop, no base64 JSON.
        Agent visual reasoning still uses governed tools.
        """
        raw_params = dict(params or {})
        collector, error = self._ensure_camera_tools()
        if collector is None or self.camera_controller is None:
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": f"camera source unavailable: {error or 'unknown error'}",
            }

        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        camera_name = str(raw_params.get("camera_name") or settings.get("camera_name") or "0")
        vehicle_name = str(raw_params.get("vehicle_name") or settings.get("vehicle_name") or "")
        image_type_name = str(raw_params.get("image_type") or settings.get("image_type") or "scene").lower()
        try:
            timeout_sec = float(raw_params.get("timeout_sec") or 2.0)
        except (TypeError, ValueError):
            timeout_sec = 2.0
        timeout_sec = max(0.4, min(4.0, timeout_sec))
        try:
            max_width = int(raw_params.get("max_width") or 640)
        except (TypeError, ValueError):
            max_width = 640
        max_width = max(240, min(1280, max_width))
        try:
            quality = int(raw_params.get("quality") or 62)
        except (TypeError, ValueError):
            quality = 62
        quality = max(35, min(90, quality))

        try:
            controller = self.camera_controller
            if source in {"rtsp", "local"}:
                image_type = 0  # real cameras: scene only
            else:
                import airsim

                type_map = {
                    "scene": airsim.ImageType.Scene,
                    "depth": airsim.ImageType.DepthVis,
                    "segmentation": airsim.ImageType.Segmentation,
                    "infrared": airsim.ImageType.Infrared,
                    "depth_planar": airsim.ImageType.DepthPlanar,
                    "depth_perspective": airsim.ImageType.DepthPerspective,
                    "surface_normals": airsim.ImageType.SurfaceNormals,
                }
                image_type = type_map.get(image_type_name, airsim.ImageType.Scene)
            names = [vehicle_name] if vehicle_name else list(getattr(controller, "_vehicles", []) or [])
            if not names and source in {"rtsp", "local"}:
                names = [""]
            if not names:
                return False, b"", "text/plain; charset=utf-8", {
                    "status": "error",
                    "message": "no AirSim vehicle is available for camera preview",
                }
            vehicle = names[0]
            with self._camera_lock:
                raw = controller.capture_image(
                    camera_name=camera_name,
                    image_type=image_type,
                    vehicle_name=vehicle,
                    timeout=timeout_sec,
                )
            if not raw:
                return False, b"", "text/plain; charset=utf-8", {
                    "status": "error",
                    "message": "AirSim returned an empty camera preview frame",
                }
            body, mime_type = self._encode_preview_frame(bytes(raw), max_width=max_width, quality=quality)
            return True, body, mime_type, {
                "status": "ok",
                "vehicle": vehicle,
                "camera": camera_name,
                "image_type": image_type_name,
                "size_kb": round(len(body) / 1024, 1),
                "source": "airsim_preview",
            }
        except Exception as exc:
            self.camera_error = str(exc)
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": str(exc),
            }

    @staticmethod
    def _encode_preview_frame(raw: bytes, max_width: int = 640, quality: int = 62) -> tuple[bytes, str]:
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(raw, np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
            if image is None:
                return raw, "image/png"
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            height, width = image.shape[:2]
            if width > max_width:
                scale = max_width / float(width)
                image = cv2.resize(image, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if ok:
                return encoded.tobytes(), "image/jpeg"
        except Exception:
            pass
        return raw, "image/png"

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
        capabilities = self._camera_capabilities(backend_profile.capabilities.to_dict())
        if self._camera_source_enabled():
            available.add("airsim_take_photo")
            available.add("airsim_get_depth_map")
        if capabilities.get("image_capture"):
            available.add("airsim_vlm_confirm_target")
            available.add("airsim_vlm_analyze_image")
        from .tool_cards import cards_for_capabilities

        return cards_for_capabilities(capabilities, available)

    def reset_connection(self) -> None:
        """Drop the current controller/tool registry so the next call starts fresh."""
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
            )

        if not self.ensure_ready() or self.collector is None:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": self.init_error or "tool runtime unavailable"},
                started,
                time.time(),
            )

        self._lock.acquire()
        try:
            if (
                name in self.CONTROL_TOOLS
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
                )
            if safety.get("level") == "danger" and not safety.get("corrected_params"):
                return ToolCallResult(
                    name, params, False,
                    {"status": "blocked", "message": "flight command blocked by safety layer", "violations": safety.get("violations", [])},
                    started, time.time(), safety=safety,
                )
            if safety.get("corrected_params"):
                params.update(safety["corrected_params"])

            fn = self.collector.tools.get(name)
            if not fn:
                return ToolCallResult(
                    name, params, False,
                    {"status": "error", "message": f"unknown tool: {name}"},
                    started, time.time(), safety=safety,
                )

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
                if not terminal and not task_id:
                    ok = False
                    terminal = True
                    data = {
                        **data,
                        "status": "error",
                        "message": "async tool returned a non-terminal status without task_id",
                    }
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
                )
                if allow_reconnect and self._should_retry_after_reconnect(name, result):
                    return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
                return result
            except Exception as e:
                result = ToolCallResult(
                    name, params, False,
                    {"status": "error", "message": str(e)},
                    started, time.time(), safety=safety,
                )
                if allow_reconnect and self._should_retry_after_reconnect(name, result):
                    return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
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

    def _public_backend_profile(self) -> dict[str, Any] | None:
        if self.backend_profile is None:
            return None
        profile = self.backend_profile.to_public_dict()
        profile["capabilities"] = self._camera_capabilities(profile.get("capabilities") or {})
        if self.backend_id == "px4_mavlink" and self._real_vehicle:
            capabilities = dict(profile.get("capabilities") or {})
            capabilities.update({
                "real_vehicle": True,
                "simulated_vehicle": False,
                "requires_operator_approval": True,
            })
            profile["capabilities"] = capabilities
        return profile

    def _operation_contract(self, drone_status: dict[str, Any] | None = None) -> dict[str, Any]:
        """Describe the exact command and mission channel selected for this backend."""
        drone = drone_status or {}
        connected = bool(getattr(self.controller, "is_connected", False)) if self.controller is not None else False
        real_vehicle = bool((self._public_backend_profile() or {}).get("capabilities", {}).get("real_vehicle"))
        map_position_valid = bool(drone.get("map_position_valid", not real_vehicle))
        if self.backend_id == "px4_mavlink":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "real_px4" if real_vehicle else "px4_sitl",
                "command_channel": "MAVLink",
                "mission_channel": "PX4 native mission protocol",
                "mission_frame": "global_relative_alt",
                "return_channel": "PX4 native RTL mode",
                "position_source": drone.get("position_source") or "MAVLink telemetry",
                "map_position_valid": map_position_valid,
                "global_mission_ready": connected and map_position_valid,
            }
        if self.backend_id == "px4_ros2":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "px4_via_ros2",
                "command_channel": "ROS2 Provider Gateway",
                "mission_channel": "ROS2 offboard local path",
                "mission_frame": "local_ned",
                "return_channel": "ROS2 gateway PX4 RTL mode",
                "position_source": drone.get("position_source") or "PX4 ROS2 odometry",
                "map_position_valid": bool(drone.get("map_position_valid", False)),
                "global_mission_ready": False,
            }
        return {
            "backend": self.backend_id,
            "vehicle_kind": "simulation",
            "command_channel": "AirSim RPC",
            "mission_channel": "AirSim local path",
            "mission_frame": "local_ned",
            "return_channel": "AirSim local home path",
            "position_source": "AirSim NED + configured geodetic origin",
            "map_position_valid": connected,
            "global_mission_ready": connected,
        }

    def status_snapshot(self) -> dict[str, Any]:
        # Try a non-blocking lock so long connect/reconnect operations do not block status polling.
        if not self._lock.acquire(blocking=False):
            live_snapshot = self._busy_status_snapshot()
            if live_snapshot is not None:
                return live_snapshot
            if self._last_status_snapshot:
                cached = dict(self._last_status_snapshot)
                cached["busy"] = True
                return cached
            return {
                "ready": self.available,
                "init_error": self.init_error,
                "connected": False,
                "stale_connection": True,
                "busy": True,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": [],
                "backends": self.backend_registry.list_public(),
                "drone": None,
                "operation_contract": self._operation_contract(),
            }
        try:
            ready = self.ensure_ready()
            connected = False
            drone_status: dict[str, Any] | None = None
            stale_connection = False
            if ready and self.controller is not None:
                connected = bool(getattr(self.controller, "is_connected", False))
                if connected:
                    try:
                        drone_status = self.controller.get_status().to_dict()
                    except Exception as e:
                        drone_status = {"error": str(e)}
                        stale_connection = True
                    connected = bool(getattr(self.controller, "is_connected", False))
                    if self._status_is_stale(drone_status):
                        stale_connection = True
                        connected = False
            snapshot = {
                "ready": ready,
                "init_error": self.init_error,
                "connected": connected,
                "stale_connection": stale_connection,
                "busy": False,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": self.list_tool_cards() if ready else [],
                "backends": self.backend_registry.list_public(),
                "drone": drone_status,
                "vehicles": self._vehicles_status(connected),
                "operation_contract": self._operation_contract(drone_status),
            }
            self._last_status_snapshot = snapshot
            return dict(snapshot)
        finally:
            self._lock.release()

    def _vehicles_status(self, connected: bool) -> list[dict[str, Any]]:
        """Per-vehicle compact status for multi-vehicle backends (AirSim).

        Single-vehicle backends report one entry matching ``drone``; backends
        without per-vehicle status fall back to the default drone payload.
        """
        controller = self.controller
        if not connected or controller is None:
            return []
        try:
            names = list(controller.list_vehicles() or [])
        except Exception:
            names = []
        if not names:
            return []
        vehicles: list[dict[str, Any]] = []
        for name in names:
            try:
                status = controller.get_status(name).to_dict()
            except Exception as exc:
                status = {"vehicle_name": name, "error": str(exc)}
            status.setdefault("vehicle_name", name)
            vehicles.append(status)
        return vehicles

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return active vehicle link and firmware metadata for settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            connection_info = getattr(controller, "get_connection_info", None)
            if callable(connection_info):
                payload["connection"] = connection_info()
            if connected:
                firmware_info = getattr(controller, "get_firmware_info", None)
                if callable(firmware_info):
                    payload["firmware"] = firmware_info(force=bool(refresh))
                parameter_status = getattr(controller, "get_parameter_status", None)
                if callable(parameter_status):
                    payload["parameters"] = parameter_status()
            return payload
        finally:
            self._lock.release()

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return active vehicle parameters for the settings UI and Agent."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "parameters": [],
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "parameters": [],
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_parameters = getattr(controller, "get_parameters", None)
            if not callable(get_parameters):
                payload["status"] = "error"
                payload["message"] = "parameter download is not supported by this backend"
                return payload
            data = get_parameters(
                refresh=bool(refresh),
                timeout=float(timeout),
                query=str(query or ""),
                limit=int(limit),
                offset=int(offset),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one PX4 parameter through the active MAVLink backend."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            set_parameter = getattr(controller, "set_parameter", None)
            if not callable(set_parameter):
                payload["status"] = "error"
                payload["message"] = "parameter write is not supported by this backend"
                return payload
            data = set_parameter(
                name=str(name or ""),
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=float(timeout or 3.0),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return QGC-style read-only PX4 setup diagnostics for the settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_setup_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle setup diagnostics are not supported by this backend"
                return payload
            data = get_snapshot(include_history=include_history, history_limit=history_limit)
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return lightweight live vehicle telemetry for high-frequency settings UI updates."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_telemetry_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle live telemetry is not supported by this backend"
                return payload
            data = get_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def _busy_status_snapshot(self) -> dict[str, Any] | None:
        """Best-effort live telemetry while a long-running control tool owns the runtime lock.

        Long local waypoint execution can hold ToolRuntime._lock for many seconds. Returning
        only the last cached snapshot during that window makes the frontend marker jump from
        the initial frame to the final frame. This method reads telemetry only; it never sends
        flight-control commands. MAVLink backends can expose get_cached_status() so this read
        does not consume command ACK / mission-transfer messages while control is active.
        """
        controller = self.controller
        if controller is None:
            return None

        ready = self.available
        connected = bool(getattr(controller, "is_connected", False))
        if not ready or not connected:
            return None

        stale_connection = False
        try:
            get_cached_status = getattr(controller, "get_cached_status", None)
            if callable(get_cached_status):
                drone_status = get_cached_status().to_dict()
            else:
                drone_status = controller.get_status().to_dict()
        except Exception as exc:
            drone_status = {"error": str(exc)}
            stale_connection = True

        connected = bool(getattr(controller, "is_connected", False))
        if self._status_is_stale(drone_status):
            stale_connection = True
            connected = False

        cached = self._last_status_snapshot or {}
        snapshot = {
            "ready": ready,
            "init_error": self.init_error,
            "connected": connected,
            "stale_connection": stale_connection,
            "busy": True,
            "backend": self.backend_id,
            "backend_profile": cached.get("backend_profile") or self._public_backend_profile(),
            "tool_cards": cached.get("tool_cards") or [],
            "backends": cached.get("backends") or self.backend_registry.list_public(),
            "drone": drone_status,
        }
        self._last_status_snapshot = snapshot
        return dict(snapshot)

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
            "before_retry": failed_result.to_dict(),
        }
        return retry

    def _requires_vehicle_connection(self, name: str) -> bool:
        if name in {"memory_store", "drone_connect", "drone_disconnect"}:
            return False
        return name.startswith("drone_") or name.startswith("airsim_")

    def _is_connection_error(self, data: dict[str, Any]) -> bool:
        text = json.dumps(data, ensure_ascii=False, default=str).lower()
        return any(marker in text for marker in self.CONNECTION_ERROR_MARKERS)

    def _spec_for(self, name: str, fn: Callable[..., str]) -> ToolSpec:
        doc = inspect.getdoc(fn) or ""
        first_line = doc.splitlines()[0] if doc else name
        params: dict[str, Any] = {}
        signature = inspect.signature(fn)
        for key, param in signature.parameters.items():
            default = None if param.default is inspect._empty else param.default
            annotation = ""
            if param.annotation is not inspect._empty:
                annotation = getattr(param.annotation, "__name__", str(param.annotation))
            params[key] = {"default": default, "annotation": annotation}
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
