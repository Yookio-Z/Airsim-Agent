"""Event publishing, the message timeline and the state snapshot.

The UI-facing surface: append-only run events, SSE subscribers, the chat message
list with its trace rows (thoughts / process / tool lines), and the state()
snapshot the browser polls. Everything here is about *describing* what the
runtime is doing; nothing in it decides what to do. Moved verbatim out of
runtime.py.
"""

from __future__ import annotations

from .run_state import (
    ChatMessage,
    RunState,
    RuntimeEvent,
    reasoning_delta,
)
from .session_store import (
    _trim_session_message,
    trim_loop_state_payload,
)
from typing import Any
import json
import math
import queue
import time


class EventsMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _close_pending_reasoning(self, run: RunState, action: str = "") -> None:
        with self._lock:
            for item in reversed(run.process_trace):
                if item.get("kind") == "reasoning" and item.get("status") == "running":
                    item["status"] = "completed"
                    body = str(item.get("body") or "").strip()
                    if not body or body in self._PENDING_REASONING_PLACEHOLDERS:
                        item["body"] = (
                            f"本轮模型未输出思考文本，直接调用 {action}。" if action else "本轮模型未输出思考文本。"
                        )
                    break

    def _on_agent_event(self, level: str, source: str, message: str, data: dict[str, Any]) -> None:
        self._append_event(level, source, message, data)
        # ReAct 每步决策的推理（reasoning_content）追加进当前消息的
        # reasoning_text——前端思考块一个折叠块看全程思考
        if source == "model_reasoning" and self._current is not None:
            run = self._current
            # 每轮只展示"本轮新增"的思考。模型（尤其推理模型）常把整段推理连同
            # 上一轮内容一起再吐一遍，直接累加会让每一轮思考块都把以前的思考又
            # 显示一次。这里按"最长旧前缀"做增量裁剪，没有新内容就整轮跳过。
            new_text = str(message or "").strip()
            with self._lock:
                if not isinstance(run.agent_state, dict):
                    run.agent_state = {}
                seen = run.agent_state.setdefault("_seen_reasoning", [])
                delta = reasoning_delta(new_text, seen)
                if not delta:
                    return
                if new_text:
                    seen.append(new_text)
                    del seen[:-10]
                message = delta
            with self._lock:
                prev = str(run.agent_state.get("_reasoning_text") or "")
                run.agent_state["_reasoning_text"] = (prev + "\n" + message).strip()[:12000]
                # JSON 草稿跨多个事件分块到达，累积原文、组装时统一截断
                full = self._strip_plan_json_draft(run.agent_state["_reasoning_text"])
                target_message = next(
                    (m for m in reversed(self._messages) if m.run_id == run.run_id and m.role == "assistant"),
                    None,
                )
                # ReAct 每轮思考成为时间线上的独立条目（流式更新同一块），
                # 下一个工具/决策条目出现时再收尾——形成"思考→工具→思考"交错。
                # 每轮只放本轮新增的思考（用 round 累积器），否则每轮都显示
                # 同一段累计文本，看起来重复。
                last_item = run.process_trace[-1] if run.process_trace else None
                same_round = bool(
                    last_item is not None
                    and last_item.get("kind") == "reasoning"
                    and last_item.get("status") == "running"
                )
                prev_round = str(run.agent_state.get("_round_reasoning") or "") if same_round else ""
                round_text = (prev_round + "\n" + message).strip()
                run.agent_state["_round_reasoning"] = round_text[:6000]
                round_body = self._strip_plan_json_draft(round_text) or message
                if same_round:
                    last_item["body"] = self._compact_process_text(round_body)
                    last_item["timestamp"] = time.time()
                else:
                    run.process_trace.append({
                        "timestamp": time.time(),
                        "title": "模型思考",
                        "body": self._compact_process_text(round_body),
                        "status": "running",
                        "tool": "",
                        "params": {},
                        "kind": "reasoning",
                    })
                    run.process_trace = run.process_trace[-80:]
            if target_message is not None:
                det = target_message.details or {}
                self._update_assistant_message(
                    run.run_id,
                    target_message.content or "",
                    "running" if target_message.status == "running" else target_message.status,
                    {"mode": det.get("mode", "execute"), "phase": det.get("phase", "executing"),
                     "reasoning_text": full},
                    persist=False,
                )
        with self._lock:
            run_log = self._run_log
        if run_log is not None:
            kind = str(data.get("kind") or "")
            if kind == "loop.decision":
                run_log.write("loop.decision", data)
            elif kind == "tool.result":
                run_log.write("tool.result", data)
            elif kind == "observation":
                run_log.write("observation", data)
            elif kind == "replan":
                run_log.write("replan", data)
            elif kind == "verification":
                run_log.write("verification", data)
            elif kind == "async.poll":
                run_log.write(
                    "async.poll",
                    {
                        "task_id": str(data.get("task_id") or ""),
                        "status": str(data.get("status") or ""),
                    },
                )
        if source != "async_task":
            return
        with self._lock:
            run = self._current
            if not run:
                return
            run.agent_state = dict(run.agent_state or {})
            run.agent_state["active_operation"] = {
                "message": message,
                "task_id": str(data.get("task_id") or (data.get("data") or {}).get("task_id") or ""),
                "status": str((data.get("data") or {}).get("status") or data.get("status") or "running"),
                "updated_at": time.time(),
            }
        self._publish_run_update(run)

    def state(self) -> dict[str, Any]:
        with self._lock:
            orphan_changed = self._mark_orphan_running_messages_locked()
            events = [e.to_dict() for e in self._events[-80:]]
            messages = [self._message_public_dict(m) for m in self._messages[-80:]]
            current = self._run_public_dict(self._current) if self._current else None
            pending_approvals = [req.to_dict() for req in self._pending_approvals.values()]
        if orphan_changed:
            self._persist_current_session()

        tool_runtime = self.tools.status_snapshot()
        agent_state = self._agent_state_context(tool_runtime)
        gcs_state = self.gcs.state().to_dict()
        agent_skill_cards = self.agent_loop.skills.guidance_cards(
            "",
            gcs_state.get("capabilities") or {},
            memory=self.memory.snapshot(),
        )

        return {
            "runtime": {
                "status": current["status"] if current else "idle",
                "time": time.time(),
            },
            "supervisor": self.supervisor.get_status(),
            "tool_runtime": tool_runtime,
            "agent_state": agent_state,
            "gcs": gcs_state,
            "agent_skills": agent_skill_cards,
            "llm": self.planner.status(),
            "current_run": current,
            "messages": messages,
            "events": events,
            "memory": self._memory_state(),
            "task_runs": self.task_runs.snapshot(),
            "tools": self.tools.list_tools(),
            "sessions": self.list_sessions(),
            "current_session": self._get_current_session_summary(),
            # P5: pending high-risk approvals (real vehicle only)
            "pending_approvals": pending_approvals,
        }

    def telemetry_state(self) -> dict[str, Any]:
        """Return the lightweight frame used by the flight HUD and map."""
        with self._lock:
            current = self._run_public_dict(self._current) if self._current else None
        return {
            "ok": True,
            "runtime": {
                "status": current["status"] if current else "idle",
                "time": time.time(),
            },
            "supervisor": self.supervisor.get_status(),
            "tool_runtime": self.tools.status_snapshot(),
            "current_run": current,
            "llm": self.planner.status(),
        }

    def _memory_state(self) -> dict[str, Any]:
        memory = self.memory.snapshot()
        with self._lock:
            message_count = len(self._messages)
        memory["conversation"] = {
            "session_id": self._current_session_id,
            "messages_saved": message_count,
            **self._conversation_context_usage(),
        }
        memory["scope"] = {
            "conversation": "per_session",
            "working_state": "global_runtime",
            "missions_lessons_risks": "per_session",
            "task_runs": "persistent_replay",
            "events": "process_only",
        }
        memory["task_runs"] = self.task_runs.snapshot(limit=6)
        return memory

    def _conversation_context_usage(self) -> dict[str, Any]:
        model = self.planner.registry.get_default() or {}
        public = self.planner.registry._public_model(model) if model else {}
        context_window = int(public.get("context_window") or 64_000)
        context = self._recent_chat_context(limit=None)
        estimated_tokens = sum(
            max(1, math.ceil(len(str(item.get("content") or "")) / 4))
            for item in context
        )
        return {
            "messages_sent_to_model": len(context),
            "session_message_limit": None,
            "history_policy": "full_session_saved_recent_context_selected_by_token_budget",
            "estimated_context_tokens": estimated_tokens,
            "context_window": context_window,
            "context_percent": round(min(100.0, estimated_tokens / max(1, context_window) * 100.0), 2),
            "model_id": str(public.get("id") or ""),
        }

    def _get_current_session_summary(self) -> dict[str, Any] | None:
        if not self._current_session_id:
            return None
        path = self._session_path(self._current_session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "id": data.get("id", self._current_session_id),
                "name": data.get("name", "未命名对话"),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(data.get("messages", [])),
            }
        except Exception:
            return None

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=300)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _append_thought(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
    ) -> None:
        # 时间线是 UI 线程与 worker 线程共享的可变列表，而 state() 是在锁内序列化
        # 它的：写入端以前不加锁，等于锁只保护了一半，前端可能读到正在被追加/
        # 截断的列表。锁是可重入的，已在锁内的调用方不受影响。
        with self._lock:
            run.thought_trace.append(
                {
                    "timestamp": time.time(),
                    "title": title,
                    "body": body,
                    "status": status,
                }
            )

    def _append_process(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
        tool: str = "",
        params: dict[str, Any] | None = None,
        kind: str = "",
    ) -> None:
        # 见 _append_thought：时间线写入必须与 state() 的序列化共用同一把锁，
        # 否则前端可能读到正在被追加/改写的条目。锁可重入，已在锁内的调用方
        # 不受影响。
        with self._lock:
            self._append_process_locked(run, title, body, status, tool, params, kind)

    def _append_process_locked(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
        tool: str = "",
        params: dict[str, Any] | None = None,
        kind: str = "",
    ) -> None:
        body = self._compact_process_text(body)
        item_kind = kind or ("tool" if tool else "reasoning")
        # 工具/步骤条目出现时，收掉前面仍在流式的思考块（形成思考→工具→思考）
        if item_kind != "reasoning":
            for it in reversed(run.process_trace):
                if it.get("kind") == "reasoning" and it.get("status") == "running":
                    it["status"] = "completed"
                    break
        if status in {"running", "completed", "failed", "blocked"}:
            for item in reversed(run.process_trace):
                same_item = item.get("tool") == tool if tool else item.get("title") == title
                if same_item and item.get("status") == "running":
                    # 计划步骤的结果回填时保持"执行计划"标签，不要变成"工具"，
                    # 这样前端能区分"模型主动调用"与"按计划落地"。
                    if item.get("kind") == "plan_step" and item_kind == "tool":
                        item_kind = "plan_step"
                    item.update(
                        {
                            "timestamp": time.time(),
                            "title": title,
                            "body": body,
                            "status": status,
                            "params": dict(params or {}),
                            "kind": item_kind,
                        }
                    )
                    return
        run.process_trace.append(
            {
                "timestamp": time.time(),
                "title": title,
                "body": body,
                "status": status,
                "tool": tool,
                "params": dict(params or {}),
                "kind": item_kind,
            }
        )
        run.process_trace = run.process_trace[-80:]

    @staticmethod
    def _compact_process_text(text: str, limit: int = 6000) -> str:
        value = str(text or "")
        if len(value) <= limit:
            return value
        return "...\n" + value[-limit:]

    def _frontend_render_grace(self, seconds: float = 0.15) -> None:
        with self._lock:
            has_subscribers = bool(self._subscribers)
        if has_subscribers:
            time.sleep(max(0.0, seconds))

    def _append_message(
        self,
        role: str,
        content: str,
        run_id: str = "",
        status: str = "complete",
        details: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        now = time.time()
        if role == "assistant" and run_id:
            updated_message: ChatMessage | None = None
            updated_payload: dict[str, Any] | None = None
            with self._lock:
                for existing in reversed(self._messages):
                    if existing.role == "assistant" and existing.run_id == run_id:
                        existing.content = content
                        existing.status = status
                        existing.details = details or existing.details
                        existing.updated_at = now
                        updated_message = existing
                        updated_payload = self._message_public_dict(existing)
                        self._dedupe_assistant_run_messages_locked(run_id, existing.id)
                        break
            if updated_message and updated_payload:
                self._publish("message_update", updated_payload)
                self._persist_current_session()
                return updated_message
        message = ChatMessage(
            id=f"msg_{int(now * 1000)}_{len(self._messages) + 1}",
            role=role,
            content=content,
            attachments=list(attachments or []),
            run_id=run_id,
            status=status,
            details=details or {},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._messages.append(message)
        self._publish("message_create", self._message_public_dict(message))
        self._persist_current_session()
        return message

    def _update_assistant_message(
        self,
        run_id: str,
        content: str,
        status: str,
        details: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        updated = None
        with self._lock:
            # 优先按 run_id 精确匹配
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    # a cancelled run's worker thread may still emit one last
                    # progress update; writing "running" back over the
                    # interrupted marker makes the orphan sweep flag it as an
                    # error later ("任务进程已中断") — freeze it instead
                    if status == "running" and run_id in self._cancelled_request_ids:
                        return
                    message.content = content
                    message.status = status
                    message.details = {**(message.details or {}), **(details or {})}
                    message.updated_at = time.time()
                    updated = self._message_public_dict(message)
                    self._dedupe_assistant_run_messages_locked(run_id, message.id)
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not updated:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        if status == "running" and run_id in self._cancelled_request_ids:
                            return
                        message.content = content
                        message.status = status
                        message.run_id = run_id
                        message.details = {**(message.details or {}), **(details or {})}
                        message.updated_at = time.time()
                        updated = self._message_public_dict(message)
                        self._dedupe_assistant_run_messages_locked(run_id, message.id)
                        break
        if updated:
            self._publish("message_update", updated)
            # persist=False 时跳过磁盘写入，避免 reasoning token 逐个触发全量 IO
            if persist:
                self._persist_current_session()
            return
        self._append_message("assistant", content, run_id=run_id, status=status, details=details)

    def _dedupe_assistant_run_messages_locked(self, run_id: str, keep_id: str) -> bool:
        if not run_id:
            return False
        before = len(self._messages)
        self._messages = [
            message
            for message in self._messages
            if not (
                message.role == "assistant"
                and message.run_id == run_id
                and message.id != keep_id
            )
        ]
        return len(self._messages) != before

    def _mark_orphan_running_messages_locked(self) -> bool:
        active_run_id = self._current.run_id if self._current else ""
        live_statuses = {"running", "queued", "planned", "responding", "awaiting_approval"}
        now = time.time()
        startup_grace_sec = 30.0
        changed = False
        seen_assistant_runs: set[str] = set()
        for message in list(reversed(self._messages)):
            if message.role != "assistant" or not message.run_id:
                continue
            if message.run_id in seen_assistant_runs:
                self._messages.remove(message)
                changed = True
                continue
            seen_assistant_runs.add(message.run_id)
            if message.status in live_statuses and message.run_id != active_run_id:
                message_age = now - max(float(message.updated_at or 0.0), float(message.created_at or 0.0))
                mode = str((message.details or {}).get("mode") or "").lower()
                if mode == "chat" and message.run_id in self._active_chat_requests:
                    continue
                created_in_this_process = float(message.created_at or 0.0) >= self._started_at - 1.0
                if mode == "chat" and created_in_this_process and message_age < 300.0:
                    continue
                # Run IDs still pending in the execute queue (submitted but
                # whose _plan_and_execute thread has not yet set self._current)
                # are alive — not orphans. This closes the race between
                # submit_command creating the message and the thread reaching
                # the self._current assignment after LLM routing.
                if message.run_id in self._pending_run_ids:
                    continue
                if created_in_this_process and message_age < startup_grace_sec:
                    continue
                if not str(message.content or "").strip():
                    message.content = "任务进程已中断或服务已重启，请重新执行该指令。"
                message.status = "error"
                details = dict(message.details or {})
                details["phase"] = "interrupted"
                details["interrupted"] = True
                message.details = details
                message.updated_at = time.time()
                changed = True
        return changed

    def _finalize_assistant_response(self, run: RunState) -> None:
        final_status = run.status
        if final_status == "cancelled" or self._is_run_cancelled(run.run_id):
            run.status = "cancelled"
            run.phase = "cancelled"
            run.finished_at = run.finished_at or time.time()
            run.assistant_message = run.assistant_message or "任务已中断。"
            self._update_assistant_message(run.run_id, run.assistant_message, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        if final_status in {"completed", "planned", "failed", "blocked"} and run.answer_with_llm:
            run.status = "responding"
            run.phase = "responding"
            self._publish_run_update(run)
            self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))

        telemetry = self.tools.status_snapshot().get("drone")
        run.final_telemetry = dict(telemetry or {})
        if not run.verification:
            run.verification = self._verify_run_outcome(run)
        if not run.answer_with_llm:
            answer = self.planner.final_answer_stream(
                command=run.command,
                run_status=final_status,
                plan=run.plan,
                telemetry=telemetry,
                failure_reason=run.failure_reason,
                verification=run.verification,
                model_id=run.model_id or None,
                # LLM-written summary (streamed); final_answer_stream falls
                # back to the template internally when the LLM is unavailable
                force_fallback=False,
                should_stop=lambda: self._is_run_cancelled(run.run_id),
            )
            if self._is_run_cancelled(run.run_id):
                final_status = "cancelled"
                answer = "任务已中断。"
            run.status = final_status
            run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
            run.assistant_message = answer
            self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        buffer: list[str] = []
        reasoning_buffer: list[str] = []

        def on_reasoning(token: str) -> None:
            reasoning_buffer.append(token)
            reasoning = "".join(reasoning_buffer).strip()
            if not reasoning:
                return
            self._append_process(run, "模型推理", reasoning, status="running", kind="reasoning")
            self._update_assistant_message(
                run.run_id,
                "".join(buffer) or self._progress_message(run),
                "running",
                None,
                persist=False,
            )

        def on_token(token: str) -> None:
            buffer.append(token)
            self._append_assistant_delta(run.run_id, token, "".join(buffer), None)

        answer = self.planner.final_answer_stream(
            command=run.command,
            run_status=final_status,
            plan=run.plan,
            telemetry=telemetry,
            failure_reason=run.failure_reason,
            verification=run.verification,
            model_id=run.model_id or None,
            on_token=on_token,
            on_reasoning=on_reasoning,
            force_fallback=not run.answer_with_llm,
            should_stop=lambda: self._is_run_cancelled(run.run_id),
        )
        if not answer and buffer:
            answer = "".join(buffer)
        if reasoning_buffer:
            self._append_process(run, "模型推理", "".join(reasoning_buffer).strip(), status="completed", kind="reasoning")
        if self._is_run_cancelled(run.run_id):
            final_status = "cancelled"
            answer = answer or "任务已中断。"
        run.status = final_status
        run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
        run.assistant_message = answer
        self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
        self._publish_run_update(run)
        self._finalize_task_run(run)
        with self._lock:
            self._cancelled_request_ids.discard(run.run_id)

    def _append_assistant_delta(
        self,
        run_id: str,
        token: str,
        content: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] | None = None
        with self._lock:
            # 已取消的任务：冻结增量写入（与 _update_assistant_message 的
            # 中断竞态保护一致，避免把中断标记覆盖回 running）
            if run_id in self._cancelled_request_ids:
                return
            # 优先按 run_id 精确匹配
            target = None
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    target = message
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not target:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        target = message
                        break
            if target:
                target.content = content
                target.status = "running"
                target.run_id = run_id
                target.details = {**(target.details or {}), **(details or {})}
                target.updated_at = time.time()
                payload = {
                    "id": target.id,
                    "run_id": run_id,
                    "token": token,
                    "content": content,
                    "message": self._message_public_dict(target),
                }
        if payload:
            self._publish("message_delta", payload)

    def _progress_message(self, run: RunState) -> str:
        phase = run.phase or run.status
        if phase == "planning":
            return "正在规划任务并选择可用工具..."
        if phase == "responding":
            return "工具调用已完成，正在整理最终回复..."
        if phase == "verifying":
            return "工具调用已完成，正在回读状态并校验结果..."

        loop_state = run.loop_state if isinstance(run.loop_state, dict) else {}
        decisions = loop_state.get("decisions") if isinstance(loop_state, dict) else []
        results = loop_state.get("results") if isinstance(loop_state, dict) else []
        if isinstance(decisions, list) and isinstance(results, list) and len(decisions) > len(results):
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."
        if isinstance(results, list) and results:
            latest_result = results[-1] if isinstance(results[-1], dict) else {}
            result_tool = str(latest_result.get("tool") or "")
            if result_tool:
                return f"已完成：{self._tool_action_label(result_tool)}，正在处理结果..."
        if isinstance(decisions, list) and decisions:
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."

        if run.plan and run.current_step:
            for step in run.plan.steps:
                if step.id == run.current_step:
                    if step.tool == "memory_store":
                        return "正在整理最终结果..."
                    label = step.label or self._tool_action_label(step.tool)
                    return f"正在执行：{label}..."
        if run.plan and run.plan.steps:
            current = next((step for step in run.plan.steps if step.status == "running"), None)
            if not current:
                current = next((step for step in run.plan.steps if step.status in {"pending", "planned"}), None)
            if current:
                if current.tool == "memory_store":
                    return "正在整理最终结果..."
                label = current.label or self._tool_action_label(current.tool)
                return f"正在执行：{label}..."

        if phase == "executing":
            return "正在执行任务，请稍候..."
        return "正在处理任务，请稍候..."

    def _message_details(self, run: RunState) -> dict[str, Any]:
        # 终态 run 的 details 快照前先收尾过程条目，避免前端残留转圈的步骤
        if run.status in {"completed", "failed", "blocked", "cancelled", "planned"}:
            self._settle_process_trace(run)
        return {
            "mode": run.mode,
            "phase": run.phase,
            "run_status": run.status,
            "progress": round(run.progress, 1),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "plan": self._sanitize_for_frontend(run.plan.to_dict()) if run.plan else None,
            "failure_reason": run.failure_reason,
            "task_level": run.task_level,
            "route_strategy": run.route_strategy,
            "route_reason": run.route_reason,
            "loop_state": self._sanitize_for_frontend(run.loop_state),
            "verification": self._sanitize_for_frontend(run.verification),
            "agent_state": self._sanitize_for_frontend(run.agent_state),
            "thought_trace": self._sanitize_for_frontend(list(run.thought_trace)),
            "process_trace": self._sanitize_for_frontend(list(run.process_trace)),
            # 思考全文与计划摘要：优先取 run.agent_state（规划时写入），
            # 取不到就从 process_trace / run.plan 兜底派生——_message_details
            # 每次重建，任何一环丢失都会让前端看不到思考或计划面板。
            "reasoning_text": self._reasoning_text_for_frontend(run),
            "plan_summary": self._plan_summary_for_frontend(run),
        }

    @staticmethod
    def _reasoning_text_for_frontend(run: RunState) -> str:
        text = str((run.agent_state or {}).get("_reasoning_text") or "").strip()
        if text:
            return text[:8000]
        for item in run.process_trace:
            if item.get("kind") == "reasoning" and str(item.get("body") or "").strip():
                return str(item.get("body"))[:8000]
        return ""

    @staticmethod
    def _plan_summary_for_frontend(run: RunState) -> str:
        stored = str((run.agent_state or {}).get("_plan_summary") or "").strip()
        if stored:
            return stored[:4000]
        steps = [
            str(step.tool)
            for step in (run.plan.steps if run.plan else [])
            if getattr(step, "tool", "") and step.tool != "memory_store"
        ]
        if not steps:
            return ""
        return "执行计划（" + str(len(steps)) + " 步）：" + " → ".join(steps)

    def _run_public_dict(self, run: RunState) -> dict[str, Any]:
        return self._sanitize_for_frontend(trim_loop_state_payload(run.to_dict()))

    def _message_public_dict(self, message: ChatMessage) -> dict[str, Any]:
        # 走和会话文件同一套瘦身：每次 /api/state 都带着整批消息，不剥掉
        # loop_state.observations / agent_state 的话，每轮轮询都要序列化几 MB。
        return self._sanitize_for_frontend(_trim_session_message(message.to_dict(), for_payload=True))

    def _sanitize_for_frontend(self, value: Any, _depth: int = 0) -> Any:
        # 深度上限：run/messages 的嵌套结构可能因共享可变字典形成循环引用，
        # 无限递归会打爆 run 更新发布（UI 冻结在最后一步）。超限用标记代替。
        if _depth > 24:
            return {"[bounded]": True}
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text == "image_base64":
                    image_text = str(item or "")
                    sanitized["image_base64_omitted"] = True
                    sanitized["image_base64_bytes"] = len(image_text)
                    continue
                if key_text in {"data_url", "image_data_url"}:
                    image_text = str(item or "")
                    sanitized[f"{key_text}_omitted"] = True
                    sanitized[f"{key_text}_bytes"] = len(image_text)
                    continue
                sanitized[key_text] = self._sanitize_for_frontend(item, _depth + 1)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_for_frontend(item, _depth + 1) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_for_frontend(item, _depth + 1) for item in value]
        if isinstance(value, str) and len(value) > 12000:
            return f"{value[:12000]}... [omitted {len(value) - 12000} chars]"
        return value

    def _append_event(
        self,
        level: str,
        source: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(time.time(), level, source, message, data or {})
        with self._lock:
            self._events.append(event)
            self._events = self._events[-200:]
            terminal = {"completed", "planned", "failed", "blocked", "cancelled"}
            default_run_id = (
                self._current.run_id
                if self._current and self._current.status not in terminal
                else ""
            )
        self._record_task_event(event, default_run_id=default_run_id)
        self._publish("runtime_event", event.to_dict())
        return event

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "type": event_type,
            "payload": payload,
            "time": time.time(),
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(envelope)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(envelope)
                except Exception:
                    pass

    def _publish_run_update(self, run: RunState) -> None:
        self._update_task_run(run)
        self._publish("run_update", self._run_public_dict(run))

    def _settle_process_trace(self, run: RunState) -> None:
        """任务收尾：把仍标记为 running 的过程条目收掉。

        否则前端会一直显示转圈的"运行中"步骤（结果回填靠 tool 名匹配，
        同名工具重复出现或走批量执行时可能漏配）。
        """
        terminal_ok = run.status in {"completed", "planned"}
        for item in run.process_trace:
            if item.get("status") == "running":
                item["status"] = "completed" if terminal_ok else "failed"
                body = str(item.get("body") or "").strip()
                if not body:
                    item["body"] = "已结束"
                elif item.get("kind") == "reasoning" and body in self._PENDING_REASONING_PLACEHOLDERS:
                    # 循环在"结果回来、模型刚准备想下一步"这一拍收敛时，占位行
                    # 来不及被决策收尾，会以"正在选择下一步动作"的样子留在
                    # 时间线上冒充思考内容。
                    item["body"] = "本轮模型未输出思考文本。"
