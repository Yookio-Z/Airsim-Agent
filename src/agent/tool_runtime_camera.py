"""相机工具：设置/注册/执行分流与预览控制器生命周期。

拆分自 tool_executor.py（ToolRuntime 方法按职责迁移，行为不变）。
"""

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


class ToolRuntimeCameraMixin:
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

    # Real devices (local webcam / RTSP) must NOT stay opened when no preview
    # request arrives for a while — otherwise e.g. a laptop webcam "in use" LED
    # stays lit forever even though nobody is viewing it. Release idle preview
    # controllers after this many seconds of no use.
    PREVIEW_IDLE_RELEASE_SEC = 3.0

    def _ensure_preview_controller(self, params: dict[str, Any] | None = None) -> tuple[Any | None, str]:
        """Build (or reuse) a camera controller for a single UI preview frame.

        Unlike ``_ensure_camera_tools`` (which keeps ONE global controller for the
        agent's configured source), this honours an optional per-request
        ``source``/``url`` override so multiple viewer windows can display
        *different* sources at the same time — e.g. one window on the AirSim
        drone, another on a real drone's RTSP stream for digital-twin work.

        Controllers are cached by a source-specific key and reused across
        requests to avoid reconnecting on every frame.
        """
        raw = dict(params or {})
        settings = self._camera_settings()
        source = str(raw.get("source") or settings.get("source") or "airsim").strip().lower()
        if source not in {"airsim", "rtsp", "local"}:
            return None, f"unsupported camera source: {source}"

        if source == "rtsp":
            url = str(raw.get("url") or settings.get("url") or settings.get("rtsp_url") or "").strip()
            if not url:
                return None, "rtsp camera source requires a stream URL"
            key = f"rtsp:{url}"
        elif source == "local":
            try:
                index = int(raw.get("camera_name") or settings.get("camera_name") or 0)
            except (TypeError, ValueError):
                index = 0
            key = f"local:{index}"
        else:
            host = str(raw.get("host") or settings.get("host") or "127.0.0.1")
            try:
                port = int(raw.get("port") or settings.get("port") or 41452)
            except (TypeError, ValueError):
                port = 41452
            key = f"airsim:{host}:{port}"

        cache = getattr(self, "_preview_controllers", None)
        if cache is None:
            cache = {}
            self._preview_controllers = cache

        with self._camera_lock:
            controller = cache.get(key)
            if controller is not None and bool(getattr(controller, "is_connected", False)):
                controller._last_preview_used = time.time()
                return controller, ""
            if controller is not None:
                try:
                    controller.disconnect()
                except Exception:
                    pass
            try:
                if source == "airsim":
                    from src.modules.airsim_controller import AirSimController

                    controller = AirSimController(ip=host, port=port)
                    info = controller.connect(ip=host, port=port)
                elif source == "rtsp":
                    from src.modules.rtsp_camera_controller import RtspCameraController

                    controller = RtspCameraController(url)
                    info = controller.connect()
                else:
                    from src.modules.rtsp_camera_controller import LocalCameraController

                    controller = LocalCameraController(index)
                    info = controller.connect()
            except Exception as exc:
                return None, str(exc)
            if not getattr(info, "connected", False):
                details = getattr(info, "details", {}) or {}
                message = details.get("message") if isinstance(details, dict) else ""
                return None, (message or f"{source} camera source is not connected")
            cache[key] = controller
            controller._last_preview_used = time.time()
            return controller, ""

    def capture_camera_preview(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight preview frame for the UI.

        Preview frames intentionally bypass the full airsim_take_photo tool
        path: no stationary check, no retry/cooldown loop, no base64 JSON.
        Agent visual reasoning still uses governed tools.
        """
        self._start_preview_reaper()
        raw_params = dict(params or {})
        controller, error = self._ensure_preview_controller(raw_params)
        if controller is None:
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": f"camera source unavailable: {error or 'unknown error'}",
            }

        settings = self._camera_settings()
        source = str(raw_params.get("source") or settings.get("source") or "airsim").strip().lower()
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
                "source": source,
            }
        except Exception as exc:
            self.camera_error = str(exc)
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": str(exc),
            }

    # -- preview controller idle reaper ------------------------------------

    def _start_preview_reaper(self) -> None:
        """Lazily start a daemon thread that releases idle real-device controllers.

        Real cameras/streams (local webcam, RTSP) must be closed when no preview
        request arrives for a while, otherwise e.g. a laptop webcam LED stays on
        forever. The reaper disconnects cached local/rtsp controllers once they
        have been idle longer than ``PREVIEW_IDLE_RELEASE_SEC``. AirSim RPC
        clients are left connected (no physical device / cheap to reconnect).
        """
        if getattr(self, "_preview_reaper_started", False):
            return
        self._preview_reaper_started = True
        thread = threading.Thread(target=self._preview_reaper_loop, name="preview-reaper", daemon=True)
        thread.start()

    def _preview_reaper_loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self._reap_idle_preview_controllers()
            except Exception:
                pass

    def _reap_idle_preview_controllers(self) -> None:
        cache = getattr(self, "_preview_controllers", None)
        if not cache:
            return
        now = time.time()
        with self._camera_lock:
            for key, controller in list(cache.items()):
                if not (key.startswith("local:") or key.startswith("rtsp:")):
                    continue
                last = float(getattr(controller, "_last_preview_used", 0.0) or 0.0)
                if last and (now - last) > self.PREVIEW_IDLE_RELEASE_SEC:
                    try:
                        controller.disconnect()
                    except Exception:
                        pass
                    controller._last_preview_used = 0.0

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
