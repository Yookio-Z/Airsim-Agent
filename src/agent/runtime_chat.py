"""命令分发（submit_command）与对话：状态回读、chat 处理、附件与会话上下文、状态压缩。

拆分自 runtime.py（AgentRuntime 方法按职责迁移，行为不变）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.autonomy.supervisor import ExecutionSupervisor
from src.gcs import GroundStationServices
from src.modules.mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from src.modules.formation import FLIGHT_ACTIONS as FORMATION_FLIGHT_ACTIONS
from src.replay.session import ReplaySession, list_replay_sessions, read_replay_session

from .agent_loop import AgentLoop
from .llm import LLMMissionPlanner, LLMUnavailableError
from .loop_types import LoopState
from .memory import AgentMemory
from .planner import MissionPlan, MissionPlanner, MissionStep
from .run_log import RunLog, RunLogStore
from .skill_registry import SkillRegistry
from .sub_agent import SubAgentRunner
from .task_runs import TaskRunStore
from .tool_cards import TOOL_CARDS
from .tool_executor import TOOL_OUTPUT_SCHEMAS, ToolCallResult, ToolRuntime
from .llm_protocol import function_tool_schema, tool_schema_from_spec, validate_json_schema
from src.config import config

from .runtime_types import (
    ChatMessage,
    RunState,
    RuntimeEvent,
    ToolApprovalRequest,
)
from .runtime_settings import (
    AIRSIM_SETTINGS_TEMPLATES,
    ATTACHMENTS_DIR,
    REPO_ROOT,
    SESSIONS_DIR,
    SETTINGS_PATH,
    SKILLS_OVERRIDES_PATH,
    _application_settings,
    _build_connect_params,
    _camera_settings,
    _connection_settings,
    _default_application_settings,
    _default_camera_settings,
    _default_connection_settings,
    _load_settings,
    _save_settings,
    _select_connection_for_backend,
)


class RuntimeChatMixin:
    def submit_command(
        self,
        command: str,
        execute: bool = False,
        model_id: str = "",
        mode: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        command = command.strip()
        if attachments and not self.planner.supports_multimodal(model_id or None):
            return {"ok": False, "error": "当前模型未启用多模态能力，请选择或配置支持图像的模型。"}
        stored_attachments = self._store_attachments(attachments or [])
        if not command and stored_attachments:
            command = "请分析我提供的图片。"
        if not command:
            return {"ok": False, "error": "command is empty"}
        model_attachments = self._hydrate_attachments(stored_attachments)

        requested_mode = str(mode or "").strip().lower()
        if requested_mode == "chat":
            active_mode = "chat"
        elif requested_mode == "execute" or execute:
            active_mode = "execute"
        else:
            active_mode = "plan"
        execute = active_mode == "execute"
        request_id = f"{active_mode}_{time.time_ns()}"

        self._append_message("user", command, attachments=stored_attachments)
        if self._is_status_readback_command(command):
            tool_runtime = self.tools.status_snapshot()
            agent_state = self._agent_state_context(tool_runtime)
            return self._complete_status_readback_command(command, request_id, agent_state, mode=active_mode)

        if execute and self._is_conflicting(command):
            event = self._append_event("warning", "llm", "指令存在冲突或过于模糊", {"command": command})
            self._append_message("assistant", event.message, status="error")
            return {"ok": False, "error": event.message}

        busy_error = ""
        execution_slot_acquired = False
        if execute and self._execution_slot.locked():
            # 打断语义：新指令提交时自动中断旧任务（用户要求"打断对话即后台
            # 停止调用"）。_cancel_active_work 置取消旗标并标记旧 run；阻塞中
            # 的飞行命令由 stop_provider 安全中断；随后等待旧线程退出并释放
            # 执行槽（acquire 返回即旧线程已走完 finally），此时清除旗标启动
            # 新任务不会放跑旧任务的收尾工作。
            self._append_event(
                "warning",
                "system",
                "检测到任务执行中，自动中断旧任务后执行新指令",
                {"command": command[:80]},
            )
            self._cancel_active_work()
            execution_slot_acquired = self._execution_slot.acquire(timeout=25.0)
            if not execution_slot_acquired:
                busy_error = "旧任务未能及时停止，请稍后重试。"
            else:
                self._cancel_requested.clear()
        elif execute:
            execution_slot_acquired = self._execution_slot.acquire(blocking=False)
            if not execution_slot_acquired:
                busy_error = "已有任务正在理解、规划或执行，请等待当前任务结束。"
        elif active_mode == "plan" and self._execution_slot.locked():
            busy_error = "执行任务进行中，暂不生成会覆盖当前运行态的计划预览。"
        if busy_error:
            self._append_message(
                "assistant",
                busy_error,
                status="error",
                details={"mode": "execute", "phase": "blocked"},
            )
            return {"ok": False, "error": busy_error}

        self._cancel_requested.clear()
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

        tool_runtime = self.tools.status_snapshot()
        telemetry = tool_runtime.get("drone")
        agent_state = self._agent_state_context(tool_runtime)
        if active_mode == "chat":
            with self._lock:
                self._active_chat_requests.add(request_id)
            self._append_message(
                "assistant",
                "正在生成回复...",
                run_id=request_id,
                status="running",
                details={
                    "mode": "chat",
                    "phase": "responding",
                    "agent_state": agent_state,
                },
            )
            thread = threading.Thread(
                target=self._handle_chat_command,
                args=(command, model_id, request_id, agent_state, model_attachments),
                daemon=True,
            )
            try:
                thread.start()
            except Exception:
                with self._lock:
                    self._active_chat_requests.discard(request_id)
                raise
            return {"ok": True, "mode": "chat", "run_id": request_id, "status": "responding"}

        self._append_message(
            "assistant",
            "",
            run_id=request_id,
            status="running",
            details={
                "mode": active_mode,
                "phase": "understanding" if execute else "planning",
                "agent_state": agent_state,
                "thought_trace": [
                    {
                        "timestamp": time.time(),
                        "title": "理解指令" if execute else "规划预览",
                        "body": "正在读取后端连接、车辆状态和会话上下文。" if execute else "正在生成只读计划预览，不执行工具。",
                        "status": "running",
                    }
                ],
            },
        )

        with self._lock:
            self._pending_run_ids.add(request_id)
        self._thread = threading.Thread(
            target=self._plan_and_execute,
            args=(command, execute, telemetry, model_id),
            kwargs={
                "run_id": request_id,
                "agent_state": agent_state,
                "attachments": model_attachments,
                "release_execution_slot": execution_slot_acquired,
            },
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            with self._lock:
                self._pending_run_ids.discard(request_id)
            if execution_slot_acquired and self._execution_slot.locked():
                self._execution_slot.release()
            raise

        return {"ok": True, "mode": active_mode, "run_id": request_id, "status": "queued" if execute else "planned"}

    def _is_status_readback_command(self, command: str) -> bool:
        text = str(command or "").strip().lower()
        if not text:
            return False
        status_terms = (
            "status", "state", "telemetry", "position", "location", "where", "connected", "connection",
            "状态", "位置", "在哪", "哪里", "高度", "坐标", "遥测", "连接", "航向", "速度", "是否在线",
            "几架", "几台", "多少架", "多少台", "数量", "哪几架", "哪几台", "多少",
        )
        # "三台/两架/共四台" 等数字+量词组合 -> 数量类只读问句
        has_status_term = any(term in text for term in status_terms) or bool(
            re.search(r"[0-9一二两三四五六七八九十百]+[台架]", text)
        )
        if not has_status_term:
            return False
        action_terms = (
            "takeoff", "fly", "move", "land", "rtl", "return", "photo", "capture", "search", "scan",
            "起飞", "飞行", "向前", "向后", "向左", "向右", "移动", "降落", "返航", "拍照", "截图",
            "搜索", "扫描", "巡航", "航点", "航线", "路径", "绕圈", "正方形", "悬停", "解锁",
        )
        return not any(term in text for term in action_terms)

    def _complete_status_readback_command(
        self,
        command: str,
        request_id: str,
        agent_state: dict[str, Any],
        *,
        mode: str = "execute",
    ) -> dict[str, Any]:
        started_at = time.time()
        # 本路径不经过 LLM，但前端思考折叠块的数据源是 details.reasoning_text——
        # 不写的话展开"思考与执行过程"只能看到一行工具调用，没有任何思考内容。
        # 用路由判断 + 实际回读序列 + 状态总结充当这一步的"思考"说明。
        reasoning_lines = [
            "识别为状态回读类只读问题，走本地快速回读通道：不经过 LLM 规划，"
            "不执行任何飞行动作，直接读取后端状态后作答。",
        ]
        # multi-vehicle aware: report every vehicle, not only the default one
        names: list[str] = []
        try:
            list_result = self.tools.execute("drone_list_vehicles", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
            raw_names = (list_result.data or {}).get("vehicles") or []
            names = [str(n) for n in raw_names if str(n)]
        except Exception:
            names = []
        if len(names) > 1 and len(names) <= 4:
            reasoning_lines.append(
                f"回读序列：drone_list_vehicles 列出 {len(names)} 架车辆 → 逐台 drone_get_status 回读状态。"
            )
            per_vehicle: list[str] = []
            ok_all = True
            failures = 0
            for name in names:
                sub = self.tools.execute("drone_get_status", {"vehicle_name": name}, dry_run=False, blocked_by_supervisor=False)
                if not sub.ok:
                    ok_all = False
                    failures += 1
                    reason = str((sub.data or {}).get("message") or (sub.data or {}).get("error") or "未知原因")[:120]
                    per_vehicle.append(f"{name}: 状态读取失败（{reason}）")
                    continue
                per_vehicle.append(self._format_vehicle_line(name, sub.data))
            if failures == len(names):
                # every per-vehicle read failed: the cached vehicle list is
                # stale and the link is actually down — say so instead of a
                # table of failures
                reasoning_lines.append(
                    f"逐台 drone_get_status 全部读取失败（{len(names)} 架）——缓存车辆列表已过期，判定后端连接实际断开。"
                )
                answer = (
                    f"检测到 {len(names)} 架无人机的缓存列表，但全部状态读取失败——"
                    "后端连接实际已断开。请检查仿真器/飞控是否在运行，然后在连接面板重新连接。"
                )
                ok = False
                body = answer
                process_trace = [
                    {
                        "timestamp": time.time(),
                        "title": "读取无人机状态",
                        "body": body,
                        "status": "failed",
                        "tool": "drone_list_vehicles",
                        "params": {},
                        "kind": "tool",
                    }
                ]
            else:
                answer = f"当前后端共 {len(names)} 架无人机：\n" + "\n".join(per_vehicle)
                dashboard = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False)
                ok = dashboard.ok and ok_all
                body = answer
                process_trace = [
                    {
                        "timestamp": time.time(),
                        "title": "读取无人机状态",
                        "body": body,
                        "status": "completed" if ok else "failed",
                        "tool": "drone_list_vehicles",
                        "params": {},
                        "kind": "tool",
                    }
                ]
        else:
            reasoning_lines.append("回读序列：drone_get_status 直接回读当前无人机状态。")
            try:
                result = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
            except Exception:
                result = ToolCallResult("drone_get_status", {}, False, {"message": "后端未连接，无法读取状态"}, time.time(), time.time(), terminal=True)
            process_trace = [
                {
                    "timestamp": time.time(),
                    "title": "读取无人机状态",
                    "body": self._format_loop_result_body(result.data) or ("ok" if result.ok else "状态读取失败"),
                    "status": "completed" if result.ok else "failed",
                    "tool": "drone_get_status",
                    "params": {},
                    "kind": "tool",
                }
            ]
            answer = self._format_status_readback_answer(result.data if result.ok else {}, result.ok)
            ok = result.ok
            if not result.ok:
                message = str(result.data.get("message") or result.data.get("error") or "无人机状态读取失败")
                answer = f"状态读取失败：{message}"
        process_trace.append(
            {
                "timestamp": time.time(),
                "title": "状态总结",
                "body": answer,
                "status": "completed" if ok else "failed",
                "tool": "",
                "params": {},
                "kind": "reasoning",
            }
        )
        reasoning_lines.append(f"状态总结：{answer}")
        self._append_message(
            "assistant",
            answer,
            run_id=request_id,
            status="complete" if ok else "error",
            details={
                "mode": mode,
                "phase": "completed" if ok else "failed",
                "run_status": "completed" if ok else "failed",
                "started_at": started_at,
                "finished_at": time.time(),
                "agent_state": agent_state,
                "process_trace": process_trace,
                "reasoning_text": "\n".join(reasoning_lines),
                "fast_readback": True,
                "command": command,
            },
        )
        return {
            "ok": bool(ok),
            "mode": mode,
            "run_id": request_id,
            "status": "completed" if ok else "failed",
            "fast_readback": True,
        }

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

    def _format_status_readback_answer(self, telemetry: dict[str, Any], ok: bool = True) -> str:
        if not ok:
            return "无人机状态读取失败。"
        active_link = telemetry.get("active_link") if isinstance(telemetry.get("active_link"), dict) else {}
        backend = str(telemetry.get("backend") or active_link.get("backend") or self.tools.backend_id)
        position = telemetry.get("position_ned") if isinstance(telemetry.get("position_ned"), dict) else {}
        velocity = telemetry.get("velocity_ned") if isinstance(telemetry.get("velocity_ned"), dict) else {}
        gps = telemetry.get("gps") if isinstance(telemetry.get("gps"), dict) else {}
        x = self._finite_float(position.get("x")) or 0.0
        y = self._finite_float(position.get("y")) or 0.0
        z = self._finite_float(position.get("z")) or 0.0
        vx = self._finite_float(velocity.get("vx")) or 0.0
        vy = self._finite_float(velocity.get("vy")) or 0.0
        vz = self._finite_float(velocity.get("vz")) or 0.0
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        heading = self._finite_float(telemetry.get("heading_deg"))
        heading_text = f"，航向 {heading:.1f}°" if heading is not None else ""
        armed = "已解锁" if telemetry.get("armed") else "未解锁"
        flying = "飞行中" if telemetry.get("flying") else "未飞行/已落地"
        mode = str(telemetry.get("mode") or "--")
        collision = telemetry.get("has_collided")
        collision_text = "，未检测到碰撞" if collision is False or collision is None else "，检测到碰撞"
        gps_text = ""
        lat = self._finite_float(gps.get("lat"))
        lon = self._finite_float(gps.get("lon"))
        alt = self._finite_float(gps.get("alt"))
        if lat is not None and lon is not None:
            gps_text = f" GPS 约为北纬 {lat:.6f}°、东经 {lon:.6f}°"
            if alt is not None:
                gps_text += f"，海拔 {alt:.1f} m"
            gps_text += "。"
        flying_now = bool(telemetry.get("flying"))
        if flying_now:
            altitude_text = f"高度约 {abs(z):.2f} m"
        else:
            # AirSim keeps the last airborne z after landing; do not report it
            # as altitude ("landed at 2.9m" confuses operators)
            altitude_text = "高度 0 m（已着陆）"
        return (
            f"已读取当前无人机状态：后端为 {backend}，{armed}，{flying}，模式 {mode}。"
            f"当前位置 NED 为 N {x:.2f} / E {y:.2f} / D {z:.2f} m，{altitude_text}，"
            f"速度约 {speed:.2f} m/s{heading_text}{collision_text}。"
            f"{gps_text}"
        ).strip()

    def _handle_chat_command(
        self,
        command: str,
        model_id: str,
        request_id: str,
        agent_state: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        # Chat mode never executes flight-control tools, but a state question
        # must not be answered from a stale/busy snapshot either. Refresh the
        # read-only state once so the model answers from real data.
        agent_state = self._refresh_chat_state(agent_state)
        buffer: list[str] = []
        reasoning_buffer: list[str] = []
        tool_trace: list[dict[str, Any]] = []
        readonly_tools = self._chat_readonly_tools()

        def execute_readonly_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
            result = self.tools.execute(str(name), dict(args or {}), dry_run=False, allow_reconnect=True)
            data = result.data if isinstance(result.data, dict) else {"result": str(result.data)[:400]}
            return {"ok": bool(result.ok), "data": data}

        def on_tool_call(name: str) -> None:
            tool_trace.append(
                {
                    "timestamp": time.time(),
                    "title": f"只读查询 {name}",
                    "body": "chat 模式只读工具调用，获取实时数据",
                    "status": "completed",
                    "kind": "tool",
                }
            )
            self._update_assistant_message(
                request_id,
                "".join(buffer),
                "running",
                details("responding"),
                persist=False,
            )

        def cancelled() -> bool:
            with self._lock:
                return request_id in self._cancelled_request_ids

        def details(phase: str, process_status: str = "running") -> dict[str, Any]:
            process_trace: list[dict[str, Any]] = list(tool_trace)
            reasoning = self._compact_process_text("".join(reasoning_buffer).strip())
            if reasoning:
                process_trace.append(
                    {
                        "timestamp": time.time(),
                        "title": "模型推理",
                        "body": reasoning,
                        "status": process_status,
                    }
                )
            elif phase == "responding" and not tool_trace:
                process_trace.append(
                    {
                        "timestamp": time.time(),
                        "title": "生成回复",
                        "body": "正在根据会话上下文组织回答。",
                        "status": process_status,
                    }
                )
            return {
                "mode": "chat",
                "phase": phase,
                "process_trace": process_trace,
            }

        def on_reasoning(token: str) -> None:
            reasoning_buffer.append(token)
            # 推理进独立 reasoning_text 字段（前端思考块渲染），不占正文
            self._update_assistant_message(
                request_id,
                "".join(buffer),
                "running",
                {"mode": "chat", "phase": "responding",
                 "reasoning_text": "".join(reasoning_buffer)[-8000:]},
                persist=False,
            )

        def on_token(token: str) -> None:
            buffer.append(token)
            self._append_assistant_delta(
                request_id,
                token,
                "".join(buffer),
                details("responding"),
            )

        try:
            answer = self.planner.chat_response_stream(
                command=command,
                conversation=self._recent_chat_context(),
                agent_state=agent_state,
                memory=self.memory.snapshot(),
                model_id=model_id or None,
                on_token=on_token,
                on_reasoning=on_reasoning,
                attachments=attachments or [],
                should_stop=cancelled,
                readonly_tools=readonly_tools,
                execute_readonly_tool=execute_readonly_tool,
                on_tool_call=on_tool_call,
            )
            if not answer and buffer:
                answer = "".join(buffer)
            if cancelled():
                self._update_assistant_message(
                    request_id,
                    "已中断当前回复。",
                    "complete",
                    details("cancelled", "completed"),
                )
                return
            self._update_assistant_message(
                request_id,
                answer,
                "complete",
                details("completed", "completed"),
            )
        except LLMUnavailableError as exc:
            if cancelled():
                self._update_assistant_message(
                    request_id,
                    "已中断当前回复。",
                    "complete",
                    details("cancelled", "completed"),
                )
                return
            message = str(exc)
            self._append_event("danger", "chat", message, {"model_id": model_id})
            self._update_assistant_message(
                request_id,
                message,
                "error",
                {
                    "mode": "chat",
                    "phase": "failed",
                    "agent_state": agent_state,
                    "error": {"type": "model_unavailable", "message": message},
                },
            )
        except Exception as exc:
            self._append_event("danger", "chat", f"Chat response failed: {exc}", {})
            self._update_assistant_message(
                request_id,
                f"Chat 处理失败: {exc}",
                "error",
                {"mode": "chat", "phase": "failed", "agent_state": agent_state},
            )
        finally:
            with self._lock:
                self._active_chat_requests.discard(request_id)
                self._cancelled_request_ids.discard(request_id)

    def _recent_chat_context(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            messages = list(self._messages)
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                del messages[index]
                break
        if limit is not None:
            recent_messages = messages[-max(1, int(limit)):]
        else:
            planner = getattr(self, "planner", None)
            registry = getattr(planner, "registry", None)
            model = registry.get_default() if registry else {}
            public = registry._public_model(model) if registry and model else {}
            context_window = int(public.get("context_window") or 64_000)
            # Reserve roughly 40% for system/tool prompts and the response.
            budget = max(4_000, int(context_window * 0.6))
            selected: list[ChatMessage] = []
            used = 0
            for message in reversed(messages):
                estimate = max(1, math.ceil(len(str(message.content or "")) / 4))
                if selected and used + estimate > budget:
                    break
                selected.append(message)
                used += estimate
            recent_messages = list(reversed(selected))
        latest_image_message_id = next(
            (message.id for message in reversed(recent_messages) if message.role == "user" and message.attachments),
            "",
        )
        context: list[dict[str, Any]] = []
        for message in recent_messages:
            content = str(message.content or "").strip()
            if not content:
                continue
            context.append({
                "role": "assistant" if message.role == "assistant" else "user",
                "content": content[:1600],
                "attachments": self._hydrate_attachments(message.attachments)
                if message.id == latest_image_message_id else [],
            })
        return context

    def _store_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        if len(attachments) > 4:
            raise ValueError("每条消息最多附加 4 张图片")
        stored: list[dict[str, Any]] = []
        total_size = 0
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise ValueError("attachment must be an object")
            mime_type = str(item.get("mime_type") or item.get("type") or "").lower()
            data_url = str(item.get("data_url") or "")
            prefix = f"data:{mime_type};base64,"
            if mime_type not in allowed or not data_url.startswith(prefix):
                raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片")
            try:
                raw = base64.b64decode(data_url[len(prefix):], validate=True)
            except Exception as exc:
                raise ValueError(f"图片数据无法解析: {exc}") from exc
            if not raw:
                raise ValueError("图片不能为空")
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("单张图片不能超过 5 MB")
            total_size += len(raw)
            if total_size > 12 * 1024 * 1024:
                raise ValueError("单条消息图片总大小不能超过 12 MB")
            digest = hashlib.sha256(raw).hexdigest()
            storage_key = f"{digest}{allowed[mime_type]}"
            path = ATTACHMENTS_DIR / storage_key
            if not path.exists():
                path.write_bytes(raw)
            stored.append({
                "id": digest[:16],
                "name": str(item.get("name") or f"image-{index + 1}{allowed[mime_type]}")[:120],
                "mime_type": mime_type,
                "size": len(raw),
                "storage_key": storage_key,
                "url": f"/api/attachments/{storage_key}",
            })
        return stored

    def _hydrate_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hydrated: list[dict[str, Any]] = []
        for item in attachments[:4]:
            if not isinstance(item, dict):
                continue
            key = Path(str(item.get("storage_key") or "")).name
            path = ATTACHMENTS_DIR / key
            mime_type = str(item.get("mime_type") or "")
            if not key or not path.is_file() or mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                continue
            raw = path.read_bytes()
            hydrated.append({
                **item,
                "data_url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
            })
        return hydrated

    def attachment_file(self, storage_key: str) -> tuple[Path, str] | None:
        key = Path(storage_key).name
        if not key or key != storage_key:
            return None
        path = ATTACHMENTS_DIR / key
        mime_by_suffix = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
        mime_type = mime_by_suffix.get(path.suffix.lower())
        if not mime_type or not path.is_file():
            return None
        return path, mime_type

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
