"""The plan-execute path: submit, plan, run, verify.

One LLM plan, then either a fixed tool sequence or the observe-decide-act loop,
then machine-checked verification. Also the chat path and the readback answers
that share the same command ingress. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from .llm import LLMUnavailableError
from .llm_protocol import (
    function_tool_schema,
    tool_schema_from_spec,
)
from .loop_types import LoopState
from .planner import (
    MissionPlan,
    MissionStep,
)
from .run_log import RunLog
from .run_state import (
    CONNECTION_FAILURE_TERMS,
    CORRECTION_ATTEMPTS_MAX,
    ChatMessage,
    MOTION_TOOLS,
    OBSERVATION_TOOLS,
    RunState,
    RuntimeEvent,
)
from .tool_executor import ToolCallResult
from typing import Any, Callable
import json
import math
import re
import threading
import time


class ExecutionMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


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
        # 执行中提交的指令优先当作"补充指令"并入正在跑的任务——这是操作员对该
        # 任务的追加要求（纠偏/补充约束），不是新的独立请求。必须放在只读回读
        # 判定之前：否则"顺便报告一下高度"这类问句会走回读快路径去抢工具锁
        # （实测 20s+ 超时），或者干脆把旧任务中断掉。
        if execute and self._execution_slot.locked():
            steer_run_id = self._steer_target_run_id()
            if steer_run_id:
                with self._lock:
                    self._pending_steer.append(str(command))
                self._append_event(
                    "info",
                    "system",
                    "已作为补充指令并入当前任务执行",
                    {"command": command[:200], "run_id": steer_run_id},
                )
                return {
                    "ok": True,
                    "mode": active_mode,
                    "run_id": steer_run_id,
                    "status": "steered",
                }
        # 状态类问句不再走关键词快速回读通道：词表判断让换个说法的同一句话拿到
        # 完全不同的处理路径，而且它绕过了规划器对意图的理解（实测操作员抱怨
        # Agent 无法准确理解意图）。所有指令统一交给规划器与 Agent Loop。

        if execute and self._is_conflicting(command):
            event = self._append_event("warning", "llm", "指令存在冲突或过于模糊", {"command": command})
            self._append_message("assistant", event.message, status="error")
            return {"ok": False, "error": event.message}

        busy_error = ""
        execution_slot_acquired = False
        if execute and self._execution_slot.locked():
            # 上面已经尝试过注入补充指令；走到这里说明当前任务已不可注入
            # （收尾中或正在生成最终回复）→ 退回"中断旧任务后执行新指令"：
            # _cancel_active_work 置取消旗标并标记旧 run，阻塞中的飞行命令由
            # stop_provider 安全中断；随后等待旧线程退出并释放执行槽
            # （acquire 返回即旧线程已走完 finally）。
            self._append_event(
                "warning",
                "system",
                "检测到任务执行中，自动中断旧任务后执行新指令",
                {"command": command[:80]},
            )
            self._cancel_active_work()
            # 旧循环可能正卡在一次 15~40s 的 LLM/VLM 调用里才退出，等待窗口要
            # 覆盖它，否则用户只会看到"旧任务未能及时停止"。
            execution_slot_acquired = self._execution_slot.acquire(timeout=60.0)
            if not execution_slot_acquired:
                busy_error = "旧任务未能及时停止，请稍后重试。"
            else:
                self._clear_cancel_state()
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

        # 只有真正开启新工作的分支才清取消状态。以前这行对所有模式无条件执行，
        # 而 chat 模式不获取执行槽，于是"停止任务 → 发一条 chat"会把取消旗标
        # 清掉，正在收尾的 AgentLoop 就再也看不到取消、继续执行飞行动作。
        if active_mode == "chat":
            pass
        elif execute:
            self._clear_cancel_state()
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

    def _plan_and_execute(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str = "",
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        release_execution_slot: bool = False,
    ) -> None:
        if execute:
            self._execution_thread_id = threading.get_ident()
        # 这一段（录像会话、RunLog 建目录、遥测快照）都必须在 try 之内：释放
        # 执行槽的 finally 挂在下面，任何一步在 try 之前抛异常，worker 线程就会
        # 带着已获取的执行槽死掉，此后每次提交都要等满 60 秒并返回"旧任务未能
        # 及时停止"，助手消息永远停在运行中，只能重启进程恢复。
        replay_session = None
        try:
            if execute and run_id:
                replay_session = self._start_replay_session(
                    run_id,
                    {"run_id": run_id, "command": command, "mode": "execute"},
                )
            if run_id:
                with self._lock:
                    self._run_log = RunLog(run_id)
                tool_runtime = self.tools.status_snapshot()
                self._run_log.write(
                    "run.start",
                    {
                        "command": command,
                        "mode": "execute" if execute else "plan",
                        "model_id": model_id or "",
                        "backend": str(tool_runtime.get("backend") or ""),
                        "attachments": len(attachments or []),
                    },
                )
            else:
                with self._lock:
                    self._run_log = None
            tool_runtime = self.tools.status_snapshot()
            tool_runtime = self._preflight_link_check(command, execute, tool_runtime)
            drone_state = tool_runtime.get("drone") or {}
            drone_pos = drone_state.get("position_ned") or {}
            drone_z = float(drone_pos.get("z", 0.0) or 0.0)
            # Simulation health gate: a wildly drifted vehicle position means
            # the simulator link broke (HIL drop / AirSim↔PX4 baseline loss).
            # Refuse to execute flight steps against a broken environment
            # instead of letting the plan run into the drift.
            if abs(drone_z) > 25.0:
                raise RuntimeError(
                    f"仿真环境异常:无人机位置失稳 (z={drone_z:.0f}m)。"
                    "任务已停止,请重启 AirSim 后再重新下发。"
                )
            agent_state = agent_state or self._agent_state_context(tool_runtime)
            backend_profile = tool_runtime.get("backend_profile") or {}
            capabilities = backend_profile.get("capabilities") or {}
            memory_snapshot = self.memory.snapshot()
            if run_id:
                self._update_assistant_message(
                    run_id,
                    "正在理解指令并准备执行计划...",
                    "running",
                    {
                        "mode": "execute" if execute else "plan",
                        "phase": "planning",
                        "agent_state": agent_state,
                        "process_trace": [
                            {
                                "timestamp": time.time(),
                                "title": "理解指令",
                                "body": "正在解析任务意图并生成可执行的工具序列；模型不可用时不会降级发出飞控指令。",
                                "status": "running",
                            }
                        ],
                    },
                )
            skill_guidance = self.skills.guidance_cards(command, capabilities, memory_snapshot)
            if skill_guidance:
                agent_state = self._agent_state_with_skill_guidance(agent_state, skill_guidance)
            # Primary path: Plan-Execute. The LLM plans once and the runtime
            # executes/verifies the sequence — simple commands finish after a
            # few deterministic steps without an agent loop, and failures or
            # observation-dependent tasks enter the correction loop.
            route = {
                "level": "plan_execute",
                "strategy": "plan_execute",
                "reason": "Plan-Execute primary path: LLM plans once, runtime executes and verifies; correction loop only on failure",
                "risk_level": "elevated" if capabilities.get("flight_control") else "safe",
            }
            self._append_event("info", "planner", "Plan-Execute 主路径启动", route)
            self._execute_plan_execute_route(
                command,
                execute,
                telemetry,
                model_id,
                route,
                capabilities,
                tool_runtime,
                memory_snapshot,
                run_id,
                agent_state,
                attachments=attachments or [],
            )
            return
        except Exception as e:
            import logging
            import traceback

            logging.getLogger("runtime").warning(
                "plan_execute_route_exception: %s\n%s",
                str(e),
                "\n".join(traceback.format_exc().splitlines()[-25:]),
            )
            failed_run = None
            hover_result = None
            if run_id:
                with self._lock:
                    if self._current and self._current.run_id == run_id:
                        self._current.status = "failed"
                        self._current.phase = "failed"
                        self._current.failure_reason = str(e)
                        self._current.finished_at = time.time()
                        failed_run = self._current
            if execute:
                hover_result = self._attempt_failure_hover(failed_run, str(e))
            if run_id:
                message = f"任务处理失败: {str(e)}"
                if hover_result:
                    message += " 已尝试执行安全悬停。"
                details = (
                    self._message_details(failed_run)
                    if failed_run
                    else {
                        "mode": "execute" if execute else "plan",
                        "phase": "failed",
                        "agent_state": agent_state or {},
                    }
                )
                if hover_result:
                    details["failure_safety_hover"] = hover_result
                self._update_assistant_message(
                    run_id,
                    message,
                    "error",
                    details,
                )
                if failed_run:
                    self._publish_run_update(failed_run)
                    try:
                        self._finalize_task_run(failed_run)
                    except Exception:
                        pass
            else:
                self._append_message("assistant", f"任务处理失败: {str(e)}", status="error")
            self._append_event("danger", "planner", "任务处理失败", {"error": str(e)})
        finally:
            if replay_session is not None:
                self._stop_replay_session()
            self._close_run_log(run_id, execute)
            if execute and self._execution_thread_id == threading.get_ident():
                self._execution_thread_id = 0
            if release_execution_slot and self._execution_slot.locked():
                self._execution_slot.release()
            if run_id:
                with self._lock:
                    self._cancelled_request_ids.discard(run_id)
                    self._pending_run_ids.discard(run_id)

    def _try_llm_plan(
        self,
        *,
        command: str,
        telemetry: dict[str, Any] | None,
        model_id: str,
        run_id: str,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        agent_state: dict[str, Any],
        attachments: list[dict[str, Any]],
        reasoning_sink: Callable[[str], None] | None = None,
    ) -> MissionPlan | None:
        planner_tool_cards = self._planner_tool_cards(
            command,
            tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            capabilities,
            memory_snapshot,
        )
        plan = self.planner.plan(
            command=command,
            tools=self.tools.list_tools(),
            safety=self._safety_snapshot(),
            telemetry=telemetry,
            memory=memory_snapshot,
            model_id=model_id or None,
            backend=str(tool_runtime.get("backend") or (tool_runtime.get("backend_profile") or {}).get("id") or ""),
            capabilities=capabilities,
            tool_cards=planner_tool_cards,
            agent_state=agent_state,
            conversation_context=self._recent_chat_context(),
            attachments=attachments,
            on_reasoning=reasoning_sink,
        )
        if run_id:
            plan.run_id = run_id
        run_log = self._run_log
        if run_log is not None:
            run_log.write(
                "plan",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "intent": plan.intent,
                    "summary": plan.summary,
                    "goal": plan.goal,
                    "steps": [
                        {"id": step.id, "label": step.label, "tool": step.tool, "params": step.params, "layer": step.layer}
                        for step in plan.steps
                    ],
                },
            )
        if str(plan.planner_source).startswith("rules"):
            self._append_event(
                "warning",
                "planner",
                "LLM 规划不可用，回退到规则路径",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "risk_notes": list(plan.risk_notes),
                },
            )
            return None
        return plan

    @staticmethod
    def _plan_has_observation_dependency(plan: MissionPlan | None) -> bool:
        """A fixed sequence fails when an observation step precedes a motion
        step: the later move depends on what the observation shows (photo ->
        decide -> move), so it must run in the ReAct loop instead."""
        steps = list(plan.steps) if plan else []
        for index, step in enumerate(steps):
            if step.tool in OBSERVATION_TOOLS:
                if any(s.tool in MOTION_TOOLS for s in steps[index + 1 :]):
                    return True
        return False

    @staticmethod
    def _plan_requires_agent_loop(plan: MissionPlan | None) -> bool:
        """Choose Plan-Execute vs ReAct. The planner may declare agent_loop
        explicitly (visual search, tracking, conditional tasks); otherwise a
        fixed sequence with observation -> motion steps is detected
        structurally — no natural-language classification involved."""
        if plan is None:
            return False
        if ExecutionMixin._plan_has_observation_dependency(plan):
            return True
        # A plan that only queries structured perception state
        # (perception_status) and otherwise uses plain motion/telemetry tools
        # executes fine as a fixed sequence: the executor reads the perception
        # snapshot directly, no LLM image analysis is involved. Only honour
        # the planner's agent_loop declaration when the plan actually contains
        # LLM-reading tools (photo/VLM/detect/depth).
        if plan.execution_mode == "agent_loop":
            steps = list(plan.steps) if plan else []
            if any(step.tool in OBSERVATION_TOOLS for step in steps):
                return True
            # The planner's agent_loop declaration is honoured for motion-only
            # plans, but a plan whose only "observation" steps are structured
            # perception_status queries needs no LLM reading -- the executor
            # consumes the snapshot directly, so run it as one fixed sequence.
            if not any(step.tool == "perception_status" for step in steps):
                return True
        return False

    def _correction_command(self, run: RunState) -> str:
        """Structured failure context for the ReAct correction loop: the LLM
        needs the failed step, tool output, verification summary, and current
        position to choose a meaningful corrective action."""
        parts = [f"继续完成原始任务并修正失败步骤。原始任务：{run.command}"]
        if run.failure_reason:
            parts.append(f"失败原因：{run.failure_reason}")
        verification = run.verification or {}
        if verification.get("summary"):
            parts.append(f"校验摘要：{verification.get('summary')}")
        failed_step = next(
            (s for s in (run.plan.steps if run.plan else []) if s.status == "failed"),
            None,
        )
        if failed_step is not None:
            detail = failed_step.result if isinstance(failed_step.result, dict) else {}
            message = str(detail.get("message") or detail.get("error") or "")
            parts.append(f"失败步骤：{failed_step.id} {failed_step.tool}{'：' + message if message else ''}")
        final = run.final_telemetry or {}
        position = final.get("position_ned") if isinstance(final, dict) else None
        if isinstance(position, dict) and any(position.get(k) is not None for k in ("x", "y", "z")):
            parts.append(
                f"当前 NED 位置：N {position.get('x')} / E {position.get('y')} / D {position.get('z')}"
            )
        return "；".join(parts)

    @staticmethod
    def _agent_loop_primary_command(run: RunState) -> str:
        """Command for a plan routed to ReAct before execution: the fixed
        sequence cannot express the task, so the loop decides per step."""
        return (
            f"按已生成的计划逐步执行。原始任务：{run.command}\n"
            "计划依赖中间观察结果（拍照/识别/确认后决策），请逐步执行："
            "每次先观察最新状态和工具返回，再选择下一步工具，直到任务完成。"
        )

    @staticmethod
    def _agent_state_with_skill_guidance(
        agent_state: dict[str, Any],
        skill_guidance: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not skill_guidance:
            return agent_state
        enriched = dict(agent_state or {})
        # Catalog entries are metadata only, so keep them all: a skill missing
        # from the catalog can never be discovered or activated.
        enriched["skill_guidance"] = [
            {
                "name": card.get("name", ""),
                "display_name": card.get("display_name", ""),
                "description": card.get("description", ""),
                "required_capabilities": list(card.get("required_capabilities") or []),
                "subtools": list(card.get("subtools") or []),
                "executable": False,
            }
            for card in skill_guidance
        ]
        return enriched

    def _execute_plan_execute_route(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str,
        route: dict[str, Any],
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skill_guidance: list[dict[str, Any]] | None = None,
    ) -> None:
        run_id = run_id or f"run_{int(time.time() * 1000)}"
        skill_guidance = skill_guidance or self.skills.guidance_cards(command, capabilities, memory_snapshot)
        agent_state = self._agent_state_with_skill_guidance(
            agent_state or self._agent_state_context(tool_runtime),
            skill_guidance,
        )
        reasoning_sink = self._plan_reasoning_sink(run_id, command)
        plan = self._try_llm_plan(
            command=command,
            telemetry=telemetry,
            model_id=model_id,
            run_id=run_id,
            capabilities=capabilities,
            tool_runtime=tool_runtime,
            memory_snapshot=memory_snapshot,
            agent_state={**agent_state, "planner_mode": "plan_execute"},
            attachments=attachments or [],
            reasoning_sink=reasoning_sink,
        )
        final_flush = getattr(reasoning_sink, "final_flush", None)
        if callable(final_flush):
            final_flush()
        reasoning_full = str(getattr(reasoning_sink, "full_text", "") or "").strip()
        if plan is None:
            if execute:
                # LLM 失效时的安全原则：不自动退化为规则规划继续飞行。
                # 规则规划覆盖不了模型级任务理解，自动执行会把 LLM 失效的
                # 影响面扩大到真实飞控；改为失败 + 安全悬停（由
                # _plan_and_execute 的异常路径执行 _attempt_failure_hover）。
                self._append_event(
                    "danger",
                    "planner",
                    "LLM 规划不可用，已停止执行以保护无人机",
                    {"command": command, "phase": "planning"},
                )
                raise LLMUnavailableError("LLM 规划不可用，已停止执行以保护无人机。请检查模型配置后重试。")
            plan = self.rule_planner.plan(command, capabilities=capabilities)
            plan.run_id = run_id
            plan.planner_source = "rules_plan_execute_fallback"
            plan.assumptions.append("仅规划预览：LLM 不可用，使用本地规则规划器生成只读预览。")
        else:
            plan.assumptions.append("采用 Plan-Execute：LLM 一次性规划，runtime 串行执行并校验；失败时进入 Agent Loop 纠错。")

        if self._is_run_cancelled(run_id):
            # 规划过程本身要花 15~40s（LLM 调用不可中断）。这期间操作员取消或
            # 改发了新指令，就不要再创建 run 去执行旧计划——立即退出，把执行槽
            # 让给新指令，用户不必傻等"旧任务未能及时停止"。
            self._append_event(
                "info",
                "planner",
                "规划期间任务已被取消，放弃执行旧计划",
                {"run_id": run_id},
            )
            return

        run = RunState(
            run_id=run_id,
            command=command,
            intent=plan.intent,
            summary=plan.summary,
            status="queued" if execute else "planned",
            mode="execute" if execute else "plan",
            phase="planning",
            execute=execute,
            model_id=model_id,
            plan=plan,
            task_level=route["level"],
            route_strategy=route["strategy"],
            route_reason=route["reason"],
            risk_level=route["risk_level"],
            answer_with_llm=False,
            start_telemetry=dict(telemetry or {}),
            agent_state=agent_state,
            backend_generation=self._backend_generation,
        )
        self._register_current_run(run)
        self._start_task_run(run)
        # 飞行包线看门狗必须在两条执行路径上都启动：之前只挂在 Agent Loop
        # 状态回调里，走一次性计划路径时完全没有保护（实测飞机爬到 30m 才被
        # 人工急停）。
        self._maybe_start_envelope_guard(run)
        self._append_event(
            "info",
            "planner",
            "Plan-Execute route selected",
            {"run_id": run.run_id, "execute": execute, "planner_source": plan.planner_source, **route},
        )
        # 思考块内容组合：
        #   模型真实流式推理 reasoning_text（思考块主内容，打字机展示）
        #   计划摘要 plan_summary（任务理解 + 步骤列表，前端单独一块）
        plan_reasoning = str(getattr(plan, "reasoning", "") or "").strip()
        if not plan_reasoning:
            plan_reasoning = f"任务理解：{plan.summary}"
        # 模型有时把 reasoning 字段写成整个 plan JSON 草稿——解析取其中的
        # reasoning/summary 文本，避免思考块里出现一大段 JSON
        if plan_reasoning.lstrip().startswith("{"):
            extracted = ""
            try:
                parsed_reasoning = json.loads(plan_reasoning)
                if isinstance(parsed_reasoning, dict):
                    extracted = str(parsed_reasoning.get("reasoning") or parsed_reasoning.get("summary") or "").strip()
            except Exception:
                pass
            if not extracted:
                # 截断/不合法的 JSON 草稿：正则直接抠 "reasoning" 字段值
                m = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', plan_reasoning)
                if m:
                    extracted = (
                        m.group(1)
                        .replace("\\n", "\n")
                        .replace("\\t", " ")
                        .replace('\\"', '"')
                    )
            if extracted:
                plan_reasoning = extracted
        # 模型真实思考（流式 token + 计划里的 reasoning 字段，二者都是模型输出）
        # 与"计划步骤列表"分开：
        #   reasoning_text = 模型思考全文（前端思考块打字机展示的主内容）
        #   plan_summary   = 执行计划步骤列表（单独一块，不占思考）
        streamed_reasoning = self._strip_plan_json_draft(reasoning_full) if reasoning_full else ""
        thinking_parts = [p for p in (streamed_reasoning, plan_reasoning) if p]
        model_thinking = "\n\n".join(dict.fromkeys(thinking_parts)).strip()
        plan_step_tools = [step.tool for step in (plan.steps or []) if step.tool and step.tool != "memory_store"]
        plan_summary = ""
        if plan_step_tools:
            plan_summary = "执行计划（" + str(len(plan_step_tools)) + " 步）：" + " → ".join(plan_step_tools)
        reasoning_full = model_thinking
        if model_thinking or plan_summary:
            # 思考全文/计划摘要同时存到 run.agent_state：details 每次重建都会
            # 从 run 取，避免任务结束时的覆写把思考内容冲掉。
            with self._lock:
                if not isinstance(run.agent_state, dict):
                    run.agent_state = {}
                run.agent_state["_reasoning_text"] = (model_thinking or plan_reasoning)[:12000]
                run.agent_state["_plan_summary"] = plan_summary[:4000]
            # 规划推理全文归档到 run 上（此前写在消息 details 里会被
            # _begin_execution_trace 的 process_trace 覆盖而丢失）：
            # thought_trace 存全文供回看，process_trace 首条进时间线折叠块。
            self._append_thought(run, "模型思考", model_thinking or plan_reasoning)
            run.process_trace.insert(
                0,
                {
                    "timestamp": time.time(),
                    "title": "模型思考",
                    "body": (model_thinking or plan_reasoning)[:8000],
                    "status": "completed",
                    "kind": "reasoning",
                },
            )
            self._update_assistant_message(
                run.run_id,
                "正在执行计划...",
                "running",
                {
                    "mode": "execute",
                    "phase": "planning",
                    # 思考块放模型思考全文；计划步骤列表单独给前端
                    "reasoning_text": (model_thinking or plan_reasoning)[:8000],
                    "plan_summary": plan_summary[:4000],
                },
                persist=False,
            )
        if execute:
            # 先判定执行策略再写说明：否则会先写"适合一次性规划执行"、随后又
            # 改判 Agent Loop，两条消息自相矛盾（用户可见）。
            needs_agent_loop = self._plan_requires_agent_loop(run.plan)
            if needs_agent_loop:
                # 提前标记 route：执行中提交的补充指令（steer）只有 Agent Loop
                # 能消费，尽早标上，规划刚结束就能接收。
                run.route_strategy = "agent_loop"
                # 措辞要描述实际行为：执行仍然是"按计划顺序推进"，只有观察结果
                # 偏离计划预期时才转回 LLM 重新决策。以前写成"逐步执行并按结果
                # 调整"，操作员看到计划被顺序跑完会觉得系统在说一套做一套。
                self._begin_execution_trace(
                    run,
                    "计划里有依赖观察结果的步骤（检测/确认之后才决定下一步）："
                    "执行时按计划顺序推进，一旦观察结果偏离计划预期，就转为重新决策再继续。",
                )
            else:
                self._begin_execution_trace(
                    run,
                    "该任务可用一次性计划完成：先生成完整工具序列，再由 runtime 逐步执行、回读和校验。",
                )
            # skill guidance is injected into the planner prompt as background
            # knowledge — it is NOT a tool call, so it must not be displayed
            # as if a skill had been invoked
            # 执行策略只看计划结构，不看操作员的措辞：关键词匹配会让同一个任务
            # 因为换个说法就拿到完全不同的预算和行为（"靠近它确认一下"不含任何
            # 追踪关键词时只给 10 步，抵近到一半被步数上限截断）。
            #   - 是否逐步反应：由计划里"观察步骤 + 后续动作"的依赖关系决定
            #     （_plan_requires_agent_loop，结构化判断，不涉及自然语言分类）；
            #   - 步数预算：Agent Loop 的每一步都可能需要一次观察-纠正，统一给
            #     28 步上限；固定序列任务按计划长度留 3 倍余量，下限 16 步；
            #   - 是否需要持续观察-响应（keep_reacting）：由规划器在计划里声明，
            #     "没有终止条件的任务"是语义判断，词表覆盖不了。
            plan_step_count = len(
                [
                    step
                    for step in (run.plan.steps if run.plan else [])
                    if step.tool and step.tool != "memory_store"
                ]
            )
            max_steps = 28 if needs_agent_loop else max(16, min(28, plan_step_count * 3))
            keep_reacting = bool(getattr(run.plan, "keep_reacting", False)) if run.plan else False
            if needs_agent_loop:
                # The plan depends on mid-execution observations (photo ->
                # decide -> move) or the planner declared agent_loop: a fixed
                # sequence would fail, so ReAct runs as the primary path.
                run.route_strategy = "agent_loop"
                self._append_event(
                    "info",
                    "planner",
                    "计划依赖中间观察，转入 Agent Loop 逐步执行",
                    {
                        "run_id": run.run_id,
                        "execution_mode": run.plan.execution_mode if run.plan else "auto",
                        "plan_steps": plan_step_count,
                        "max_steps": max_steps,
                        "keep_reacting": keep_reacting,
                    },
                )
                self._run_correction_loop(
                    run,
                    capabilities=capabilities,
                    tool_runtime=tool_runtime,
                    model_id=model_id,
                    attachments=attachments or [],
                    label="Agent Loop",
                    command_override=self._agent_loop_primary_command(run),
                    announce=False,
                    max_steps=max_steps,
                    reactive=True,
                    keep_reacting=keep_reacting,
                )
            else:
                self._run_plan(run, finalize=False, remember=False)
                while self._should_enter_correction_loop(run):
                    run.correction_attempts += 1
                    self._append_event(
                        "warning",
                        "planner",
                        f"计划执行失败，进入 Agent Loop 纠错（{run.correction_attempts}/{CORRECTION_ATTEMPTS_MAX}）",
                        {"run_id": run.run_id, "failure_reason": run.failure_reason},
                    )
                    self._run_correction_loop(
                        run,
                        capabilities=capabilities,
                        tool_runtime=tool_runtime,
                        model_id=model_id,
                        attachments=attachments or [],
                    )
                total = len(run.plan.steps if run.plan else [])
                ok_count = sum(1 for step in (run.plan.steps if run.plan else []) if step.status == "completed")
                self._remember_plan_run(run, total=max(1, total), ok_count=ok_count)
            self._finalize_assistant_response(run)
        else:
            self._simulate_plan(run)
            self._finalize_assistant_response(run)

    def _should_enter_correction_loop(self, run: RunState) -> bool:
        if not run.execute or self._is_run_cancelled(run.run_id):
            return False
        if run.route_strategy != "plan_execute":
            return False
        if run.correction_attempts >= CORRECTION_ATTEMPTS_MAX:
            return False
        reason = (run.failure_reason or "").lower()
        if any(term in reason for term in ["operator", "approval", "emergency stop", "急停", "操作员"]):
            return False
        # Link-level failures cannot be fixed by re-deciding the plan.
        if any(term in reason for term in CONNECTION_FAILURE_TERMS):
            return False
        return run.status in {"failed", "blocked"} or run.verification.get("level") == "failed"

    def _run_correction_loop(
        self,
        run: RunState,
        *,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        model_id: str,
        attachments: list[dict[str, Any]],
        label: str = "纠错 Loop",
        command_override: str | None = None,
        announce: bool = True,
        max_steps: int = 10,
        reactive: bool = False,
        keep_reacting: bool = False,
    ) -> None:
        if announce:
            self._append_process(
                run,
                label,
                "一次性计划未完全达成，进入 Agent Loop 回读当前状态并选择修正动作。"
                if label == "纠错 Loop"
                else "任务需要观察-响应循环，进入 Agent Loop 逐步执行。",
                status="running",
                kind="system",
            )
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        correction_command = command_override or self._correction_command(run)
        loop = self.agent_loop.run(
            run_id=run.run_id,
            command=correction_command,
            capabilities=capabilities,
            tool_cards=tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            initial_plan=run.plan,
            model_id=model_id or None,
            # 搜索/识别类任务通常需要 10 步上下；追踪类由调用方传入更大预算
            max_steps=max(1, int(max_steps)),
            initial_plan_cursor=self._plan_completed_prefix(run),
            execute=True,
            attachments=attachments,
            require_llm=True,
            conversation_context=self._recent_chat_context(),
            reactive=bool(reactive),
            keep_reacting=bool(keep_reacting),
        )
        correction_plan = self._plan_from_loop_state(loop)
        if run.plan:
            offset = len(run.plan.steps)
            for index, step in enumerate(correction_plan.steps, 1):
                step.id = f"s{offset + index:02d}"
                run.plan.steps.append(step)
        else:
            run.plan = correction_plan
        run.loop_state = loop.to_dict()
        run.summary = loop.summary or run.summary
        run.status = loop.status if loop.status in {"completed", "failed", "blocked"} else "completed"
        # 目标未达成兜底：原计划中的运动步骤（起飞/移动/降落等）失败且纠错
        # 阶段没有留下同工具的成功记录时，一律不得标记 completed——LLM 有时会
        # 在目标动作仍缺失时误判"任务目标已满足"，此处用机器检查拦截。
        failed_motion = [
            step.tool for step in (run.plan.steps or [])
            if step.status == "failed" and step.tool in MOTION_TOOLS
        ]
        recovered_motion = {
            step.tool for step in (correction_plan.steps or [])
            if step.status == "completed" and step.tool in failed_motion
        }
        unresolved_motion = [tool for tool in failed_motion if tool not in recovered_motion]
        if run.status == "completed" and unresolved_motion:
            run.status = "failed"
            run.verification = {
                "level": "failed",
                "summary": (
                    f"任务目标未完全达成：以下动作在计划执行中失败，"
                    f"且纠错阶段未成功补做：{', '.join(dict.fromkeys(unresolved_motion))}"
                ),
            }
        # a recovered earlier failure must never leak into a completed run:
        # the frontend renders the error badge from failure_reason
        run.failure_reason = "" if run.status == "completed" else (run.failure_reason or loop.failure_reason)
        run.finished_at = loop.finished_at or time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.verification = self._verify_run_outcome(run)
        # Loop-level task-contract verification (machine-checked completion
        # criteria) feeds the same failed-verification gate as the plan path.
        if loop.verification_status == "failed" and run.verification.get("level") != "failed":
            run.verification = {
                "level": "failed",
                "summary": f"完成判据未满足：{loop.summary or loop.failure_reason or '任务目标未达成'}",
            }
        if not run.verification:
            # 任务没有声明可校验的成功判据：把"正在回读…"那行收尾，否则它会
            # 永远停在运行中（前端一直转圈）。
            for item in reversed(run.process_trace):
                if item.get("kind") == "verify" and item.get("status") == "running":
                    item["status"] = "completed"
                    item["body"] = "该任务未声明可校验的成功判据，已回读最终状态。"
                    break
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.failure_reason = run.verification.get("summary", "纠错后任务校验仍未通过")
        run.phase = run.status if run.status in {"completed", "failed", "blocked"} else "completed"
        self._append_process(
            run,
            "模型总结" if loop.summary else label,
            loop.summary or run.failure_reason or f"{label} 已结束。",
            status="completed" if run.status == "completed" else "failed",
            kind="reasoning",
        )
        self._publish_run_update(run)

    def _plan_from_loop_state(self, loop: LoopState, planned: bool = False) -> MissionPlan:
        """Rebuild a plan from the loop's audit trail.

        Decisions and results are paired by tool name with consumption order,
        so corrective decisions and batch results are never lost from the
        rebuilt plan; leftover results (e.g. batch extras) become their own
        steps at the end.
        """
        steps: list[MissionStep] = []
        consumed: set[int] = set()

        def status_for(result: Any) -> str:
            if result is None:
                return "pending"
            return "planned" if planned and result.ok else ("completed" if result.ok else "failed")

        for decision in loop.decisions:
            if decision.is_complete or not decision.action:
                continue
            result = None
            for ridx, row in enumerate(loop.results):
                if ridx in consumed:
                    continue
                if row.tool == decision.action:
                    result = row
                    consumed.add(ridx)
                    break
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=decision.reason or decision.action,
                    tool=decision.action,
                    params=dict(decision.params or {}),
                    layer="agent_loop",
                    status=status_for(result),
                    result=result.data if result else None,
                )
            )
        for ridx, row in enumerate(loop.results):
            if ridx in consumed:
                continue
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=row.tool,
                    tool=row.tool,
                    params=dict(row.params or {}),
                    layer="agent_loop",
                    status="completed" if row.ok else "failed",
                    result=row.data,
                )
            )
        return MissionPlan(
            run_id=loop.run_id,
            command=loop.command,
            intent="agent_loop",
            summary=loop.summary or "Agent Loop task",
            steps=steps,
            planner_source="agent_loop",
            reasoning="Loop decisions are stored in loop_state.decisions.",
            risk_notes=[loop.failure_reason] if loop.failure_reason else [],
        )

    def _planner_tool_cards(
        self,
        command: str,
        atomic_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
        memory_snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a small LLM-facing action surface.

        The model should reason over skills first. Atomic tools remain visible
        only when they are safe/read-only, needed for visual grounding, or the
        active backend has no suitable skill for the requested capability.
        """
        skill_names: set[str] = set()
        atomic_by_name = {
            str(card.get("name")): card
            for card in atomic_cards
            if isinstance(card, dict) and card.get("name")
        }
        allowed_atomic = self._allowed_planner_atomic_tools(command, skill_names, capabilities)
        cards: list[dict[str, Any]] = []
        for name in sorted(allowed_atomic):
            card = atomic_by_name.get(name)
            if card:
                cards.append(card)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            name = str(card.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(card)
        # 技能卡片必须出现在规划器的动作面里：这个方法的注释写着"先考虑技能"，
        # 但实现里技能只被用来过滤原子工具、卡片本身从未返回——于是走
        # plan-execute 的任务（绝大多数）里模型根本看不到技能，永远不可能激活
        # 它们（实测从未出现过一次 skill 调用）。放在最前面，让模型先看到。
        skill_cards: list[dict[str, Any]] = []
        loader = getattr(self.skills, "usable_doc_cards", None)
        if callable(loader):
            try:
                skill_cards = [
                    {
                        "name": str(card.get("name") or ""),
                        # 行动导向的一句话：模型是把卡片当"可执行动作"来挑的，
                        # 直接把整段 description 贴上去它会当成背景知识而不去激活。
                        "purpose": (
                            "先激活这个技能，取回该类任务的完整操作步骤（含前置条件、"
                            "坐标系与安全规则），再按步骤调用工具执行。适用场景："
                            + str(card.get("description") or "").strip()[:220]
                        ),
                        "kind": "skill",
                    }
                    for card in (loader(capabilities) or [])
                    if isinstance(card, dict) and card.get("name")
                ]
            except Exception:
                skill_cards = []
        # Agent-level cards (memory/subtask) always keep a slot.
        agent_names = {"memory_recall", "memory_remember", "agent_subtask"}
        agent_cards = [card for card in deduped if card.get("name") in agent_names]
        regular = [card for card in deduped if card.get("name") not in agent_names]
        budget = max(0, 18 - len(agent_cards) - len(skill_cards))
        return (skill_cards + regular[:budget] + agent_cards)[:20]

    def _allowed_planner_atomic_tools(
        self,
        command: str,
        skill_names: set[str],
        capabilities: dict[str, Any],
    ) -> set[str]:
        text = (command or "").lower()
        allowed = {"drone_connect", "drone_get_status"}
        visual_terms = (
            "camera", "image", "photo", "see", "look", "detect", "search", "find", "target",
            "摄像头", "画面", "图像", "图片", "照片", "拍照", "看到", "看一下", "看看", "识别", "检测", "搜索", "寻找", "目标",
        )
        mission_terms = ("mission", "waypoint", "航点", "航线", "任务", "上传", "下载", "进度", "清空", "启动")
        landing_terms = ("land", "rtl", "return", "降落", "返航", "返回")
        hover_terms = ("hover", "hold", "pause", "悬停", "保持", "暂停")
        path_terms = ("path", "route", "orbit", "circle", "scan", "patrol", "绕圈", "转圈", "盘旋", "扫描", "巡航", "巡检", "半径")

        if any(term in text for term in visual_terms):
            allowed.update({
                "airsim_take_photo",
                "inspect_current_frame",
                "airsim_get_depth_map",
                "airsim_task_status",
                "airsim_task_cancel",
            })
            if "skill:visual_observe" not in skill_names:
                allowed.update({"airsim_take_photo", "inspect_current_frame"})

        if any(term in text for term in mission_terms):
            allowed.update({
                "drone_download_mission",
                "drone_get_mission_progress",
                "drone_upload_mission",
                "drone_start_mission",
                "drone_clear_mission",
            })

        if any(term in text for term in landing_terms):
            allowed.add("drone_land")
        if any(term in text for term in hover_terms):
            allowed.add("drone_hover")
        if any(term in text for term in path_terms):
            allowed.add("drone_fly_path")
        formation_terms = (
            "formation", "swarm", "编队", "队形", "coverage", "覆盖", "区域扫描", "网格扫描", "分区扫描",
        )
        if any(term in text for term in formation_terms):
            allowed.add("formation_command")

        if "skill:navigation" not in skill_names:
            allowed.update({"drone_arm", "drone_takeoff", "drone_fly_to", "drone_move_relative", "drone_hover"})
        if "skill:return_home" not in skill_names and capabilities.get("flight_control"):
            allowed.update({"drone_fly_to", "drone_land"})
        return allowed

    def _simulate_plan(self, run: RunState) -> None:
        total = max(1, len(run.plan.steps if run.plan else []))
        if not run.plan:
            return
        run.phase = "planning"
        for index, step in enumerate(run.plan.steps, 1):
            result = self.tools.execute(step.tool, step.params, dry_run=True)
            step.status = "planned" if result.ok else "blocked"
            step.result = result.data
            step.safety = result.safety
            run.progress = index / total * 100
        run.status = "planned"
        run.phase = "planned"
        run.finished_at = time.time()

    def _run_plan(self, run: RunState, finalize: bool = True, remember: bool = True) -> None:
        if not run.plan:
            return
        run.status = "running"
        run.phase = "executing"
        total = max(1, len(run.plan.steps))
        ok_count = 0
        preapproved = self._preapprove_first_high_risk_tool(run)
        if not (preapproved and preapproved.get("approved") is False):
            self._capture_start_telemetry(run)

        for index, step in enumerate(run.plan.steps, 1):
            if preapproved and preapproved.get("approved") is False:
                break
            # 暂停轮询必须能被取消打断：暂停中取消时，_cancel_active_work 会
            # resume 且这里也会因取消而退出——否则 worker 会永远在 0.2 秒轮询里
            # 打转并占着执行槽（之后每个任务都要等满 60 秒再报"旧任务未停止"）。
            while (
                self.supervisor.should_pause()
                and not self.supervisor.is_emergency_stopped()
                and not self._is_run_cancelled(run.run_id)
            ):
                run.status = "paused"
                run.phase = "paused"
                run.current_step = step.id
                time.sleep(0.2)

            abort_reason = self._should_abort_run(run)
            if abort_reason:
                run.status = "blocked" if "emergency" in abort_reason else "cancelled"
                run.phase = run.status
                run.failure_reason = abort_reason
                run.finished_at = time.time()
                self._publish_run_update(run)
                self._append_event(
                    "warning" if run.status == "cancelled" else "danger",
                    "system",
                    f"固定序列执行已中止：{abort_reason}",
                    {"run_id": run.run_id, "step": step.id, "completed_steps": index - 1},
                )
                break

            run.status = "running"
            run.phase = "executing"
            run.current_step = step.id
            step.status = "running"
            self._publish_run_update(run)
            self._append_event(
                "info",
                step.layer,
                f"执行步骤 {step.id}: {step.label}",
                {"tool": step.tool, "params": step.params},
            )
            self._update_execution_trace_for_step(run, step, index, total)

            result = self._maybe_skip_idempotent_step(step)
            if result is None:
                already_approved = bool(
                    preapproved
                    and preapproved.get("approved") is True
                    and preapproved.get("tool") == step.tool
                    and preapproved.get("params") == dict(step.params)
                )
                result = self._execute_agent_tool(
                    step.tool,
                    step.params,
                    dry_run=False,
                    run=run,
                    approval_already_granted=already_approved,
                )
            step.result = result.data
            step.safety = result.safety
            step.status = "completed" if result.ok else "failed"
            self._record_task_tool_result(run, step, result)
            self.memory.remember_tool_call(step.tool, result.ok)
            if not run.start_position_recorded:
                self._remember_task_start(run, result.data)
            self._remember_position_from_payload(result.data, source=step.tool)
            run.progress = index / total * 100
            self._publish_run_update(run)
            self._update_execution_trace_after_step(run, step, result.ok)

            if result.ok:
                ok_count += 1
                self._append_event("info", "tool", f"{step.tool} 完成", result.to_dict())
            else:
                run.status = "failed"
                run.phase = "failed"
                run.failure_reason = result.data.get("message", f"{step.tool} failed")
                self._publish_run_update(run)
                self._append_event("danger", "tool", f"{step.tool} 失败", result.to_dict())
                if step.tool not in {"drone_land", "drone_hover"}:
                    self.tools.execute("drone_hover", {}, dry_run=False)
                break

            run.progress = index / total * 100

        if run.status == "running":
            run.status = "completed"
            run.phase = "verifying"
            run.progress = 100.0

        run.finished_at = time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.agent_state = self._agent_state_context()
        self._append_thought(run, "校验结果", "正在回读最终状态并核对任务目标。", status="running")
        self._append_process(run, "回读与校验", "正在回读最终状态并核对任务目标。", status="running", kind="verify")
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        self._publish_run_update(run)
        run.verification = self._verify_run_outcome(run)
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.phase = "failed"
            run.failure_reason = run.verification.get("summary", "任务后状态校验失败")
            self._append_thought(run, "校验未通过", run.failure_reason, status="failed")
            self._upsert_verify_row(run, run.failure_reason, status="failed")
            self._append_event("warning", "verifier", "任务后状态校验失败", run.verification)
        elif run.verification:
            self._append_thought(run, "校验完成", str(run.verification.get("summary") or ""), status="completed")
            self._upsert_verify_row(run, self._verification_body(run.verification))
            self._append_event("info", "verifier", "任务后状态校验完成", run.verification)
        if run.status == "completed":
            run.phase = "completed"
        if run.status == "completed":
            self._append_event("info", "memory", "任务闭环完成，写入经验")
        if remember:
            self._remember_plan_run(run, total=total, ok_count=ok_count)
        if finalize:
            self._finalize_assistant_response(run)

    def _remember_plan_run(self, run: RunState, total: int, ok_count: int) -> None:
        self.memory.remember_mission(
            {
                "run_id": run.run_id,
                "command": run.command,
                "intent": run.intent,
                "status": run.status,
                "summary": run.summary,
                "duration_sec": round((run.finished_at or time.time()) - run.started_at, 2),
                "steps_total": total,
                "steps_ok": ok_count,
                "failure_reason": run.failure_reason,
                "route_strategy": run.route_strategy,
                "tool_sequence": [step.tool for step in (run.plan.steps if run.plan else [])],
                "verification_status": run.verification.get("status", ""),
            }
        )

    def _maybe_skip_idempotent_step(self, step: MissionStep) -> ToolCallResult | None:
        """Skip already-satisfied setup steps in deterministic plans."""
        runtime = self.tools.status_snapshot()
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        message = ""
        if step.tool == "drone_connect" and connected:
            message = "already connected"
        elif step.tool == "drone_arm" and bool(drone.get("armed")):
            message = "already armed"
        elif step.tool == "drone_takeoff":
            takeoff_note = self._takeoff_skip_note(drone, step.params)
            if not takeoff_note:
                return None
            message = takeoff_note
        else:
            return None

        now = time.time()
        return ToolCallResult(
            tool=step.tool,
            params=dict(step.params),
            ok=True,
            data={
                "status": "ok",
                # 让"读了状态并据此跳过"这件事在时间线上看得见：操作员此前完全
                # 看不出计划里的解锁/起飞是因为"飞机已经满足"才没执行。
                "message": (
                    f"依据当前状态跳过重复步骤：{step.tool}（{message}）"
                    if step.tool != "drone_connect"
                    else f"已连接，跳过重复的连接步骤（{message}）"
                ),
                "skipped": True,
                "skip_reason": message,
                "drone": drone,
            },
            started_at=now,
            finished_at=now,
        )

    def _is_takeoff_already_satisfied(self, drone: dict[str, Any], params: dict[str, Any]) -> bool:
        return bool(self._takeoff_skip_note(drone, params))

    def _takeoff_skip_note(self, drone: dict[str, Any], params: dict[str, Any]) -> str:
        """应跳过起飞步骤时返回说明文本，否则返回 ""。

        比目标高度高出很多时仍然跳过——"爬升到 3m"这条命令作用在已经在 30m 的
        飞机上行为并不明确，不该拿它当下降用——但把高度偏差写进说明，避免"计划
        假设 3m、实际 30m"被静默带过（后续步骤都是按计划高度选的参数）。
        """
        if not isinstance(drone, dict):
            return ""
        altitude = self._vehicle_altitude_m(drone)
        try:
            target = abs(float(params.get("altitude", 3.0) or 3.0))
        except (TypeError, ValueError):
            target = 3.0
        if altitude is None:
            return "already flying (altitude unknown)" if drone.get("flying") else ""
        target = max(0.5, target)
        minimum = max(0.5, min(target * 0.85, target - 0.3 if target > 1.0 else target * 0.85))
        if not drone.get("flying") or altitude < minimum:
            return ""
        if altitude > target + max(1.0, target * 0.35):
            return f"already airborne at {altitude:.1f}m，计划高度 {target:.1f}m（偏差 {altitude - target:+.1f}m）"
        return "already airborne near requested altitude"

    def _vehicle_altitude_m(self, drone: dict[str, Any]) -> float | None:
        for key in ("altitude_m", "altitude"):
            value = drone.get(key)
            if value is None:
                continue
            try:
                return abs(float(value))
            except (TypeError, ValueError):
                pass
        pos = drone.get("position_ned")
        if isinstance(pos, dict) and pos.get("z") is not None:
            try:
                return abs(float(pos.get("z")))
            except (TypeError, ValueError):
                return None
        return None

    def _start_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.start_run(run, session_id=self._current_session_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _update_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.update_run(run)
        except Exception:
            pass

    @staticmethod
    def _plan_completed_prefix(run: RunState) -> int:
        """计划中连续已成功完成的步骤数（纠错重入时跳过它们）。"""
        steps = list(run.plan.steps if run.plan else [])
        count = 0
        for step in steps:
            if str(getattr(step, "status", "")) == "completed":
                count += 1
            else:
                break
        return count

    def _finalize_task_run(self, run: RunState) -> None:
        run_id = str(getattr(run, "run_id", "") or "")
        # 只关掉属于这个 run 的看门狗：不带 run_id 的调用会把另一个正在飞行的
        # 任务的包线看门狗一起关掉（计划预览与执行任务重叠时实测过）。
        try:
            self._stop_envelope_guard(run_id)
        except Exception:
            pass
        # 清共享状态前先确认"当前活跃任务还是不是我"：重叠的预览收尾时清掉的
        # 会是执行任务的补充指令与取消请求。_current 缺失视为"无人拥有"。
        lock = getattr(self, "_lock", None)
        current = None
        if lock is not None:
            with lock:
                current = getattr(self, "_current", None)
        owns_current = current is None or str(getattr(current, "run_id", "") or "") == run_id
        if owns_current:
            with lock:
                pending_steer = getattr(self, "_pending_steer", None)
                if pending_steer is not None:
                    # 任务已收尾，没有下一轮循环去消费补充指令了：留着只会泄漏给下一个任务
                    pending_steer.clear()
            # 取消旗标是"当前有取消请求"的瞬时状态：任务都已经收尾就必须清掉，
            # 否则飞行 stop_provider 会一直读到它——操作员随后点起飞会被
            # "takeoff interrupted by emergency stop / cancel" 立刻打断
            # （实测：飞机明明已经起飞，界面却报起飞失败）。
            clear_cancel = getattr(self, "_clear_cancel_state", None)
            if callable(clear_cancel):
                clear_cancel()
            else:
                cancel_event = getattr(self, "_cancel_requested", None)
                if cancel_event is not None:
                    cancel_event.clear()
        # 收尾终态按"是否危险"分流：
        #   failed  —— 异常收尾（任务失败、位置/链路不可信），受控降落是明确的安全终态；
        #   cancelled / blocked —— 操作员打断或到达步数上限，飞机链路与位置都正常，
        #   此时降落会让操作员失去一架还在空中、本来可以继续指挥的飞机（实测反馈：
        #   "我只是想让它靠近，结果它自己降落了"），改为保持悬停等待指令。
        try:
            if run is not None and getattr(run, "execute", False):
                if run.status == "failed":
                    self._attempt_failure_hover(run, run.failure_reason or run.status)
                elif run.status in {"cancelled", "blocked"}:
                    self._attempt_hold_position(run, run.failure_reason or run.status)
        except Exception:
            pass
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.finalize_run(run)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_event(self, event: RuntimeEvent, default_run_id: str = "") -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_event(event.to_dict(), default_run_id=default_run_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_tool_result(self, run: RunState, step: MissionStep, result: ToolCallResult) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_tool_result(run, step, result)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _capture_start_telemetry(self, run: RunState) -> None:
        if isinstance(run.start_telemetry, dict) and isinstance(run.start_telemetry.get("position_ned"), dict):
            self._remember_task_start(run, run.start_telemetry)
            return
        result = self.tools.execute("drone_get_status", {}, dry_run=False)
        if result.ok and isinstance(result.data, dict):
            run.start_telemetry = dict(result.data)
            self._remember_task_start(run, run.start_telemetry)
            self._append_event("info", "verifier", "任务起点状态已回读", result.to_dict())
        else:
            self._append_event("warning", "verifier", "任务起点状态回读失败", result.to_dict())

    def _verify_run_outcome(self, run: RunState) -> dict[str, Any]:
        if not run.execute or run.status == "planned":
            return {
                "status": "not_executed",
                "level": "info",
                "summary": "当前仅完成规划，未执行仿真动作，因此不进行任务后位置校验。",
            }

        start = run.start_telemetry or {}
        end = run.final_telemetry or {}
        start_pos = start.get("position_ned") if isinstance(start, dict) else None
        end_pos = end.get("position_ned") if isinstance(end, dict) else None
        checks: list[dict[str, Any]] = []

        result: dict[str, Any] = {
            "status": "unknown",
            "level": "info",
            "summary": "已回读任务后状态。",
            "start_position_ned": start_pos or {},
            "final_position_ned": end_pos or {},
            "final_flying": end.get("flying") if isinstance(end, dict) else None,
            "final_landed_state": end.get("landed_state") if isinstance(end, dict) else None,
            "checks": checks,
        }

        if isinstance(start_pos, dict) and isinstance(end_pos, dict):
            dx = self._float(end_pos.get("x")) - self._float(start_pos.get("x"))
            dy = self._float(end_pos.get("y")) - self._float(start_pos.get("y"))
            dz = self._float(end_pos.get("z")) - self._float(start_pos.get("z"))
            result["delta_ned"] = {"x": round(dx, 3), "y": round(dy, 3), "z": round(dz, 3)}
            result["delta_xy_m"] = round((dx * dx + dy * dy) ** 0.5, 3)
            result["delta_3d_m"] = round((dx * dx + dy * dy + dz * dz) ** 0.5, 3)

        lower = run.command.lower()
        steps = list(run.plan.steps if run.plan else [])
        wants_land = any(k in lower for k in ["land", "降落", "落地"])
        final_landing_expected = wants_land or any(step.tool == "drone_land" for step in steps)

        def later_has_position_goal(index: int) -> bool:
            later_tools = {step.tool for step in steps[index + 1 :]}
            return bool(later_tools & {"drone_fly_to", "drone_move_relative", "drone_upload_mission", "drone_start_mission"})

        def position_before(index: int):
            """Nearest known position recorded before this step (get_status /
            fly_to results carry position_ned) — the segment start for
            move_relative verification in multi-leg missions."""
            for j in range(index - 1, -1, -1):
                prior = steps[j]
                r = prior.result if isinstance(prior.result, dict) else {}
                p = r.get("position_ned")
                if isinstance(p, dict):
                    return p
            return start_pos

        def later_lands(index: int) -> bool:
            return any(step.tool == "drone_land" for step in steps[index + 1 :])

        if final_landing_expected:
            # 落地判据必须有地理证据：PX4 一进 LAND 模式 flying 就可能变 false，
            # 但飞机还在 2~3m 下沉。所以要求"高度接近地面"或 PX4 明确报 ON_GROUND
            # （landed_state=3），或已上锁（armed=false）。
            land_step_ok = any(
                step.tool == "drone_land" and step.status == "completed"
                for step in steps
            )
            end_z = None
            if isinstance(end_pos, dict):
                try:
                    end_z = abs(float(end_pos.get("z", 0.0) or 0.0))
                except (TypeError, ValueError):
                    end_z = None
            on_ground = (
                end.get("landed_state") in (1, "landed", "on_ground")  # MAV_LANDED_STATE_ON_GROUND=1
                or (end_z is not None and end_z < 0.6)
            )
            landed = bool(on_ground or (land_step_ok and end.get("armed") is False))
            checks.append({
                "name": "landed_state",
                "ok": landed,
                "severity": "hard",
                "expected": "高度接近地面 / PX4 ON_GROUND / 已上锁",
                "actual": {
                    "flying": end.get("flying"),
                    "armed": end.get("armed"),
                    "landed_state": end.get("landed_state"),
                    "altitude_m": round(end_z, 2) if end_z is not None else None,
                },
            })

        takeoff_steps = [step for step in steps if step.tool == "drone_takeoff"]
        if takeoff_steps and isinstance(end, dict) and not final_landing_expected:
            expected_altitude = max(self._float(step.params.get("altitude"), 3.0) for step in takeoff_steps)
            ned_altitude = abs(self._float(end_pos.get("z"))) if isinstance(end_pos, dict) else 0.0
            gps = end.get("gps") if isinstance(end.get("gps"), dict) else {}
            gps_altitude = abs(self._float(gps.get("alt"))) if isinstance(gps, dict) else 0.0
            actual_altitude = max(ned_altitude, gps_altitude)
            # PX4 悬停高度实测常与目标差 0.3~0.6m；旧容差 (0.85 倍 / -0.5m)
            # 会把正常悬停误判为失败。放宽到 0.8 倍 / -1.0m。
            min_altitude = max(0.5, expected_altitude * 0.8, expected_altitude - 1.0)
            flying = bool(end.get("flying") is True or actual_altitude >= 0.5)
            checks.append({
                "name": "takeoff_altitude",
                "ok": flying and actual_altitude >= min_altitude,
                "severity": "hard",
                "expected": {"altitude_m": round(expected_altitude, 3), "min_observed_m": round(min_altitude, 3)},
                "actual": {
                    "altitude_m": round(actual_altitude, 3),
                    "flying": end.get("flying"),
                    "armed": end.get("armed"),
                    "mode": end.get("mode"),
                },
            })

        for index, step in enumerate(steps):
            if step.tool == "drone_move_relative" and isinstance(start_pos, dict) and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                # Verify the leg displacement (from the nearest position
                # snapshot before this step to the run end), not the whole
                # mission displacement — multi-leg missions otherwise fail
                # because earlier legs inflate the total delta.
                seg_start = position_before(index) or start_pos
                seg_dx = self._float(end_pos.get("x")) - self._float(seg_start.get("x"))
                seg_dy = self._float(end_pos.get("y")) - self._float(seg_start.get("y"))
                expected_xy = (self._float(step.params.get("forward_m")) ** 2 + self._float(step.params.get("right_m")) ** 2) ** 0.5
                actual_xy = (seg_dx * seg_dx + seg_dy * seg_dy) ** 0.5
                tolerance = max(1.0, expected_xy * 0.45)
                error = abs(actual_xy - expected_xy)
                hard_tolerance = max(3.0, expected_xy * 1.5)
                ok = error <= tolerance
                checks.append({
                    "name": "relative_xy_distance",
                    "ok": ok,
                    "severity": "hard" if error > hard_tolerance else "soft",
                    "expected": round(expected_xy, 3),
                    "actual": round(actual_xy, 3),
                    "tolerance": round(tolerance, 3),
                    "error_m": round(error, 3),
                })
            elif step.tool == "drone_fly_to" and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                target = step.params
                dx = self._float(end_pos.get("x")) - self._float(target.get("x"))
                dy = self._float(end_pos.get("y")) - self._float(target.get("y"))
                dz = self._float(end_pos.get("z")) - self._float(target.get("z"))
                err_xy = (dx * dx + dy * dy) ** 0.5
                ignore_z = later_lands(index) or final_landing_expected
                err = err_xy if ignore_z else (dx * dx + dy * dy + dz * dz) ** 0.5
                tolerance = 2.0
                hard_tolerance = 6.0
                checks.append({
                    "name": "absolute_position_target",
                    "ok": err <= tolerance,
                    "severity": "hard" if err > hard_tolerance else "soft",
                    "expected": {"x": target.get("x"), "y": target.get("y"), "z": target.get("z")},
                    "actual": end_pos,
                    "error_m": round(err, 3),
                    "xy_error_m": round(err_xy, 3),
                    "z_ignored_after_land": ignore_z,
                    "tolerance": tolerance,
                })

        if isinstance(end, dict) and ("has_collided" in end or "collision" in end):
            collision_value = end.get("has_collided")
            if collision_value is None and isinstance(end.get("collision"), dict):
                collision_value = end["collision"].get("has_collided")
            checks.append({
                "name": "collision_free",
                "ok": collision_value is not True,
                "severity": "hard",
                "expected": False,
                "actual": collision_value,
            })

        wants_search = any(k in lower for k in ["search", "find", "locate", "搜索", "寻找", "查找", "目标"])
        search_steps = [step for step in steps if step.tool in {
            "skill:search", "airsim_search_target", "inspect_current_frame",
            "airsim_detect_objects", "airsim_take_photo", "perception_status",
        }]
        if wants_search or search_steps:
            search_statuses = {
                str(value).strip().lower()
                for step in search_steps
                for value in self._collect_field_values(step.result, "status")
            }
            found_markers = {"candidate_found", "target_found", "found", "locked",
                             "target_confirmed", "confirmed", "image_analyzed"}
            failed_markers = {"not_found", "target_not_confirmed", "failed", "cancelled", "canceled", "error", "blocked"}
            # 真实检出优先于文案状态：只要任一搜索步骤的结果里带了
            # detections/targets/primary（含 perception_status.snapshot），
            # 就认为目标确实被检出，而不是只看 status 字段是不是特定词。
            detected_target = False
            for step in search_steps:
                res = step.result
                if not isinstance(res, dict):
                    continue
                snap = res.get("snapshot") if isinstance(res.get("snapshot"), dict) else res
                if snap.get("primary") or snap.get("targets") or res.get("detections"):
                    detected_target = True
                    break
            search_ok = (
                bool(search_steps)
                and bool(search_statuses & (found_markers | {"completed", "ok"}))
                and not bool(search_statuses & failed_markers)
                and (detected_target or bool(search_statuses & (found_markers | {"not_found"})))
            )
            checks.append({
                "name": "target_search_outcome",
                "ok": search_ok,
                "severity": "hard",
                "expected": "检测/搜索得到明确结果（检出目标或如实报告未找到）",
                "actual": sorted(search_statuses) + ([f"detected={detected_target}"] if detected_target else []),
            })

        wants_track = any(k in lower for k in ["track", "follow", "追踪", "跟踪", "跟随"])
        tracking_steps = [step for step in steps if step.tool == "airsim_track_object"]
        if wants_track or tracking_steps:
            tracking_statuses = {
                str(value).strip().lower()
                for step in tracking_steps
                for value in self._collect_field_values(step.result, "status")
            }
            # 本架构没有独立追踪工具：追踪 = 感知轴持续锁定目标 + Agent 完成
            # 多次"检测/确认/抵近"循环。旧判据只认不存在的 airsim_track_object，
            # 会把成功的追踪任务硬判失败（假失败）。
            track_loop_tools = {"airsim_detect_objects", "inspect_current_frame",
                                "airsim_take_photo"}
            loop_ok_steps = [
                step for step in steps
                if step.tool in track_loop_tools and str(getattr(step, "status", "")) == "completed"
            ]
            # 目标是否仍被感知轴锁定（从最后一次 perception_status 结果取）
            locked = False
            for step in reversed(steps):
                if step.tool != "perception_status" or not isinstance(step.result, dict):
                    continue
                snap = step.result.get("snapshot") if isinstance(step.result.get("snapshot"), dict) else step.result
                if snap.get("primary") or snap.get("targets"):
                    locked = True
                break
            confirmed = any(
                str(value).strip().lower() in {"target_confirmed", "confirmed", "locked"}
                for step in steps
                for value in self._collect_field_values(step.result, "status")
            )
            # 本任务只要跑过一次视觉/检测步骤且目标处于锁定/确认状态，就算追踪
            # 达成：感知轴本身负责持续锁定，不需要 Agent 再堆很多重复检测步骤。
            detected_any = False
            for step in steps:
                res = step.result
                if isinstance(res, dict):
                    snap = res.get("snapshot") if isinstance(res.get("snapshot"), dict) else res
                    if snap.get("primary") or snap.get("targets") or res.get("detections"):
                        detected_any = True
                        break
            tracking_ok = (
                bool(tracking_steps) and "completed" in tracking_statuses
                and not bool(tracking_statuses & {"failed", "cancelled", "canceled", "error", "blocked"})
            ) or (len(loop_ok_steps) >= 1 and (locked or confirmed or detected_any))
            checks.append({
                "name": "tracking_outcome",
                "ok": tracking_ok,
                "severity": "hard",
                "expected": "追踪任务完成（目标被检出并锁定/确认）",
                "actual": sorted(tracking_statuses) or [
                    f"track_loop_steps={len(loop_ok_steps)}", f"locked={locked}",
                    f"confirmed={confirmed}", f"detected={detected_any}",
                ],
            })

        if checks:
            failed = [check for check in checks if not check.get("ok")]
            hard_failed = [check for check in failed if check.get("severity") == "hard"]
            result["status"] = "failed" if hard_failed else ("passed_with_warnings" if failed else "passed")
            result["level"] = "failed" if hard_failed else ("warning" if failed else "ok")
            if hard_failed:
                result["summary"] = "任务执行后关键状态未达到目标。"
            elif failed:
                result["summary"] = "任务执行后状态已回读，未发现阻断性失败。"
            else:
                result["summary"] = "任务执行后状态与目标一致。"
        else:
            result["status"] = "observed"
        return result

    def _float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _collect_field_values(self, value: Any, field_name: str) -> list[Any]:
        values: list[Any] = []
        if isinstance(value, dict):
            if field_name in value:
                values.append(value.get(field_name))
            for nested in value.values():
                values.extend(self._collect_field_values(nested, field_name))
        elif isinstance(value, list):
            for nested in value:
                values.extend(self._collect_field_values(nested, field_name))
        return values

    def _remember_task_start(self, run: RunState, telemetry: dict[str, Any] | None) -> None:
        if run.start_position_recorded or not isinstance(telemetry, dict):
            return
        position = telemetry.get("position_ned")
        if not isinstance(position, dict):
            return
        heading = telemetry.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_task_start(run.run_id, run.command, position, heading_float)
        self.memory.remember_position(position, heading_float, source="task_start")
        run.start_position_recorded = True

    def _remember_position_from_payload(self, payload: dict[str, Any] | None, source: str) -> None:
        if not isinstance(payload, dict):
            return
        position = payload.get("position_ned") or payload.get("target_position_ned")
        if not isinstance(position, dict):
            return
        heading = payload.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_position(position, heading_float, source=source)

    def _chat_readonly_tools(self) -> list[dict[str, Any]]:
        """Read-only query tools exposed to chat mode (function-calling
        schemas). The whitelist is the safety boundary: chat can pull live
        status data but can never arm/move/land a vehicle."""
        allowed = {"drone_get_status", "drone_list_vehicles"}
        schemas: list[dict[str, Any]] = []
        try:
            for spec in self.tools.list_tools():
                name = str(spec.get("name") or "")
                if name not in allowed:
                    continue
                schemas.append(
                    function_tool_schema(
                        name,
                        str(spec.get("description") or name),
                        tool_schema_from_spec(name, spec.get("parameters") or {}, {}),
                    )
                )
        except Exception:
            return []
        return schemas

    def _refresh_chat_state(self, agent_state: dict[str, Any]) -> dict[str, Any]:
        """Refresh the read-only vehicle state once before answering a chat
        question when the snapshot is busy or stale.

        Chat mode does not execute control tools, but it must not answer from
        fabricated/outdated numbers either — a single read-only status + list
        call gives the model real telemetry to reason about.
        """
        try:
            runtime = self.tools.status_snapshot()
        except Exception:
            return agent_state
        if not runtime.get("connected") or runtime.get("stale_connection"):
            return agent_state
        busy = bool(runtime.get("busy"))
        has_vehicle = bool((agent_state or {}).get("vehicle") or (runtime.get("vehicles")))
        if not busy and has_vehicle:
            return agent_state
        result = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
        if not result.ok:
            return agent_state
        try:
            fresh_runtime = self.tools.status_snapshot()
        except Exception:
            fresh_runtime = runtime
        fresh = self._agent_state_context(fresh_runtime)
        if fresh:
            agent_state = fresh
        return agent_state

    def _plan_reasoning_sink(self, run_id: str, command: str) -> Callable[[str], None]:
        """Throttled reasoning-token sink for streamed planning.

        Reasoning streams into the message's ``reasoning_text`` details field
        （前端思考块：默认折叠、标题行滚动最新一句、展开看全文——dsh 插件
        同款交互）。正文 content 不被推理占据，规划完成后直接呈现结果。"""
        buffer: list[str] = []
        emitted: list[str] = []
        last_flush: list[float] = [0.0]

        def flush() -> None:
            if not buffer:
                return
            text = "".join(buffer)
            buffer.clear()
            if not text:
                return
            emitted.append(text)
            full = self._strip_plan_json_draft("".join(emitted))
            sink.full_text = full  # type: ignore[attr-defined]
            self._append_event("info", "model_reasoning", text[-1500:], {"run_id": run_id, "command": command[:60]})
            # 规划期的流式显示交给前端（消息 details 里就有 reasoning_text）：
            # 后端在这里往 process_trace 追加块会落在错误位置（规划完成时完整思考
            # 是插到最前面的，两条并存会让思考块出现在工具行之后，实测操作员看到
            # "流式输出位置不对"）。
            self._update_assistant_message(
                run_id,
                "思考中…",
                "running",
                {"mode": "execute", "phase": "planning", "reasoning_text": full[-8000:]},
                persist=False,
            )

        def sink(token: str) -> None:
            buffer.append(token)
            now = time.time()
            if now - last_flush[0] >= 0.4:
                last_flush[0] = now
                flush()

        # attach the final flush so the wrapper can drain the tail
        sink.final_flush = flush  # type: ignore[attr-defined]
        sink.full_text = ""
        return sink

    def _is_conflicting(self, command: str) -> bool:
        lower = command.lower()
        if not command.strip():
            return True
        landish = any(k in lower for k in ["land", "降落", "落地"])
        takeoffish = any(k in lower for k in ["takeoff", "起飞", "升空"])
        if landish and takeoffish and len(lower) < 20:
            return True
        return False
