"""Vehicle, link and backend control surface.

Link presets and switching, vehicle parameters/setup/telemetry, camera settings,
the AirSim settings templates, and the operator's manual flight overrides
(land / return-home / hover). Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from . import settings_store
from .run_state import RunState
from .settings_store import (
    AIRSIM_SETTINGS_TEMPLATES,
    REPO_ROOT,
    _build_connect_params,
    _camera_settings,
    _connection_settings,
    _select_connection_for_backend,
)
from pathlib import Path
from src.modules.mavlink_autodiscovery import discover_serial_mavlink_candidates
from typing import Any
import math
import os
import time


class VehicleMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _format_vehicle_line(self, name: str, telemetry: dict[str, Any]) -> str:
        """One compact per-vehicle summary line for multi-vehicle readbacks."""
        position = telemetry.get("position_ned") if isinstance(telemetry.get("position_ned"), dict) else {}
        x = self._finite_float(position.get("x"))
        y = self._finite_float(position.get("y"))
        z = self._finite_float(position.get("z"))
        pos_text = f"N {x:.2f} / E {y:.2f} / D {z:.2f}" if x is not None else "--"
        alt = abs(z) if z is not None else None
        flying = telemetry.get("flying")
        state_text = "飞行中" if flying else "未飞行/已落地"
        armed = "已解锁" if telemetry.get("armed") else "未解锁"
        if flying:
            alt_text = f"，高度约 {alt:.2f} m" if alt is not None else ""
        else:
            # AirSim keeps the last airborne z after landing; reporting it as
            # altitude would confuse operators ("landed at 2.9m")
            alt_text = "，高度 0 m（已着陆）"
        return f"{name}：{armed}，{state_text}{alt_text}，位置 {pos_text}"

    def _agent_state_context(self, tool_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = tool_runtime or self.tools.status_snapshot()
        profile = runtime.get("backend_profile") or {}
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else None
        with self._lock:
            current = self._current
            active_run = {
                "run_id": current.run_id,
                "status": current.status,
                "phase": current.phase,
                "progress": round(current.progress, 1),
                "current_step": current.current_step,
                "summary": current.summary,
            } if current and current.status in {"queued", "running", "paused", "responding", "awaiting_approval"} else None
        return {
            "ready": bool(runtime.get("ready")),
            "connected": bool(runtime.get("connected")),
            "stale_connection": bool(runtime.get("stale_connection")),
            # 操作员可编辑的基本操作规范（config/agent_system.md）：注入到
            # agent_state 后，规划器与 Agent Loop 的提示词都会带上它；飞行的
            # 前置条件这类"基本常识"（未起飞不能转向、降落后要重新解锁）
            # 放在这里，比散落在各个技能里更可靠。
            "agent_instructions": self._agent_instructions(),
            "busy": bool(runtime.get("busy")) or self._execution_slot.locked(),
            "backend": str(runtime.get("backend") or ""),
            "backend_name": str(profile.get("name") or profile.get("id") or runtime.get("backend") or ""),
            "capabilities": dict(profile.get("capabilities") or {}),
            "vehicle": self._compact_vehicle_state(drone),
            "vehicles": self._compact_vehicles_state(runtime.get("vehicles")),
            "active_run": active_run,
        }

    def _compact_vehicles_state(self, raw_vehicles: Any) -> list[dict[str, Any]]:
        """Compact per-vehicle states for the LLM context (multi-vehicle)."""
        if not isinstance(raw_vehicles, list):
            return []
        compact: list[dict[str, Any]] = []
        for item in raw_vehicles:
            if not isinstance(item, dict):
                continue
            state = self._compact_vehicle_state(item) or {}
            state.setdefault("vehicle_name", item.get("vehicle_name", ""))
            compact.append(state)
        return compact

    def _compact_vehicle_state(self, drone: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(drone, dict):
            return None
        keys = [
            "vehicle_name",
            "armed",
            "flying",
            "landed_state",
            "flight_mode",
            "mode",
            "altitude",
            "altitude_m",
            "position_ned",
            "velocity",
            "velocity_ned",
            "heading_deg",
            "battery",
            "battery_percent",
            "has_collided",
            "collision",
            "connection_error",
        ]
        compact = {key: drone.get(key) for key in keys if key in drone}
        if "error" in drone:
            compact["error"] = drone.get("error")
        return compact

    def _preflight_link_check(
        self,
        command: str,
        execute: bool,
        tool_runtime: dict[str, Any],
    ) -> dict[str, Any]:
        """执行前链路自检：不凭记忆假设链路/目标状态。

        未连接或链路陈旧时先尝试一次重连；重连后仍不可用就直接中止并上报，
        避免任务在"假状态"里空转（例如以为飞机已起飞、以为目标就在前方）。
        只对需要飞控的执行任务生效，只读/规划模式不受影响。
        """
        if not execute:
            return tool_runtime
        capabilities = (tool_runtime.get("backend_profile") or {}).get("capabilities") or {}
        if not capabilities.get("flight_control"):
            return tool_runtime
        connected = bool(tool_runtime.get("connected")) and not bool(tool_runtime.get("stale_connection"))
        if connected:
            self._warn_px4_health(tool_runtime)
            return tool_runtime
        self._append_event(
            "warning", "system", "任务前链路自检：飞控未连接，尝试自动重连",
            {"command": (command or "")[:60]},
        )
        # 链路抖动（UDP 丢包、飞控重启后的心跳间隙）很常见：先重连 + 轮询几次，
        # 只有持续不可用才中止任务，避免一次瞬时抖动把任务误判为"链路不可用"。
        refreshed = tool_runtime
        for attempt in range(3):
            try:
                result = self.tools.reconnect()
                self._append_event("info", "tool", f"Reconnect (preflight {attempt + 1}/3)", result.to_dict())
            except Exception as exc:
                self._append_event("warning", "system", f"任务前链路自检重连异常：{exc}", {})
            time.sleep(2.0)
            refreshed = self.tools.status_snapshot()
            if bool(refreshed.get("connected")) and not bool(refreshed.get("stale_connection")):
                self._warn_px4_health(refreshed)
                return refreshed
        raise RuntimeError(
            "任务前链路自检未通过：飞控链路持续不可用（已重连 3 次），任务已停止。"
            "请确认 PX4 SITL / 仿真仍在运行，并在设置里重新连接后再下发指令。"
        )

    def _warn_px4_health(self, tool_runtime: dict[str, Any]) -> None:
        """把 PX4 自身的健康告警（如罗盘未校准）显式抛到前端。

        这类告警以前只在 PX4 控制台可见，表现为"任务能飞但降落/悬停不稳"却
        查不到原因。这里在任务开始前提示根因，避免用户反复重启仿真排查。
        """
        drone = tool_runtime.get("drone") if isinstance(tool_runtime.get("drone"), dict) else {}
        text = str((drone or {}).get("status_text") or "").strip()
        if not text:
            return
        lowered = text.lower()
        if not any(key in lowered for key in ("compass", "calibrat", "magnetometer", "mag ")):
            return
        signature = text[:120]
        if getattr(self, "_px4_health_warned", "") == signature:
            return
        self._px4_health_warned = signature
        self._append_event(
            "warning", "system",
            f"PX4 健康告警：{text}（可能导致偏航/定位不稳、降落震荡；"
            "建议先在 PX4 校准罗盘，或确认磁力计配置与仿真一致）",
            {"status_text": text},
        )

    def _close_formation(self, reason: str) -> bool:
        """Hover all formation drones and stop the control thread.

        Called on run end, backend switches, and emergency stop so the swarm
        never keeps flying without an owner. Returns True when a mission was
        actually active.
        """
        try:
            return self.tools.formation_shutdown(reason)
        except Exception:
            return False

    def _attempt_failure_hover(self, run: RunState | None, reason: str) -> dict[str, Any] | None:
        try:
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if not capabilities.get("flight_control"):
                return None
            drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
            position = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            z = self._finite_float(position.get("z")) or 0.0
            min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
            active_airframe = bool(drone.get("flying") or drone.get("armed") or abs(z) >= min_altitude)
            if not active_airframe:
                return None
            # 任务异常收尾优先"受控降落"而不是原地悬停：历史上悬停会把飞机留在
            # 离起飞点很远的未知位置，随后失去控制而掉落。降落是明确的安全终态。
            result = self.tools.execute("drone_land", {}, dry_run=False, blocked_by_supervisor=False)
            payload = result.to_dict()
            self._append_event(
                "warning" if result.ok else "danger",
                "tool",
                "任务异常后安全降落",
                {"reason": reason, "land": payload},
            )
            if run is not None:
                self._append_process(
                    run,
                    "异常安全降落",
                    "Agent 决策中断，已发送受控降落指令。"
                    if result.ok
                    else f"Agent 决策中断，降落失败：{result.data.get('message', '')}",
                    status="completed" if result.ok else "failed",
                    tool="drone_land",
                    params={},
                    kind="tool",
                )
            return payload
        except Exception as exc:
            self._append_event("warning", "tool", "任务异常后安全悬停失败", {"reason": reason, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _attempt_hold_position(self, run: RunState | None, reason: str) -> dict[str, Any] | None:
        """任务未完成但飞行状态正常时的终态：保持悬停，等操作员决定。

        与 _attempt_failure_hover 的区别：那是异常收尾（任务失败、位置不可信），
        必须把飞机落到地面；这里只是到达流程边界（步数上限、操作员打断），链路
        与位置都正常，降落反而让操作员失去一架还在空中、本来可以继续指挥的飞机。
        """
        try:
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if not capabilities.get("flight_control"):
                return None
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            payload = result.to_dict()
            self._append_event(
                "info" if result.ok else "warning",
                "tool",
                "任务未完成，保持悬停等待指令",
                {"reason": reason, "hover": payload},
            )
            if run is not None:
                self._append_process(
                    run,
                    "保持悬停",
                    "任务到达边界（步数上限/被中断）但飞行状态正常，已保持悬停，等待下一步指令。"
                    if result.ok
                    else f"悬停指令失败：{result.data.get('message', '')}",
                    status="completed" if result.ok else "failed",
                    tool="drone_hover",
                    params={},
                    kind="tool",
                )
            return payload
        except Exception as exc:
            self._append_event("warning", "tool", "保持悬停失败", {"reason": reason, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _manual_land(self, targets: list[str] | None = None) -> dict[str, Any]:
        """降落目标机（空 = 全部载具）：并发派发降落，逐机验证落地后上锁。

        只有当目标列表里的每一台都确认落地才算完成，避免"降了一台就报完成"。
        """
        # 降落意味着操作员要停下来：正在跑的 Agent 任务对它已经没有意义，顺手
        # 取消掉。否则任务继续占着执行槽，操作员降落后再点起飞会被"任务执行中"
        # 拒绝——而界面上看不出还有任务在跑（实测反馈）。
        if self._active_run_is_interruptible():
            try:
                self._cancel_active_work()
            except Exception:
                pass
        runtime = self.tools.status_snapshot()
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        if not connected:
            return {"ok": False, "error": "flight controller link is offline or stale"}
        self._warn_px4_health(runtime)
        vehicles = [v for v in (runtime.get("vehicles") or []) if isinstance(v, dict) and not v.get("error")]
        by_name = {str(v.get("vehicle_name") or ""): v for v in vehicles}
        names = [str(t).strip() for t in (targets or []) if str(t).strip()] or list(by_name.keys()) or [""]

        to_land: list[str] = []
        to_disarm: list[str] = []
        results: list[dict[str, Any]] = []
        for name in names:
            v = by_name.get(name)
            if v is None and len(names) == 1:
                v = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else None
            flying = bool((v or {}).get("flying"))
            armed = bool((v or {}).get("armed"))
            if flying:
                to_land.append(name)
            elif armed:
                to_disarm.append(name)
            else:
                results.append({"vehicle": name or "默认机", "state": "already_grounded_disarmed", "ok": True})

        controller = getattr(self.tools, "controller", None)
        dispatch_land = getattr(controller, "dispatch_land", None) if controller is not None else None
        for name in to_land:
            if callable(dispatch_land):
                if not dispatch_land(name):
                    return {"ok": False, "error": f"{name or '默认机'} 降落派发失败", "vehicles": results}
            else:
                r = self.tools.execute(
                    "drone_land", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                if not r.ok:
                    return {"ok": False, "error": f"{name or '默认机'} 降落指令失败", "vehicles": results}

        # 轮询验证：只有在拿到"确实在地面"的强证据后才上锁。
        # 不能用 flying==False 当落地依据：PX4 一进入 LAND 模式 landed_state 就
        # 可能报非 IN_AIR，而飞机还在 2~3m 下沉；据此上锁会在空中切电机。
        controller = getattr(self.tools, "controller", None)
        ground_z = {}
        kin_ground = getattr(controller, "ground_z_kin", None) if controller is not None else None
        kin_ground_val = None
        if callable(kin_ground):
            try:
                kin_ground_val = kin_ground()
            except Exception:
                kin_ground_val = None
        if kin_ground_val is not None:
            for name in to_land:
                ground_z[name] = float(kin_ground_val)
        else:
            home_read = getattr(controller, "home_position", None) if controller is not None else None
            for name in to_land:
                if callable(home_read):
                    try:
                        home = home_read(name)
                    except Exception:
                        home = None
                    if isinstance(home, dict):
                        ground_z[name] = float(home.get("z", 0.0))
        pending = set(to_land)
        abort_note = ""
        unstable_counts: dict[str, int] = {}
        deadline = time.time() + 120.0
        while pending and time.time() < deadline:
            time.sleep(1.5)
            snap = self.tools.status_snapshot()
            for v in snap.get("vehicles") or []:
                n = str(v.get("vehicle_name") or "")
                if n not in pending:
                    continue
                pos = v.get("position_ned") if isinstance(v.get("position_ned"), dict) else {}
                vel = v.get("velocity_ned") if isinstance(v.get("velocity_ned"), dict) else {}
                att = v.get("attitude_rad") if isinstance(v.get("attitude_rad"), dict) else {}
                z = float(pos.get("z", 0.0) or 0.0)
                vz = abs(float(vel.get("vz", 0.0) or 0.0))
                try:
                    # MAV_LANDED_STATE_ON_GROUND == 1（IN_AIR=2, TAKEOFF=3, LANDING=4）
                    on_ground_flag = v.get("landed_state") is not None and int(v.get("landed_state")) == 1
                except (TypeError, ValueError):
                    on_ground_flag = False
                near_ground = True if n not in ground_z else abs(z - ground_z[n]) < 0.6
                if near_ground and vz <= 0.4 and (on_ground_flag or not v.get("armed")):
                    pending.discard(n)
                    continue
                # 姿态失稳保护：降落过程中出现大幅横滚/俯仰振荡时中止降落、转悬停
                # 并上报，避免在失控姿态下继续下降导致摔机。
                roll = abs(float(att.get("roll", 0.0) or 0.0))
                pitch = abs(float(att.get("pitch", 0.0) or 0.0))
                if max(roll, pitch) > 0.7:  # ~40°
                    unstable_counts[n] = unstable_counts.get(n, 0) + 1
                    if unstable_counts[n] >= 2:
                        try:
                            self.tools.execute("drone_hover", {"vehicle_name": n} if n else {},
                                               dry_run=False, blocked_by_supervisor=False)
                        except Exception:
                            pass
                        abort_note = (
                            f"{n or '默认机'} 降落过程中姿态失稳"
                            f"(roll/pitch > {math.degrees(0.7):.0f}°)，已中止降落并转为悬停"
                        )
                        pending.discard(n)
                        break
                else:
                    unstable_counts[n] = 0

        landed = [n for n in to_land if n not in pending]
        for name in to_disarm:
            r = self.tools.execute(
                "drone_disarm", {"vehicle_name": name} if name else {},
                dry_run=False, blocked_by_supervisor=False,
            )
            results.append({"vehicle": name or "默认机", "state": "grounded_disarmed", "ok": bool(r.ok)})
        for name in landed:
            r = self.tools.execute(
                "drone_disarm", {"vehicle_name": name} if name else {},
                dry_run=False, blocked_by_supervisor=False,
            )
            results.append({"vehicle": name or "默认机", "state": "landed_disarmed", "ok": bool(r.ok)})

        ok = not pending and all(r.get("ok") for r in results)
        if abort_note:
            message = abort_note
        elif pending:
            message = f"降落超时未确认: {', '.join(sorted(pending))}（未上锁，避免空中切电机）"
        elif len(results) == 1:
            message = "已降落并锁定"
        else:
            message = f"全部降落并锁定: {len(results)} 台"
        self._append_event("warning" if (pending or abort_note) else "info", "tool", "手动降落",
                           {"vehicles": results, "message": message})
        return {"ok": ok, "message": message, "vehicles": results}

    def _manual_return_home(self, targets: list[str] | None = None) -> dict[str, Any]:
        """返航：每台目标机都回到各自初始点并降落锁定。

        - 空中：飞回初始点上方 → 降落 → 上锁
        - 待飞(解锁在地面)：爬升 → 飞回初始点 → 降落 → 上锁
        - 已锁定但在远处：重新解锁 → 爬升 → 飞回初始点 → 降落 → 上锁
        - 已锁定且在初始点：无需动作
        """
        if self.tools.formation_active():
            return {
                "ok": False,
                "error": "a formation/coverage mission is active; use formation_command(action=land_all) or hover_all before return home",
            }
        runtime = self.tools.status_snapshot()
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        if not connected:
            return {"ok": False, "error": "flight controller link is offline or stale"}

        min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
        max_altitude = float(getattr(self.tools.safety.constraints, "max_altitude", 50.0) or 50.0)
        controller = getattr(self.tools, "controller", None)
        gps_to_ned = getattr(controller, "gps_to_ned", None) if controller is not None else None
        home_read = getattr(controller, "home_position", None) if controller is not None else None

        vehicles = [v for v in (runtime.get("vehicles") or []) if isinstance(v, dict) and not v.get("error")]
        target_set = {str(t).strip() for t in (targets or []) if str(t).strip()}
        selected = [
            v for v in vehicles
            if not target_set or str(v.get("vehicle_name") or "") in target_set
        ]
        if not selected and isinstance(runtime.get("drone"), dict) and runtime.get("drone"):
            selected = [runtime.get("drone")]

        memory = self.memory.snapshot()
        session = memory.get("session") if isinstance(memory, dict) else {}
        memory_start = session.get("last_task_start_position_ned") if isinstance(session, dict) else None
        speed = max(1.0, min(3.0, float(getattr(self.tools.safety.constraints, "max_velocity", 3.0) or 3.0)))

        results: list[dict[str, Any]] = []
        for vehicle in selected:
            name = str(vehicle.get("vehicle_name") or "")
            label = name or "默认机"
            gps = vehicle.get("gps") if isinstance(vehicle.get("gps"), dict) else None
            home = None
            if callable(home_read):
                try:
                    home = home_read(name)
                except Exception:
                    home = None

            # 当前位置：优先 GPS→NED（地面状态下 kinematics 多机读数不可靠）
            cur = None
            cur_from_gps = False
            if gps is not None and callable(gps_to_ned):
                try:
                    cur = gps_to_ned(float(gps.get("lat")), float(gps.get("lon")), float(gps.get("alt", 0.0) or 0.0))
                    cur_from_gps = cur is not None
                except (TypeError, ValueError):
                    cur = None
            if cur is None:
                pos = vehicle.get("position_ned") if isinstance(vehicle.get("position_ned"), dict) else {}
                cur = {
                    "x": self._ned_value(pos, "x", 0.0) or 0.0,
                    "y": self._ned_value(pos, "y", 0.0) or 0.0,
                    "z": self._ned_value(pos, "z", 0.0) or 0.0,
                }

            flying = bool(vehicle.get("flying"))
            armed = bool(vehicle.get("armed"))

            if not isinstance(home, dict) or home.get("x") is None:
                # 无该机返航点记录：单机回退 memory/safety home，多机跳过并说明
                if len(selected) == 1 and not name:
                    target_x = self._ned_value(memory_start, "x")
                    target_y = self._ned_value(memory_start, "y")
                    if target_x is None or target_y is None:
                        home_x, home_y = self.tools.safety.constraints.home_position
                        target_x, target_y = float(home_x), float(home_y)
                    home = {"x": round(float(target_x), 3), "y": round(float(target_y), 3), "z": 0.0}
                else:
                    results.append({
                        "vehicle": name, "ok": False,
                        "message": "no recorded initial position for this vehicle",
                    })
                    continue

            dist_home = math.hypot(cur["x"] - float(home["x"]), cur["y"] - float(home["y"]))

            # 已锁定且在初始点：无需动作
            if (not flying) and (not armed) and dist_home < 1.5:
                results.append({
                    "vehicle": name, "ok": True,
                    "message": "already at initial point and disarmed",
                    "dist_home_m": round(dist_home, 2),
                })
                continue

            # 目标巡航高度：当前离地高度（钳制在安全范围）。
            # cur 为 kinematics 帧时用 kinematics 地面标定（GPS 帧偏差 ~2m）
            kin_ground_val = None
            kin_ground_fn = getattr(controller, "ground_z_kin", None)
            if not cur_from_gps and callable(kin_ground_fn):
                try:
                    kin_ground_val = kin_ground_fn()
                except Exception:
                    kin_ground_val = None
            if kin_ground_val is not None:
                altitude_agl = max(0.0, float(kin_ground_val) - float(cur.get("z", 0.0)))
            else:
                altitude_agl = max(0.0, -(float(cur.get("z", 0.0)) - float(home.get("z", 0.0))))
            cruise = min(max(altitude_agl, min_altitude, 3.0), max_altitude)
            target = {
                "x": round(float(home["x"]), 3),
                "y": round(float(home["y"]), 3),
                "z": round(-cruise, 3),
            }

            if flying and dist_home < 0.6:
                # 空中且已在初始点上方 → 直接降落并锁定
                land_result = self.tools.execute(
                    "drone_land", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                disarm_result = None
                if land_result.ok:
                    disarm_result = self.tools.execute(
                        "drone_disarm", {"vehicle_name": name} if name else {},
                        dry_run=False, blocked_by_supervisor=False,
                    )
                ok_near = land_result.ok and (disarm_result is None or disarm_result.ok)
                results.append({
                    "vehicle": name, "ok": bool(ok_near),
                    "message": "already above initial point; landed and disarmed",
                    "dist_home_m": round(dist_home, 2),
                })
                continue

            # 其余所有状态(空中远处/待飞/已锁在远处) → 引导返航+降落锁定
            move_result = self.tools.execute(
                "drone_dispatch_return_land",
                {
                    **target,
                    "velocity": speed,
                    "vehicle_name": name,
                },
                dry_run=False,
                blocked_by_supervisor=False,
            )
            results.append({
                "vehicle": name,
                "ok": bool(move_result.ok),
                "message": move_result.data.get("message", "return + land dispatched"),
                "target_position_ned": target,
                "dist_home_m": round(dist_home, 2),
            })

        ok = all(r.get("ok") for r in results) and bool(results)
        if len(results) == 1:
            message = "返航已派发：到位后将自动降落锁定" if ok else str(results[0].get("message") or "return home failed")
        else:
            names = "、".join(str(r.get("vehicle") or "默认机") for r in results)
            message = f"返航已派发（到位后各自降落锁定）: {names}" if ok else f"部分返航派发失败: {names}"
        self._append_event(
            "info" if ok else "warning",
            "tool",
            "手动返航" if len(results) == 1 else "多机手动返航",
            {"vehicles": results},
        )
        return {"ok": ok, "message": message, "vehicles": results}

    def control(self, action: str, expected_backend: str = "", vehicles: list[Any] | None = None) -> dict[str, Any]:
        action = action.strip().lower()
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        # 目标机列表（空 = 全部载具）；来自 UI 多选 chips
        target_vehicles = [str(v).strip() for v in (vehicles or []) if str(v).strip()]
        if action in {"cancel", "stop", "interrupt"}:
            return self._cancel_active_work()
        if action == "pause":
            self.supervisor.pause()
            # 与 worker 线程读写同一个 run 对象，字段更新要在锁内（以前无锁，
            # 会和 _run_plan / _on_agent_loop_state 的写入互相覆盖）。
            with self._lock:
                if self._current and self._current.status == "running":
                    self._current.status = "paused"
                    self._current.phase = "paused"
            self._append_event("warning", "safety", "任务已暂停")
            return {"ok": True}
        if action == "resume":
            self.supervisor.resume()
            with self._lock:
                if self._current and self._current.status == "paused":
                    self._current.status = "running"
                    self._current.phase = "executing"
            self._append_event("info", "safety", "任务已恢复")
            return {"ok": True}
        if action == "emergency_stop":
            self._stop_envelope_guard()
            self._stop_tracking_assist("急停")
            self.supervisor.emergency_stop()
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            # hover every formation drone too — the single-vehicle hover only
            # covers the default vehicle
            formation_stopped = self._close_formation("emergency_stop")
            self._append_event(
                "danger",
                "safety",
                "急停已触发，尝试悬停",
                {**result.to_dict(), "formation_stopped": formation_stopped},
            )
            with self._lock:
                if self._current:
                    self._current.status = "blocked"
                    self._current.phase = "blocked"
                    self._current.failure_reason = "emergency stop"
                    self._current.finished_at = time.time()
            return {"ok": result.ok, "result": result.to_dict(), "formation_stopped": formation_stopped}
        if action == "reset_emergency":
            # "仅在地面状态"是 supervisor.reset_emergency 的文档约定，但那边只清
            # 标志位、没有这个检查。空中解除急停会让飞控指令重新放行，而飞机此刻
            # 只是在悬停——先落地再解除是更安全的顺序。
            # 只有"确知在空中"才拒绝：读不到遥测时放行，否则链路一断操作员就被
            # 永久锁在急停状态里出不来。
            flying = (self.tools.status_snapshot().get("drone") or {}).get("flying")
            if flying is True:
                return {
                    "ok": False,
                    "error": "飞机还在空中，请先降落再解除急停。",
                    "flying": True,
                }
            self.supervisor.reset_emergency()
            self._append_event(
                "warning",
                "safety",
                "急停状态已复位，飞行指令重新放行",
                {"flying": flying},
            )
            return {"ok": True}
        if action == "hover":
            results = []
            for name in target_vehicles or [""]:
                result = self.tools.execute(
                    "drone_hover", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                results.append(result.to_dict())
            ok = all(r["ok"] for r in results)
            self._append_event("info", "tool", "手动悬停", {"vehicles": results})
            return {"ok": ok, "result": {"vehicles": results}}
        if action == "land":
            result = self._manual_land(target_vehicles)
            self._append_event("warning", "tool", "手动降落", result)
            return {"ok": result.get("ok", False), "result": result}
        if action in {"return_home", "rtl"}:
            cancel_result = self._cancel_active_work() if self._active_run_is_interruptible() else None
            runtime = self.tools.status_snapshot()
            backend = str(runtime.get("backend") or "")
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if backend in {"px4_mavlink", "px4_ros2"} and capabilities.get("mode_control"):
                rtl_result = self.tools.execute(
                    "drone_set_mode",
                    {"mode": "RTL"},
                    dry_run=False,
                    blocked_by_supervisor=False,
                )
                result = {
                    "ok": rtl_result.ok,
                    "message": "PX4 native RTL requested" if rtl_result.ok else "PX4 rejected native RTL",
                    "control_channel": "MAVLink native mode" if backend == "px4_mavlink" else "ROS2 gateway native mode",
                    "result": rtl_result.to_dict(),
                }
            else:
                result = self._manual_return_home(target_vehicles)
                result["control_channel"] = "local NED guided path"
            if cancel_result:
                result["cancelled_active_task"] = cancel_result
            return result
        if action in {"connect", "reconnect"}:
            result = self.tools.reconnect()
            self._append_event("info", "tool", "Reconnect AirSim", result.to_dict())
            return {"ok": result.ok, "result": result.to_dict()}
        if action == "clear_events":
            with self._lock:
                self._events.clear()
            return {"ok": True}
        return {"ok": False, "error": f"unknown control action: {action}"}

    def _backend_mismatch(self, expected_backend: str = "") -> dict[str, Any] | None:
        expected = str(expected_backend or "").strip()
        active = str(self.tools.backend_id or "").strip()
        if expected and expected != active:
            return {
                "ok": False,
                "error": f"active backend changed from {expected} to {active}; command was not sent",
                "expected_backend": expected,
                "active_backend": active,
            }
        return None

    def link_summary(self) -> dict[str, Any]:
        """Single shot of how the Agent is wired to the vehicle.

        Reports the active flight backend, connection mode (MAVLink over
        UDP/TCP/serial, ROS2 gateway, AirSim), the perception axis profile
        (frame source type, target class, health) and a one-line hint
        describing which set of tools the Agent can call given that
        combination.
        """
        summary: dict[str, Any] = {
            "flight": {},
            "perception": {},
            "available": {"flight_control": False, "perception": False, "vlm": False},
            "hint": "",
        }
        try:
            tr = self.tools.status_snapshot()
            backend = str(tr.get("backend") or self.tools.backend_id or "")
            dr = tr.get("drone") or {}
            summary["flight"] = {
                "backend": backend,
                "connected": bool(tr.get("connected") and not tr.get("stale_connection")),
                "vehicle": dr.get("vehicle_name") or "px4_sys1",
                "mode": dr.get("mode"),
                "armed": dr.get("armed"),
                "flying": dr.get("flying"),
                "altitude_m": abs(float(dr.get("position_ned", {}).get("z") or 0.0)),
                "battery_v": dr.get("battery_voltage"),
                "gps": dr.get("gps"),
                "heartbeat_age_s": dr.get("heartbeat_age_s"),
            }
            summary["available"]["flight_control"] = summary["flight"]["connected"]
        except Exception as exc:
            summary["flight"] = {"error": str(exc)}
        try:
            axis = self.perception_axis
            online = bool(getattr(axis, "is_online", lambda: False)()) if axis is not None else False
            profile_name = getattr(getattr(axis, "_profile", None), "profile", "")
            source = ""
            try:
                if axis is not None and axis._engine is not None:
                    src_obj = axis._engine._frame_source
                    source = type(src_obj).__name__
            except Exception:
                pass
            summary["perception"] = {
                "enabled": getattr(axis, "enabled", False),
                "online": online,
                "profile": profile_name,
                "frame_source": source,
            }
            if axis is not None and getattr(axis, "enabled", False):
                health = axis.health()
                summary["perception"].update(health)
            summary["available"]["perception"] = online
        except Exception as exc:
            summary["perception"] = {"error": str(exc)}
        try:
            llm = self.application_settings().get("agent", {}) or {}
            summary["available"]["vlm"] = bool(llm.get("auto_select_multimodal_model", True))
        except Exception:
            pass
        # Plain-language hint based on what is actually live
        if summary["flight"].get("connected") and summary["perception"].get("online"):
            hint = "链路就绪:飞行控制可用,感知在线,Agent 可调用飞行与视觉工具。"
        elif summary["flight"].get("connected") and not summary["perception"].get("online"):
            hint = "飞行控制可用,感知离线(无法获取画面)。先检查感知配置(.env 或设置 → 连接)。"
        elif not summary["flight"].get("connected"):
            hint = "飞行控制未连接:选择正确的连接(仿真 UDP/真机数传/ROS2 网关)后重新激活。"
        else:
            hint = "链路状态未知,请检查服务与配置。"
        summary["hint"] = hint
        return summary

    def connection_settings(self) -> dict[str, Any]:
        """Return current connection settings (QGC Links style)."""
        settings = _connection_settings()
        try:
            settings["detected_mavlink_links"] = [
                candidate.to_dict()
                for candidate in discover_serial_mavlink_candidates()
            ]
        except Exception:
            settings["detected_mavlink_links"] = []
        try:
            settings["vehicle_info"] = self.tools.vehicle_info(refresh=False)
        except Exception as exc:
            settings["vehicle_info"] = {"status": "error", "message": str(exc)}
        return settings

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return current active vehicle connection and firmware metadata."""
        try:
            info = self.tools.vehicle_info(refresh=refresh)
            return {"ok": info.get("status") != "error", "vehicle_info": info}
        except Exception as exc:
            return {"ok": False, "vehicle_info": {"status": "error", "message": str(exc)}}

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return current active vehicle parameter cache/query results."""
        try:
            data = self.tools.vehicle_parameters(
                refresh=refresh,
                query=query,
                limit=limit,
                offset=offset,
                timeout=timeout,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "parameter_info": data}
        except Exception as exc:
            return {"ok": False, "parameter_info": {"status": "error", "message": str(exc), "parameters": []}}

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one active vehicle parameter through MAVLink."""
        try:
            data = self.tools.set_vehicle_parameter(
                name=name,
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=timeout,
            )
            return {"ok": data.get("status") == "ok", "parameter_write": data}
        except Exception as exc:
            return {"ok": False, "parameter_write": {"status": "error", "message": str(exc)}}

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return current active vehicle setup diagnostics and telemetry histories."""
        try:
            data = self.tools.vehicle_setup_snapshot(
                include_history=include_history,
                history_limit=history_limit,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_setup": data}
        except Exception as exc:
            return {"ok": False, "vehicle_setup": {"status": "error", "message": str(exc), "history": {}}}

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return current active vehicle lightweight telemetry and histories."""
        try:
            data = self.tools.vehicle_telemetry_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_telemetry": data}
        except Exception as exc:
            return {"ok": False, "vehicle_telemetry": {"status": "error", "message": str(exc), "history": {}}}

    def camera_settings(self) -> dict[str, Any]:
        """Return current UI camera source settings."""
        return _camera_settings()

    def save_connection_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the connection list / active id / auto-connect flag."""
        try:
            settings = settings_store._load_settings()
            if payload is None:
                payload = {}
            settings["connections"] = {
                "auto_connect": bool(payload.get("auto_connect", True)),
                "active_connection_id": str(payload.get("active_connection_id", "")),
                "connections": list(payload.get("connections") or []),
            }
            settings_store._save_settings(settings)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_camera_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the camera source settings used by the UI camera viewer."""
        try:
            settings = settings_store._load_settings()
            current = _camera_settings(settings)
            if payload is None:
                payload = {}
            payload_dict = dict(payload)
            merged = _camera_settings({"camera": {**current, **payload_dict}})
            persisted = dict(merged)
            if "host" not in payload_dict:
                persisted.pop("host", None)
            if "port" not in payload_dict:
                persisted.pop("port", None)
            settings["camera"] = persisted
            settings_store._save_settings(settings)
            return {"ok": True, "camera": merged}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def airsim_settings_info(self) -> dict[str, Any]:
        """List AirSim settings.json templates (with full content) + target path.

        AirSim's communication mode is decided by the settings.json:
          * SimpleFlight           -> airsim backend (direct RPC control)
          * PX4Multirotor + UDP    -> px4_mavlink backend (local/WSL PX4 SITL)
          * PX4Multirotor + TCP    -> px4_ros2 backend (Jetson/edge PX4 SITL)
        """
        templates_dir = REPO_ROOT / "config" / "airsim_settings"
        templates: list[dict[str, Any]] = []
        for template_id, meta in AIRSIM_SETTINGS_TEMPLATES.items():
            path = templates_dir / meta["file"]
            templates.append(
                {
                    "id": template_id,
                    "label": meta["label"],
                    "description": meta["description"],
                    "backend": meta["backend"],
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                    "content": path.read_text(encoding="utf-8") if path.is_file() else "",
                }
            )
        target = self._airsim_settings_path()
        return {
            "templates": templates,
            "target_path": str(target),
            "target_exists": target.is_file(),
        }

    @classmethod
    def _airsim_settings_path(cls) -> Path:
        """Resolve the AirSim settings.json location automatically.

        Nothing is hard-coded: %USERPROFILE% resolves from the current
        Windows user (Path.home()), so moving to another machine just works.
        The AIRSIM_SETTINGS_PATH environment variable remains available for
        deployment-level overrides (e.g. a custom AirSim install location);
        it is not exposed in the UI.
        """
        env_path = os.environ.get("AIRSIM_SETTINGS_PATH", "").strip()
        if env_path:
            return Path(env_path)
        candidates = [
            Path.home() / "Documents" / "AirSim" / "settings.json",
            Path.home() / "AirSim" / "settings.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def apply_airsim_settings_template(self, template_id: str) -> dict[str, Any]:
        """Backup the current settings.json and write the requested template.

        Never destructive: the existing file (if any) is copied to
        settings.json.bak-<timestamp> before writing.
        """
        meta = AIRSIM_SETTINGS_TEMPLATES.get(str(template_id or ""))
        if meta is None:
            return {"ok": False, "error": f"unknown template: {template_id}"}
        template_path = REPO_ROOT / "config" / "airsim_settings" / meta["file"]
        if not template_path.is_file():
            return {"ok": False, "error": f"template file missing: {template_path.name}"}
        target = self._airsim_settings_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup_path = None
            if target.is_file():
                backup_path = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
                backup_path.write_bytes(target.read_bytes())
            target.write_bytes(template_path.read_bytes())
            return {
                "ok": True,
                "template": template_id,
                "target_path": str(target),
                "backup_path": str(backup_path) if backup_path else None,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_backend(
        self,
        backend_id: str,
        connect_params: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Switch backend and reconnect, persisting the choice to disk.

        Args:
            backend_id: Target backend identifier.
            connect_params: Optional connection overrides. For PX4 MAVLink
                pass ``{"url": "udp:127.0.0.1:14540"}``. For AirSim pass
                ``{"ip": "127.0.0.1", "port": 41452}``.
            connection_id: Optional link id from the QGC Links panel. When
                provided, ``connect_params`` are taken from the stored link
                definition unless explicitly overridden.
        """
        # 执行中禁止切换链路（见 _run_in_progress_error）。代际自增必须在拒绝
        # 判断之后：先自增会把正在跑的任务判成"后端已换代"而被误拒。
        blocked = self._run_in_progress_error("切换飞行后端")
        if blocked:
            return blocked
        with self._lock:
            self._backend_generation += 1
        # Resolve params: explicit overrides > stored link definition > defaults.
        resolved_params = dict(connect_params) if connect_params else {}
        if connection_id:
            conn_section = _connection_settings()
            connection = next(
                (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
                None,
            )
            if connection:
                _, built_params = _build_connect_params(connection)
                # Connection definition takes precedence; explicit overrides are fallback defaults.
                resolved_params = {**(dict(connect_params) if connect_params else {}), **built_params}

        switch = self.tools.set_backend(backend_id)
        if not switch.ok:
            return {"ok": False, "error": switch.data.get("message", "backend switch failed")}

        # Persist a minimal backend record for backwards compatibility.
        try:
            settings = settings_store._load_settings()
            settings["backend"] = self.tools.backend_id
            if resolved_params:
                settings["connect_params"] = resolved_params
            settings_store._save_settings(settings)
        except Exception:
            pass

        ip = str(resolved_params.get("ip", "127.0.0.1"))
        port = int(resolved_params.get("port", 41452))
        url = str(resolved_params.get("url", ""))
        fallback_url = str(resolved_params.get("fallback_url", ""))
        remote_host = str(resolved_params.get("remote_host", ""))
        remote_port = int(resolved_params.get("remote_port", 0) or 0)
        real_vehicle = bool(resolved_params.get("real_vehicle", False))
        result = self.tools.reconnect(
            ip=ip,
            port=port,
            url=url,
            fallback_url=fallback_url,
            remote_host=remote_host,
            remote_port=remote_port,
            real_vehicle=real_vehicle,
        )
        self._append_event(
            "info" if result.ok else "warning",
            "tool",
            f"Backend {self.tools.backend_id} reconnect",
            result.to_dict(),
        )
        return {
            "ok": result.ok,
            "backend": self.tools.backend_id,
            "switch": switch.to_dict(),
            "result": result.to_dict(),
        }

    def deactivate_connection(self) -> dict[str, Any]:
        """Disconnect the live MAVLink/AirSim link and forget the active link id.

        Separate from ``activate_connection`` on purpose: the UI may identify the
        live link from the real endpoint (see the connection panel), and that link
        is not necessarily the one remembered in settings. Toggling by remembered
        id would then re-connect instead of disconnecting, so "断开" needs an
        explicit, id-free path.
        """
        blocked = self._run_in_progress_error("断开当前链路")
        if blocked:
            return blocked
        result = self.tools.execute("drone_disconnect", {})
        ok = result.ok
        if ok:
            settings = settings_store._load_settings()
            settings["connections"] = settings.get("connections") or {}
            settings["connections"]["active_connection_id"] = ""
            settings_store._save_settings(settings)
        self._append_event(
            "info" if ok else "warning",
            "tool",
            "Disconnect link",
            result.to_dict(),
        )
        return {"ok": ok, "action": "disconnect", "result": result.to_dict()}

    def activate_connection(self, connection_id: str) -> dict[str, Any]:
        """Connect/disconnect a QGC Links entry.

        If the requested link is already the active one and currently connected,
        disconnect. Otherwise switch backend and reconnect with the link params.
        """
        if not connection_id:
            return {"ok": False, "error": "connection_id required"}

        conn_section = _connection_settings()
        connection = next(
            (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
            None,
        )
        if not connection:
            return {"ok": False, "error": f"unknown connection {connection_id}"}

        backend_id, connect_params = _build_connect_params(connection)
        tool_runtime = self.tools.status_snapshot()
        active_id = str(conn_section.get("active_connection_id") or "")
        already_active = tool_runtime.get("backend") == backend_id
        currently_connected = bool(tool_runtime.get("connected")) and not tool_runtime.get("stale_connection")

        if already_active and currently_connected and active_id == connection_id:
            return self.deactivate_connection()

        result = self.set_backend(backend_id, connect_params=connect_params, connection_id=connection_id)
        if result.get("ok"):
            settings = settings_store._load_settings()
            settings["connections"] = settings.get("connections") or {}
            settings["connections"]["active_connection_id"] = connection_id
            settings_store._save_settings(settings)
        return result

    def _auto_connect_from_settings(self, expected_generation: int = 0, expected_backend_id: str = "") -> None:
        """Load persisted active link and reconnect on startup."""
        try:
            time.sleep(0.05)
            with self._lock:
                if (
                    self._backend_generation != expected_generation
                    or self.tools.backend_id != (expected_backend_id or self.tools.backend_id)
                ):
                    self._append_event(
                        "info",
                        "system",
                        "Auto-connect skipped because backend changed during startup",
                        {
                            "expected_generation": expected_generation,
                            "current_generation": self._backend_generation,
                            "expected_backend": expected_backend_id,
                            "current_backend": self.tools.backend_id,
                        },
                    )
                    return
            conn_section = _connection_settings()
            if not conn_section.get("auto_connect"):
                return
            expected_backend = self.tools.backend_id
            active_id, connection = _select_connection_for_backend(conn_section, expected_backend)
            if not connection:
                return
            backend_id, connect_params = _build_connect_params(connection)
            if active_id != conn_section.get("active_connection_id"):
                settings = settings_store._load_settings()
                settings["connections"] = settings.get("connections") or {}
                settings["connections"]["active_connection_id"] = active_id
                settings_store._save_settings(settings)
            self._append_event(
                "info",
                "system",
                f"Auto-connecting to {connection.get('name', active_id)}",
                {"backend": backend_id, "connect_params": connect_params},
            )
            self.set_backend(backend_id, connect_params=connect_params, connection_id=active_id)
        except Exception as exc:
            self._append_event("warning", "system", f"Auto-connect failed: {exc}", {})

    def execute_tool(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        result = self._execute_agent_tool(tool, params or {}, dry_run=dry_run)
        self.memory.remember_tool_call(tool, result.ok)
        self._remember_position_from_payload(result.data, source=tool)
        self._append_event("info" if result.ok else "warning", "tool", f"工具调用: {tool}", result.to_dict())
        return {"ok": result.ok, "result": result.to_dict()}

    def capture_camera_frame(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capture a UI stream frame without polluting task history or tool statistics."""
        result = self._execute_agent_tool("airsim_take_photo", params or {}, dry_run=False)
        return {"ok": result.ok, "result": result.to_dict()}

    def camera_preview_frame(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight camera preview frame for the frontend."""
        return self.tools.capture_camera_preview(params or {})

    def _safety_snapshot(self) -> dict[str, Any]:
        constraints = self.tools.safety.constraints
        return {
            "max_altitude_m": constraints.max_altitude,
            "min_altitude_m": constraints.min_altitude,
            "max_velocity_ms": constraints.max_velocity,
            "geofence_radius_m": constraints.max_distance_from_home,
            "home_position_ned": list(constraints.home_position),
            "no_fly_zones": constraints.no_fly_zones,
            "hard_rules": [
                "NED z must be negative in the air",
                "danger-level safety validation blocks execution",
                "emergency stop may override every action",
                "long-running search/tracking tools must return task_id and be polled",
            ],
        }
