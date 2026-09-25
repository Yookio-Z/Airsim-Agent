"""The bridge between AgentRuntime and the Agent loop / tool layer.

Loop state callbacks (mirroring observations, decisions and results into the UI),
the governed tool entry point and its guards, skills/sub-agents/memory dispatch,
and the result rendering that turns tool payloads into readable trace rows.
Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from .loop_types import LoopState
from .planner import MissionStep
from .run_state import RunState
from .sub_agent import SubAgentRunner
from .tool_executor import ToolCallResult
from pathlib import Path
from typing import Any
import base64
import json
import math
import re
import threading
import time


class AgentBridgeMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _append_sub_agent_rows(self, run: RunState, loop: LoopState, sub_run_id: str) -> None:
        """把子 Agent 的进度作为时间线行附到父任务下，不改写父 run 的任何状态。

        子循环的 decisions/results 属于子任务，父 run 的 loop_state、进度、状态、
        看门狗都必须保持自己的语义；这里只做可视化，并用已见计数去重（子循环
        每次都回显完整列表）。
        """
        if not isinstance(run.agent_state, dict):
            return
        seen = run.agent_state.setdefault("_sub_agent_rows", {})
        if not isinstance(seen, dict):
            return
        counts = seen.get(sub_run_id) or {"decisions": 0, "results": 0}
        decisions = list(getattr(loop, "decisions", []) or [])
        results = list(getattr(loop, "results", []) or [])
        if len(decisions) > int(counts.get("decisions") or 0):
            counts["decisions"] = len(decisions)
            decision = decisions[-1]
            self._append_process(
                run,
                f"子任务决策 {len(decisions)}",
                self._loop_decision_public_text(decision)
                or str(getattr(decision, "action", "") or "检查子任务是否完成"),
                status="completed" if getattr(decision, "is_complete", False) else "running",
                kind="reasoning",
            )
        if len(results) > int(counts.get("results") or 0):
            counts["results"] = len(results)
            result = results[-1]
            self._append_process(
                run,
                f"子任务动作: {getattr(result, 'tool', '')}",
                "子任务工具执行完成。" if getattr(result, "ok", False) else "子任务工具执行失败。",
                status="completed" if getattr(result, "ok", False) else "failed",
                kind="tool",
            )
        seen[sub_run_id] = counts

    def _on_agent_loop_state(self, loop: LoopState) -> None:
        with self._lock:
            run = self._current
            if not run or run.run_id != loop.run_id:
                return
            sub_run_id = str(getattr(loop, "sub_run_id", "") or "")
            if sub_run_id:
                # 子 Agent 的进度回显：只往时间线上补行。以前这里不区分回显，
                # 子循环的 decisions/results 会整体覆盖父 run 的 loop_state、按
                # 子循环的 max_steps 重算父进度，还会替父 run 启动包线看门狗。
                self._append_sub_agent_rows(run, loop, sub_run_id)
                self._publish_run_update(run)
                return
            if run.status == "cancelled":
                self._publish_run_update(run)
                return
            previous_decision_count = len((run.loop_state or {}).get("decisions") or [])
            previous_result_count = len((run.loop_state or {}).get("results") or [])
            previous_observation_count = len((run.loop_state or {}).get("observations") or [])
            run.loop_state = loop.to_dict()
            decision_count = len(loop.decisions)
            result_count = len(loop.results)
            observation_count = len(loop.observations)
            run.current_step = f"loop-{decision_count}" if decision_count else "observe"
            run.progress = min(95.0, decision_count / max(1, loop.max_steps) * 100.0)
            if run.execute and run.status not in {"paused", "awaiting_approval", "cancelled", "blocked"}:
                run.status = "running"
                run.phase = "executing"
            if observation_count > previous_observation_count and decision_count == previous_decision_count:
                # 仅当上一轮是模型决策时才提示"模型正在决策"；计划驱动的步骤
                # 之间没有模型思考，不该显示成思考占位。
                prior_source = ""
                if loop.decisions:
                    prior_source = str(getattr(loop.decisions[-1], "source", "llm") or "llm")
                if prior_source != "plan":
                    # 新一轮思考开始，先清掉上一轮的累积器：占位符刚建出来时它的
                    # status 就是 running，_handle_reasoning 会把第一段流式文本接在
                    # 旧累积后面，前端就表现为"新一轮显示的是上一轮的思考"。
                    if isinstance(run.agent_state, dict):
                        run.agent_state["_round_reasoning"] = ""
                    self._append_process(
                        run,
                        "模型决策",
                        "正在根据最新遥测、工具结果和任务目标选择下一步动作。",
                        status="running",
                        kind="reasoning",
                    )
            if decision_count > previous_decision_count:
                decision = loop.decisions[-1]
                decision_text = self._loop_decision_public_text(decision)
                decision_source = str(getattr(decision, "source", "llm") or "llm")
                run.thought_trace.append({
                    "timestamp": time.time(),
                    "title": f"循环决策 {decision_count}",
                    "body": decision_text or decision.action or "检查任务是否完成",
                    "status": "completed" if decision.is_complete else "running",
                    "source": decision_source,
                })
                run.thought_trace = run.thought_trace[-30:]
                if decision_source == "plan":
                    # 按计划执行：这不是模型的"新思考"，只是把 LLM 已给出的
                    # 计划步骤落地。前端应显示为"执行计划"，不要混进模型思考。
                    # 同时收掉之前残留的"模型决策 running"占位。
                    for item in reversed(run.process_trace):
                        if item.get("kind") == "reasoning" and item.get("status") == "running":
                            item["status"] = "completed"
                            if not item.get("body"):
                                item["body"] = "计划已确定，按计划执行，无需重新决策。"
                            break
                    if decision.action:
                        self._append_process(
                            run,
                            decision_text or decision.action,
                            self._format_tool_call_body(decision.params),
                            status="running",
                            tool=decision.action,
                            params=decision.params,
                            kind="plan_step",
                        )
                else:
                    # 每个真实 LLM 决策轮次前加分隔标题，前端思考块能看出
                    # "第 N 轮思考（基于上一步结果）"的 ReAct 节奏
                    with self._lock:
                        if not isinstance(run.agent_state, dict):
                            run.agent_state = {}
                        prev = str(run.agent_state.get("_reasoning_text") or "")
                        run.agent_state["_reasoning_text"] = (
                            prev + f"\n\n── 第 {decision_count} 轮思考 ──\n"
                        ).strip()[:12000]
                    if decision.is_complete:
                        self._append_process(
                            run,
                            "模型决策",
                            "任务目标已满足，正在整理最终报告。",
                            status="completed",
                            kind="reasoning",
                        )
                    if decision_text:
                        self._append_process(
                            run,
                            "模型总结" if decision.is_complete else "模型决策",
                            decision_text,
                            status="completed",
                            kind="reasoning",
                        )
                    elif not decision.is_complete:
                        # 模型这一轮只给了工具调用、没有思考文本：把本轮开头创建的
                        # "正在选择下一步"占位收尾。否则它会一直挂在时间线上冒充
                        # 本轮思考——实测从第二轮起永远显示那句固定占位。
                        self._close_pending_reasoning(run, decision.action)
                    if decision.action:
                        # 顺序由 agent_loop 保证：真正的模型思考事件已先于本
                        # 决策写入时间线（见 AgentLoop.run）。这里只落动作行；
                        # 技能激活单独成类，和工具调用区分开。
                        self._append_process(
                            run,
                            decision.action,
                            self._format_tool_call_body(decision.params),
                            status="running",
                            tool=decision.action,
                            params=decision.params,
                            kind="skill" if str(decision.action).startswith("skill:") else "tool",
                        )
            if result_count > previous_result_count and loop.results:
                result = loop.results[-1]
                # 技能激活是"取回这类任务的操作步骤"，不是一次设备动作：单独标成
                # skill，前端才能把它渲染成与"模型思考/工具调用"同级的一条。
                self._append_process(
                    run,
                    result.tool,
                    self._format_loop_result_body(result.data),
                    status="completed" if result.ok else "failed",
                    tool=result.tool,
                    params=result.params,
                    kind="skill" if str(result.tool).startswith("skill:") else "tool",
                )
        self._publish_run_update(run)
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        self._maybe_start_envelope_guard(run)
        self._maybe_start_tracking_assist(run)

    @staticmethod
    def _loop_decision_public_text(decision: Any) -> str:
        parts: list[str] = []
        reason = str(getattr(decision, "reason", "") or "").strip()
        reflection = str(getattr(decision, "reflection", "") or "").strip()
        if reason:
            parts.append(reason)
        if reflection and reflection != reason:
            parts.append(reflection)
        text = "\n".join(parts).strip()
        if not text:
            return ""
        # 模型有时把整段决策 JSON 塞进 reason 字段（实测："{\"action\": \"drone_hover\",
        # ...}"），它会原样出现在"模型思考"里。思考块只保留自然语言。
        stripped = AgentBridgeMixin._strip_plan_json_draft(text)
        if not stripped:
            return ""
        text = stripped
        # "Call <tool>" 只是"模型只给了 tool_call、没有正文"的合成标签，不是思考
        # 内容；它已由工具行表达。留在这里会让"模型思考"看起来整轮都在空转。
        if all(
            re.fullmatch(r"Call [A-Za-z0-9_.]+", line.strip()) for line in text.splitlines() if line.strip()
        ):
            return ""
        return text

    @staticmethod
    def _format_tool_call_body(params: dict[str, Any] | None) -> str:
        if not params:
            return "准备调用工具。"
        try:
            payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            payload = str(params)
        return f"参数 {payload}"

    @classmethod
    def _humanize_result(cls, data: dict[str, Any]) -> str:
        """把工具返回渲染成操作员能读的要点，而不是一整块 JSON。

        原始 JSON 里混着 status/backend/vehicle_name 这类协议字段和嵌套结构，
        操作员要的只是"飞机在哪、什么状态、目标如何"。这里按已知字段逐项写成
        中文要点，只把无法归类的内容压成一行精简 JSON 兜底。
        """
        src = data.get("drone") if isinstance(data.get("drone"), dict) else data
        lines: list[str] = []

        pos = src.get("position_ned")
        if isinstance(pos, dict):
            try:
                x = float(pos.get("x", 0.0) or 0.0)
                y = float(pos.get("y", 0.0) or 0.0)
                z = float(pos.get("z", 0.0) or 0.0)
                lines.append(f"位置 N {x:.2f} / E {y:.2f} / D {z:.2f} m（高度 {abs(z):.2f} m）")
            except (TypeError, ValueError):
                pass

        vel = src.get("velocity_ned") or src.get("velocity")
        if isinstance(vel, dict):
            try:
                vx = float(vel.get("vx", 0.0) or 0.0)
                vy = float(vel.get("vy", 0.0) or 0.0)
                vz = float(vel.get("vz", 0.0) or 0.0)
                lines.append(f"速度 {vx:.2f} / {vy:.2f} / {vz:.2f} m/s")
            except (TypeError, ValueError):
                pass

        flags: list[str] = []
        if "armed" in src:
            flags.append("已解锁" if src.get("armed") else "未解锁")
        if "flying" in src:
            flags.append("飞行中" if src.get("flying") else "在地面")
        mode = src.get("mode") or src.get("flight_mode")
        if mode:
            flags.append(f"{mode} 模式")
        if flags:
            lines.append("状态: " + " · ".join(str(f) for f in flags))

        extra: list[str] = []
        if src.get("heading_deg") is not None:
            try:
                extra.append(f"航向 {float(src['heading_deg']):.1f}°")
            except (TypeError, ValueError):
                pass
        if src.get("battery_voltage") is not None:
            try:
                extra.append(f"电量 {float(src['battery_voltage']):.2f} V")
            except (TypeError, ValueError):
                pass
        gps = src.get("gps")
        satellites = None
        if isinstance(gps, dict):
            satellites = gps.get("satellites", gps.get("satellites_visible"))
        satellites = satellites if satellites is not None else src.get("satellites_visible")
        if satellites is not None:
            extra.append(f"GPS {satellites} 颗星")
        if isinstance(gps, dict) and gps.get("fix_type"):
            extra.append(f"定位 {gps['fix_type']}")
        if src.get("heartbeat_age_s") is not None:
            try:
                extra.append(f"心跳 {float(src['heartbeat_age_s']):.2f}s")
            except (TypeError, ValueError):
                pass
        if extra:
            lines.append(" · ".join(extra))

        flat = {
            key: value
            for key, value in {**src, **data}.items()
            if key not in cls._RESULT_NOISE_KEYS
            and key not in cls._RESULT_TELEMETRY_KEYS
            and not isinstance(value, (dict, list))
        }
        if flat:
            lines.append("，".join(f"{key}={value}" for key, value in list(flat.items())[:8]))

        nested = {
            key: value
            for key, value in {**src, **data}.items()
            if isinstance(value, (dict, list))
            and key not in cls._RESULT_TELEMETRY_KEYS
            and key not in cls._RESULT_NOISE_KEYS
        }
        if nested:
            try:
                import json as _json

                rendered = _json.dumps(nested, ensure_ascii=False, default=str)
            except Exception:
                rendered = str(nested)
            if len(rendered) > 700:
                rendered = rendered[:700] + " …"
            lines.append("其他: " + rendered)
        return chr(10).join(lines)

    @classmethod
    def _result_body_fallback(cls, data: dict[str, Any], message: str) -> str:
        """工具返回的正文：优先渲染成可读要点，实在没有可读内容才退回 JSON。"""
        humanized = cls._humanize_result(data) if isinstance(data, dict) else ""
        if humanized.strip():
            return humanized
        try:
            import json as _json

            payload = {k: v for k, v in (data or {}).items() if k not in cls._RESULT_NOISE_KEYS}
            rendered = _json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return message
        if len(rendered) > 1200:
            rendered = rendered[:1200] + " …"
        return rendered if rendered.strip() not in {"{}", "null"} else message

    def _format_loop_result_body(data: dict[str, Any] | None) -> str:
        if not isinstance(data, dict):
            return ""
        # 技能激活返回的是完整操作指导，正文本身就是结果：压缩成一行摘要会让
        # 操作员展开后什么也看不到（实测反馈"技能调用不能展开/看不到内容"）。
        for key in ("guidance", "guidance_text", "markdown", "skill_body", "content"):
            value = data.get(key)
            if isinstance(value, str) and len(value.strip()) > 80:
                return value.strip()
        message = str(data.get("message") or data.get("summary_zh") or data.get("status") or "").strip()
        tool_results = data.get("tool_results")
        if isinstance(tool_results, list) and tool_results:
            parts: list[str] = []
            for item in tool_results[:12]:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool") or item.get("name") or "tool")
                ok = "ok" if item.get("ok") is True else ("failed" if item.get("ok") is False else "")
                nested = item.get("data") if isinstance(item.get("data"), dict) else {}
                detail = str(nested.get("message") or nested.get("summary_zh") or nested.get("status") or "").strip()
                label = f"{tool} {ok}".strip()
                parts.append(f"{label}: {detail}" if detail else label)
            summary = " → ".join(parts)
            if len(tool_results) > 12:
                summary += f" → +{len(tool_results) - 12} more"
            return f"{AgentBridgeMixin._result_body_fallback(data, message)}\n{summary}".strip() if AgentBridgeMixin._result_body_fallback(data, message) else summary
        fallback = AgentBridgeMixin._result_body_fallback(data, message)
        # 首行保留人话摘要（折叠标题只用第一行），完整数据接在后面：
        # 展开才看得到遥测、坐标这些真正返回的东西。
        if message and fallback and not fallback.lstrip().startswith(message):
            return message + chr(10) + chr(10) + fallback
        return fallback or message

    @staticmethod
    def _verification_body(verification: dict[str, Any] | None) -> str:
        """校验行的正文：结论 + 回读到的状态 + 逐条校验项。

        以前只显示一句 summary，操作员根本不知道"在校验什么、依据是什么"；
        真正的数据在 checks / 起点终点位置里，这里如实列出来。
        """
        payload = verification if isinstance(verification, dict) else {}
        lines: list[str] = []
        summary = str(payload.get("summary") or "").strip()
        if summary:
            lines.append(summary)

        def _fmt(pos: Any) -> str:
            if not isinstance(pos, dict):
                return "—"
            try:
                return (
                    f"N {float(pos.get('x', 0.0)):.2f} / E {float(pos.get('y', 0.0)):.2f} "
                    f"/ D {float(pos.get('z', 0.0)):.2f} m"
                )
            except Exception:
                return "—"

        start_pos = payload.get("start_position_ned")
        final_pos = payload.get("final_position_ned")
        if isinstance(start_pos, dict):
            lines.append(f"起点: {_fmt(start_pos)}")
        if isinstance(final_pos, dict):
            lines.append(f"终点: {_fmt(final_pos)}")
        if "final_flying" in payload:
            flying = payload.get("final_flying")
            landed = payload.get("final_landed_state")
            tail = f"（landed_state={landed}）" if landed is not None else ""
            lines.append(f"结束状态: {'飞行中' if flying else '已落地'}{tail}")

        checks = payload.get("checks") or payload.get("results")
        if isinstance(checks, list) and checks:
            lines.append("校验项:")
            for item in checks[:12]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("metric") or "检查")
                ok = item.get("ok", item.get("satisfied"))
                mark = "通过" if ok is True else ("未通过" if ok is False else "未评估")
                severity = str(item.get("severity") or "").strip()
                detail = str(
                    item.get("detail") or item.get("message") or item.get("reason") or ""
                ).strip()
                suffix = f"【{severity}】" if severity else ""
                lines.append(f"- {name}: {mark}{suffix}" + (f" — {detail}" if detail else ""))
                expected = item.get("expected")
                if isinstance(expected, dict) and expected:
                    rendered = "，".join(
                        f"{key}={value}" for key, value in list(expected.items())[:4]
                    )
                    lines.append(f"    期望: {rendered}")
        if not lines:
            lines.append("未声明可校验的成功判据，仅回读最终状态。")
        return chr(10).join(lines)

    def _upsert_verify_row(self, run: RunState, body: str, status: str = "completed") -> None:
        """就地更新最后一条校验行，而不是再追加一条。

        "正在回读…"那条占位行被收尾成 completed 之后，再追加结果就会在时间线
        上出现两条校验结果——操作员看到的正是这个（实测反馈："为啥每次返回两次"）。
        """
        with self._lock:
            for item in reversed(run.process_trace):
                if item.get("kind") == "verify":
                    item.update({"timestamp": time.time(), "body": body, "status": status})
                    return
        self._append_process(run, "回读与校验", body, status=status, kind="verify")

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _ned_value(self, position: dict[str, Any] | None, key: str, default: float | None = None) -> float | None:
        if not isinstance(position, dict):
            return default
        value = self._finite_float(position.get(key))
        return value if value is not None else default

    def _execute_agent_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
        approval_already_granted: bool = False,
    ) -> ToolCallResult:
        """Single governed entry point used by plans, loops, skills, and the tool API."""
        params = dict(params or {})
        caller_owns_run = self._execution_thread_id == threading.get_ident()
        with self._lock:
            run = run or (self._current if caller_owns_run else None)

        manual_safety_tools = {"drone_hover", "drone_land", "airsim_task_cancel"}
        read_only_tools = set(self.tools.READ_ONLY_TOOLS) | {"airsim_task_status"}
        if self._execution_slot.locked() and not caller_owns_run and tool not in read_only_tools | manual_safety_tools:
            return self._blocked_tool_result(
                tool,
                params,
                "有 Agent 任务正在执行，这条指令被拒绝了。请先点『停止/打断』结束任务；"
                "悬停与降落始终允许，降落会自动结束当前任务。",
            )

        # Formation conflict guard: while the deterministic formation/coverage
        # control loop is commanding vehicles, single-vehicle flight tools are
        # blocked so two control paths can never fight over the same drone.
        # Hover/land/status/connect stay available as safe recovery actions.
        formation_active = getattr(self.tools, "formation_active", None)
        if (
            callable(formation_active)
            and formation_active()
            and tool in self.tools.CONTROL_TOOLS
            and tool not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect", "airsim_task_cancel"}
        ):
            return self._blocked_tool_result(
                tool,
                params,
                "a formation/coverage mission is active; use formation_command(action=hover_all) or land_all before single-vehicle control",
            )

        if tool.startswith("skill:"):
            return self._execute_skill_tool(tool, params, dry_run=dry_run, run=run)
        if tool == "agent_subtask":
            return self._execute_sub_agent_tool(params, dry_run=dry_run, run=run)
        if tool == "memory_recall":
            return self._execute_memory_recall(params, dry_run=dry_run)
        if tool == "memory_remember":
            return self._execute_memory_remember(params, dry_run=dry_run)
        if dry_run:
            return self.tools.execute(tool, params, dry_run=True)

        runtime = self.tools.status_snapshot()
        profile = runtime.get("backend_profile") or {}
        capabilities = profile.get("capabilities") or {}
        # 后端代际校验：任务启动后链路若被换过（切后端/重连到别的端点），后续
        # 飞控动作会打到另一架飞机上。代际不符直接拒绝，不做"尽力而为"。
        if run is not None and tool in self.tools.CONTROL_TOOLS:
            expected_generation = int(getattr(run, "backend_generation", -1))
            with self._lock:
                current_generation = self._backend_generation
            if expected_generation >= 0 and expected_generation != current_generation:
                return self._blocked_tool_result(
                    tool,
                    params,
                    "任务启动后飞行后端已被切换，拒绝在未经确认的链路上执行飞控动作。"
                    "请停止任务、确认链路后重新下发。",
                )
        risk_level = self._tool_risk_level(tool, capabilities, run, params)
        requires_approval = bool(capabilities.get("requires_operator_approval"))
        if risk_level == "high" and requires_approval and not approval_already_granted:
            if run is None:
                return self._blocked_tool_result(
                    tool,
                    params,
                    (
                        "真机上的高危动作不能从工具栏直接下发：请在 Execute 模式下提交该指令，"
                        "由操作员审批后执行。"
                        "（high-risk real-vehicle tools must be submitted through Execute "
                        "mode and approved）"
                    ),
                )
            approved = self._await_tool_approval(
                run,
                tool,
                params,
                risk_level,
                reason=self._approval_reason(tool, params),
            )
            if not approved:
                return self._blocked_tool_result(tool, params, run.failure_reason or "operator approval rejected")
            # 审批期间 run.status 变成 awaiting_approval，包线看门狗会据此退出；
            # plan-execute 路径没有别的重挂点，所以批准后立刻重启，否则整段任务
            # 余下部分都在没有连续包线监控的情况下飞行。
            self._maybe_start_envelope_guard(run)

        low_altitude_block = self._low_altitude_motion_guard(tool, params, runtime, capabilities)
        if low_altitude_block:
            return low_altitude_block

        obstacle_block = self._obstacle_guard(tool, params, capabilities)
        if obstacle_block:
            return obstacle_block

        # 飞控指令串行闸门：只有真正改变飞行状态的工具才需要独占飞控，
        # 持锁期间算法级视觉伺服不插入，避免两路线程同时向 PX4 推不同目标点
        # （历史上会导致突然俯冲/乱转/掉机）。只读/视觉工具不占锁，保证
        # OFFBOARD 设定值流不中断。视觉伺服 tick 很短，等待上限 30s。
        needs_gate = tool in self.tools.CONTROL_TOOLS
        gate_held = self.tools.acquire_control_gate(blocking=True, timeout=30.0) if needs_gate else False
        try:
            result = self.tools.execute(
                tool,
                params,
                dry_run=False,
                blocked_by_supervisor=self.supervisor.is_emergency_stopped(),
            )
        finally:
            if gate_held:
                self.tools.release_control_gate()
        # 降落指令返回时飞机通常仍在下沉（LAND 模式下 flying 会提前变 false）。
        # 这里在不持有执行锁的情况下等它真正落地/上锁，避免随后的完成校验把
        # "正在降落"误判成"未落地"而触发无谓的纠错循环。
        if tool == "drone_land" and result.ok:
            self._await_grounded()
        self._remember_visual_frame_from_payload(result.data, source=tool, params=params)
        return result

    def _await_grounded(self, timeout: float = 30.0) -> bool:
        """轮询等待飞机真正落地（不持执行锁）。"""
        deadline = time.time() + max(3.0, float(timeout))
        while time.time() < deadline:
            if self.supervisor.is_emergency_stopped():
                return False
            try:
                drone = (self.tools.status_snapshot().get("drone") or {})
            except Exception:
                return False
            pos = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            vel = drone.get("velocity_ned") if isinstance(drone.get("velocity_ned"), dict) else {}
            try:
                alt = abs(float((pos or {}).get("z", 0.0) or 0.0))
                vz = abs(float((vel or {}).get("vz", 0.0) or 0.0))
            except (TypeError, ValueError):
                alt, vz = 99.0, 99.0
            try:
                on_ground = int(drone.get("landed_state")) == 1  # MAV_LANDED_STATE_ON_GROUND
            except (TypeError, ValueError):
                on_ground = False
            if (not drone.get("armed")) or (on_ground and alt < 0.6 and vz <= 0.4):
                return True
            if not drone.get("flying") and alt < 0.6 and vz <= 0.4:
                return True
            time.sleep(1.0)
        return False

    def _low_altitude_motion_guard(
        self,
        tool: str,
        params: dict[str, Any],
        runtime: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        if tool != "drone_move_relative" or not capabilities.get("flight_control"):
            return None
        try:
            forward = abs(float(params.get("forward_m", 0.0) or 0.0))
            right = abs(float(params.get("right_m", 0.0) or 0.0))
            up = float(params.get("up_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if (forward * forward + right * right) ** 0.5 < 0.15:
            return None
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        altitude = self._vehicle_altitude_m(drone)
        if altitude is None:
            return None
        # 只要"确实在空中"就允许水平移动。之前用 1.5m 阈值会在飞机因模式回退
        # 短暂掉到 ~1.3m 时误拦，导致 Agent 反复重起飞/解锁、任务被拖死。
        # 真正要拦的是"没起飞/在地面"的水平移动。
        if bool(drone.get("flying")) and altitude >= 0.8:
            return None
        return self._blocked_tool_result(
            tool,
            params,
            (
                "horizontal relative movement is blocked: the vehicle is not airborne "
                f"(altitude {altitude:.2f} m, flying={bool(drone.get('flying'))}, armed={bool(drone.get('armed'))}). "
                "Take off first and confirm flying=true before moving horizontally."
            ),
        )

    def _execute_memory_recall(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        query = str(params.get("query") or "").strip()
        limit = 5
        try:
            limit = max(1, min(10, int(params.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        results = [] if dry_run else self.memory.recall(query, limit=limit)
        data = {"status": "planned" if dry_run else "ok", "query": query, "count": len(results), "results": results}
        return ToolCallResult("memory_recall", dict(params), True, data, started, time.time())

    def _execute_memory_remember(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        key = str(params.get("key") or "").strip()
        value = str(params.get("value") or "").strip()
        if not key:
            return ToolCallResult(
                "memory_remember",
                dict(params),
                False,
                {"status": "error", "message": "memory_remember requires a non-empty 'key'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        tags = params.get("tags")
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",") if item.strip()]
        if not dry_run:
            self.memory.remember_fact(key, value, tags)
        data = {"status": "planned" if dry_run else "ok", "key": key, "message": f"fact '{key}' stored"}
        return ToolCallResult("memory_remember", dict(params), True, data, started, time.time())

    def _execute_sub_agent_tool(self, params: dict[str, Any], dry_run: bool = False, run: RunState | None = None) -> ToolCallResult:
        """Delegate an open-ended subtask to a bounded sub-agent.

        Runs synchronously in the caller's thread, so the execution slot and
        approval context are shared with the parent loop. The sub-agent gets
        its own run log and step budget; the parent only receives the report.
        """
        started = time.time()
        goal = str(params.get("goal") or "").strip()
        if not goal:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                False,
                {"status": "error", "message": "agent_subtask requires a non-empty 'goal'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        max_steps = 6
        try:
            max_steps = max(2, min(12, int(params.get("max_steps") or 6)))
        except (TypeError, ValueError):
            max_steps = 6
        model_id = str(params.get("model_id") or "").strip() or (run.model_id if run else "") or ""
        if dry_run:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                True,
                {"status": "planned", "goal": goal, "message": "dry run only"},
                started,
                time.time(),
            )
        tool_runtime = self.tools.status_snapshot()
        capabilities = ((tool_runtime.get("backend_profile") or {}).get("capabilities")) or {}
        tool_cards = self.tools.list_tool_cards()
        parent_run_id = run.run_id if run else f"run_{int(time.time() * 1000)}"
        runner = SubAgentRunner(
            tools=self.tools,
            planner=self.planner,
            memory=self.memory,
            execute_tool=lambda name, sub_params, sub_dry: self._execute_agent_tool(name, sub_params, dry_run=sub_dry, run=run),
            should_stop=lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set(),
            should_pause=self.supervisor.should_pause,
            on_ui_event=self._on_agent_event,
            on_ui_state=self._on_agent_loop_state,
            # 计数器必须由父级持有：runner 是每次调用新建的，自带计数器会从 0
            # 重启，同一任务的第二个子任务又会去写 <parent>.sub1.jsonl（seq 重复、
            # 两次子运行的审计记录交错在同一个文件里）。
            sub_counter=self._sub_agent_counter,
        )
        report = runner.run(
            parent_run_id,
            goal,
            constraints=str(params.get("constraints") or ""),
            tool_cards=tool_cards,
            capabilities=capabilities,
            model_id=model_id or None,
            max_steps=max_steps,
        )
        ok = report.get("status") == "completed"
        error_code = "" if ok else ("BLOCKED" if report.get("status") == "blocked" else "TOOL_ERROR")
        return ToolCallResult(
            "agent_subtask",
            dict(params),
            ok,
            {"status": "ok" if ok else "failed", **report},
            started,
            time.time(),
            error_code=error_code,
        )

    def _execute_skill_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()

        def governed(subtool: str, subparams: dict[str, Any], sub_dry_run: bool) -> ToolCallResult:
            return self._execute_agent_tool(subtool, subparams, dry_run=sub_dry_run, run=run)

        result = self.skills.execute(
            tool,
            params,
            self.tools,
            dry_run=dry_run,
            execute_tool=governed,
        )
        data = result.to_dict()
        # 技能激活的核心产出就是它的操作指导正文：放进 data，时间线展开才能看到
        # 真正的内容，而不是一行 "activated skill guidance" 摘要。
        getter = getattr(self.skills, "skill_body", None)
        if str(tool).startswith("skill:") and callable(getter):
            try:
                guidance = str(getter(tool) or "").strip()
            except Exception:
                guidance = ""
            if guidance:
                data["guidance"] = guidance
        self._remember_visual_frame_from_payload(data, source=tool, params=params)
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=result.ok,
            data=data,
            started_at=started,
            finished_at=time.time(),
        )

    def _obstacle_guard(
        self,
        tool: str,
        params: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        guarded_tools = {"drone_move_relative"}
        if tool not in guarded_tools or not capabilities.get("obstacle_avoidance"):
            return None

        collector = getattr(self.tools, "collector", None)
        available_tools = getattr(collector, "tools", {}) if collector else {}
        if "provider_validate_motion" not in available_tools:
            return None

        validation_params = {
            "forward_m": float(params.get("forward_m", 0.0) or 0.0),
            "right_m": float(params.get("right_m", 0.0) or 0.0),
            "up_m": float(params.get("up_m", 0.0) or 0.0),
            "velocity": float(params.get("velocity", 1.0) or 1.0),
            "max_age_sec": 1.0,
        }
        check = self.tools.execute(
            "provider_validate_motion",
            validation_params,
            dry_run=False,
            allow_reconnect=False,
        )
        if check.ok:
            return None

        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={
                "status": "blocked",
                "message": (
                    "motion blocked by obstacle provider: "
                    f"{check.data.get('message') or check.data.get('status') or 'unsafe motion'}"
                ),
                "provider_check": check.to_dict(),
            },
            started_at=now,
            finished_at=now,
        )

    def _blocked_tool_result(self, tool: str, params: dict[str, Any], message: str) -> ToolCallResult:
        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={"status": "blocked", "message": message},
            started_at=now,
            finished_at=now,
            error_code="BLOCKED",
        )

    def _remember_visual_frame_from_payload(
        self,
        payload: dict[str, Any] | None,
        source: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return
        image_base64 = self._find_visual_value(payload, {"image_base64"})
        image_saved_to = self._find_visual_value(payload, {"image_saved_to", "saved_to", "approach_image_saved_to"})
        if not image_base64 and image_saved_to:
            image_base64 = self._read_image_base64(str(image_saved_to))
        if not image_base64:
            return
        metadata = self._visual_metadata(payload)
        with self._lock:
            self._last_visual_frame = {
                "source_tool": source,
                "params": dict(params or {}),
                "image_base64": str(image_base64),
                "image_saved_to": str(image_saved_to or ""),
                "metadata": metadata,
                "updated_at": time.time(),
            }

    def _find_visual_value(self, value: Any, keys: set[str], _depth: int = 0) -> Any:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item:
                    return item
            for nested in value.values():
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in reversed(value):
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        return None

    def _read_image_base64(self, image_path: str) -> str:
        if not image_path:
            return ""
        try:
            path = Path(image_path).expanduser()
            if not path.exists() or not path.is_file():
                return ""
            if path.stat().st_size > 12 * 1024 * 1024:
                return ""
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            return ""

    def _visual_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {
            "status",
            "message",
            "target",
            "target_class",
            "target_description",
            "vehicle",
            "camera",
            "image_type",
            "selected_view",
            "current_position",
            "search_progress",
            "detections",
            "target_world_position",
            "target_depth_meters",
            "target_distance_meters",
            "task_id",
        }
        metadata = {key: payload.get(key) for key in keep_keys if key in payload}
        task = payload.get("task")
        if isinstance(task, dict):
            result = task.get("result")
            if isinstance(result, dict):
                metadata["task_result"] = {
                    key: result.get(key)
                    for key in keep_keys
                    if key in result and key not in {"image_base64"}
                }
        return metadata

    def _begin_execution_trace(self, run: RunState, content: str = "") -> None:
        if not run.plan:
            return
        run.phase = "executing" if run.execute else "planning"
        if content:
            self._append_thought(run, "思考", content)
            self._append_process(run, "理解任务", content, status="completed", kind="system")
        else:
            overview = self._thought_overview(run)
            self._append_thought(run, "理解任务", overview)
            self._append_process(run, "理解任务", overview, status="completed", kind="system")
        tools = " → ".join(step.tool for step in run.plan.steps if step.tool and step.tool != "memory_store")
        if tools:
            self._append_thought(run, "工具选择", tools)
            self._append_process(run, "选择工具", tools, status="completed", kind="plan")
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._publish_run_update(run)
        self._frontend_render_grace()

    def _update_execution_trace_for_step(
        self,
        run: RunState,
        step: MissionStep,
        index: int,
        total: int,
    ) -> None:
        if step.tool == "memory_store":
            return
        self._append_thought(
            run,
            f"调用 {step.tool}",
            f"{index}/{total} · {step.label}",
            status="running",
        )
        self._append_process(
            run,
            self._tool_action_label(step.tool),
            f"{index}/{total} · {step.label}",
            status="running",
            tool=step.tool,
            params=step.params,
            kind="tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._frontend_render_grace(0.08)

    def _plan_step_body(self, step: Any, label: str, message: str, ok: bool) -> str:
        """计划执行路径的时间线正文：首行摘要 + 完整数据。

        这条路径以前只写 "{label} → {message}"，展开后看不到任何返回值，
        技能激活也只显示一句 "activated skill guidance"——正文本身（指导全文、
        遥测、坐标）全丢了。
        """
        tool = str(getattr(step, "tool", "") or "")
        summary = f"{label} → {message}" if message else ("完成" if ok else "失败")
        if tool.startswith("skill:"):
            getter = getattr(self.skills, "skill_body", None)
            guidance = ""
            if callable(getter):
                try:
                    guidance = str(getter(tool) or "").strip()
                except Exception:
                    guidance = ""
            if guidance:
                return summary + chr(10) + chr(10) + guidance
            return summary
        result = getattr(step, "result", None)
        if isinstance(result, dict):
            detail = self._result_body_fallback(result, message)
            if message and detail and not detail.lstrip().startswith(message):
                return message + chr(10) + chr(10) + detail
            return detail or summary
        return summary

    def _update_execution_trace_after_step(
        self,
        run: RunState,
        step: MissionStep,
        ok: bool,
    ) -> None:
        if step.tool == "memory_store":
            return
        message = ""
        if isinstance(step.result, dict):
            message = str(step.result.get("message") or step.result.get("status") or "")
        label = self._tool_action_label(step.tool)
        self._append_thought(
            run,
            f"{step.tool} {'完成' if ok else '失败'}",
            message,
            status="completed" if ok else "failed",
        )
        self._append_process(
            run,
            label,
            self._plan_step_body(step, label, message, ok),
            status="completed" if ok else "failed",
            tool=step.tool,
            params=step.params,
            kind="skill" if str(step.tool).startswith("skill:") else "tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )

    def _tool_action_label(self, tool: str) -> str:
        labels = {
            "drone_connect": "Connect flight link",
            "drone_disconnect": "Disconnect flight link",
            "drone_list_vehicles": "List vehicles",
            "drone_get_status": "Read vehicle status",
            "drone_arm": "Arm motors",
            "drone_disarm": "Disarm motors",
            "drone_takeoff": "Take off",
            "drone_land": "Land",
            "drone_hover": "Hold position",
            "drone_fly_to": "Fly to local coordinate",
            "drone_move_relative": "Move in body frame",
            "drone_fly_velocity": "Fly by velocity",
            "drone_fly_path": "Fly waypoint path",
            "drone_upload_mission": "Upload mission",
            "drone_download_mission": "Download mission",
            "drone_clear_mission": "Clear mission",
            "drone_start_mission": "Start mission",
            "drone_get_mission_progress": "Read mission progress",
            "drone_rotate_to": "Rotate heading",
            "drone_set_mode": "Set flight mode",
            "airsim_take_photo": "Capture image",
            "airsim_get_sensors": "Read sensors",
            "airsim_get_depth_map": "Read depth map",
            "airsim_detect_objects": "Detect objects",
            "inspect_current_frame": "Inspect current frame",
            "provider_bridge_health": "Check provider bridge",
            "provider_obstacle_summary": "Read obstacle provider",
            "provider_validate_motion": "Validate motion provider",
            "airsim_task_status": "Read legacy task status",
            "airsim_task_cancel": "Cancel legacy task",
            "memory_store": "Store mission memory",
        }
        return labels.get(tool, tool.replace("_", " "))

    @staticmethod
    def _strip_plan_json_draft(text: str) -> str:
        """截掉思考文本末尾的规划/决策 JSON 草稿。

        模型思考流的结尾常带一段它正在起草的计划 JSON（{"intent": ...}）
        或纠错决策 JSON（{"action": ...}），思考块只保留自然语言推理——
        从第一段可识别的 JSON 草稿起截断。
        """
        value = str(text or "")
        m = re.search(
            r'\{\s*"(intent|summary|steps|assumptions|risk_level|task_level|execution_mode'
            r'|action|reason|is_complete|needs_replan|reflection|goal|tool|params|target)"\s*:',
            value,
        )
        if m:
            return value[: m.start()].rstrip()
        return value

    def _thought_overview(self, run: RunState) -> str:
        if not run.plan:
            return run.route_reason or "正在整理任务上下文。"
        reasoning = (run.plan.reasoning or "").strip()
        if reasoning:
            return reasoning
        if run.route_strategy == "direct" and run.plan.steps:
            tool = run.plan.steps[0].tool
            return f"这是一个明确的单步飞控意图，我直接选择 {tool}，随后用遥测回读确认结果。"
        if run.route_strategy == "template":
            return "这是结构清晰的飞行任务，我先形成可审计的工具序列，再逐步执行并校验状态。"
        if run.route_strategy == "plan_execute":
            return "这是短序列飞行任务，我采用一次性规划执行：LLM 先给出完整工具序列，runtime 逐步执行并校验，失败时再进入 Agent Loop 纠错。"
        if run.route_reason:
            return run.route_reason
        return run.plan.summary or "正在整理任务上下文。"
