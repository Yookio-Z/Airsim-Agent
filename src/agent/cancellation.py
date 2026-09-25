"""Cancellation, pause and run ownership.

The protocol that decides "should this run keep going?": the cancel flag bound to
a run id (not a bare global), the time-windowed flight abort signal, run
registration, and the pause/steer plumbing. Every race fixed in this area lives
here, so it is deliberately one module: a reviewer can see all the predicates
that answer "stop?" side by side. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from .run_state import (
    CANCEL_ABORT_WINDOW_S,
    RunState,
)
from typing import Any
import time


class CancellationMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _active_run_is_interruptible(self) -> bool:
        with self._lock:
            return bool(
                self._current
                and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}
            )

    def _active_execute_run(self) -> RunState | None:
        """正在执行的 run（执行槽被占、且当前任务确实处在进行中的状态）。"""
        # 取属性一律用 getattr：测试与嵌入式用法会用 object.__new__ 造出只带
        # 部分属性的 runtime，硬取属性会直接抛 AttributeError。
        slot = getattr(self, "_execution_slot", None)
        if slot is None or not slot.locked():
            return None
        lock = getattr(self, "_lock", None)
        if lock is None:
            return None
        with lock:
            current = getattr(self, "_current", None)
            if current is not None and str(getattr(current, "status", "") or "") in {
                "queued",
                "running",
                "paused",
                "awaiting_approval",
                "responding",
            }:
                return current
        return None

    def _run_in_progress_error(self, action: str) -> dict[str, Any] | None:
        """执行中禁止改变链路/会话：返回拒绝结果，否则 None。

        切后端会立刻断开旧控制器，而正在跑的任务没有代际校验，后续的
        takeoff/fly/land 会落到新链路上（可能是另一架飞机）；切会话则会把
        运行态从 _current 里抹掉，任务继续飞但界面上已经看不到它。
        """
        active = self._active_execute_run()
        if active is None:
            return None
        return {
            "ok": False,
            "error": f"有任务正在执行，无法{action}。请先点『停止/打断』结束任务。",
            "run_id": active.run_id,
        }

    def _take_pending_steer(self) -> list[str]:
        """取出并清空待注入的补充指令（agent loop 每轮开头调用一次）。"""
        with self._lock:
            if not self._pending_steer:
                return []
            items = list(self._pending_steer)
            self._pending_steer.clear()
            return items

    def _steer_target_run_id(self) -> str:
        """当前可接收补充指令的 run id；"" 表示只能走"中断旧任务再执行"。

        任务在规划/执行阶段都能追加要求；已经在收尾（完成/失败/取消/阻塞）
        或正在生成最终回复的任务不行——那时没有下一轮循环去消费注入的指令。
        LLM 规划要 15~40s，这期间 self._current 还没挂上（见 _plan_and_execute），
        但执行槽已被占用，此时提交的指令同样并入队列，等循环第一轮取走。
        """
        with self._lock:
            run = self._current
            if run is not None:
                if not getattr(run, "execute", False):
                    return ""
                if run.status not in {"queued", "running", "paused"}:
                    return ""
                if str(run.phase or "") in {
                    "completed", "failed", "cancelled", "blocked", "responding", "awaiting_approval",
                }:
                    return ""
                # steer 只有 Agent Loop 能消费：plan-execute 是一次性顺序执行，
                # 没有下一轮决策去读注入的指令——实测会"提示已并入但没有任何
                # 一轮拿走"。这类任务退回中断重建，绝不能让用户以为补充生效了。
                if str(getattr(run, "route_strategy", "") or "") != "agent_loop":
                    return ""
                return str(run.run_id or "")
            return ""

    def _cancel_active_work(self) -> dict[str, Any]:
        self._cancel_requested.set()
        self._cancel_requested_at = time.time()
        run_to_publish: RunState | None = None
        previous_phase = ""
        cancelled_ids: list[str] = []
        rejected_approvals: list[str] = []
        with self._lock:
            if self._current and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}:
                previous_phase = self._current.phase
                self._current.status = "cancelled"
                self._current.phase = "cancelled"
                self._current.failure_reason = "operator cancelled task"
                self._current.finished_at = time.time()
                self._current.progress = 100.0
                self._current.assistant_message = "任务已中断。"
                self._cancelled_request_ids.add(self._current.run_id)
                self._cancel_requested_run_id = self._current.run_id
                cancelled_ids.append(self._current.run_id)
                run_to_publish = self._current
            for message in self._messages:
                if message.role == "assistant" and message.status == "running" and message.run_id:
                    self._cancelled_request_ids.add(message.run_id)
                    cancelled_ids.append(message.run_id)
                    if not str(message.content or "").strip():
                        message.content = "已中断当前回复。"
                    message.status = "complete"
                    details = dict(message.details or {})
                    details["phase"] = "cancelled"
                    details["cancelled"] = True
                    message.details = details
                    message.updated_at = time.time()
            # 待批的高危动作必须一起作废：审批等待循环不读取消旗标，若不在这里
            # 拒绝并唤醒，"停止后再点批准"会把已取消的任务复活并执行飞控动作。
            for request_id, req in list(self._pending_approvals.items()):
                if req.run_id in set(cancelled_ids) or not cancelled_ids:
                    req.approved = False
                    req.event.set()
                    rejected_approvals.append(request_id)
                    self._pending_approvals.pop(request_id, None)

        # 暂停是全局布尔：暂停中取消会让固定序列执行卡在暂停轮询里出不来，
        # 一直占着执行槽（之后每个任务都要等满 60 秒然后报"旧任务未停止"）。
        # 取消是终态，解锁暂停；且 pause 不清会渗给下一个任务。
        self.supervisor.resume()

        hover_result = None
        if run_to_publish and run_to_publish.mode == "execute" and previous_phase != "responding":
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if capabilities.get("flight_control"):
                hover_result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)

        if run_to_publish:
            self._update_assistant_message(
                run_to_publish.run_id,
                "任务已中断。",
                "complete",
                self._message_details(run_to_publish),
            )
            self._publish_run_update(run_to_publish)
        self._append_event(
            "warning",
            "agent",
            "已发送中断请求",
            {
                "run_ids": sorted(set(cancelled_ids)),
                "hover_result": hover_result.to_dict() if hover_result else None,
                "rejected_approvals": rejected_approvals,
            },
        )
        return {
            "ok": True,
            "cancelled": sorted(set(cancelled_ids)),
            "hover": hover_result.to_dict() if hover_result else None,
            "rejected_approvals": rejected_approvals,
        }

    def _is_run_cancelled(self, run_id: str) -> bool:
        with self._lock:
            if run_id and run_id in self._cancelled_request_ids:
                return True
            # 全局旗标只在它确实指向这次工作时才算数：否则上一个任务的取消会
            # 顺着旗标泄漏给下一个任务（新任务第一步就被判取消）。
            return bool(
                self._cancel_requested.is_set()
                and self._cancel_requested_run_id
                and self._cancel_requested_run_id == run_id
            )

    def _clear_cancel_state(self) -> None:
        """结束一次取消请求：清旗标、时间戳与归属 run。"""
        self._cancel_requested.clear()
        self._cancel_requested_at = 0.0
        self._cancel_requested_run_id = ""

    def _active_run_cancelled(self) -> bool:
        """当前活跃 run 是否已被取消。

        AgentLoop 的 should_stop 用它，而不是读裸旗标——旗标会被后续提交
        （比如一条 chat）清掉，那样正在收尾的循环就永远看不到取消了。
        """
        with self._lock:
            current = self._current
            run_id = str(getattr(current, "run_id", "") or "")
            if run_id and run_id in self._cancelled_request_ids:
                return True
            if not self._cancel_requested.is_set():
                return False
            if not run_id:
                return True
            return self._cancel_requested_run_id == run_id

    def _flight_abort_requested(self) -> bool:
        """飞行 stop provider 的判据：是否应当打断正在执行的阻塞式飞行命令。

        急停永远算数。取消只在"刚点下停止"的时间窗内算数：旗标是全局的，
        没有任务在跑时点停止也会置位，而它以前一直留到下一次任务收尾，于是
        操作员下一次手动起飞会被立刻打断（实测：飞机明明已经起飞，界面报
        起飞失败）。时间窗既保留了"打断当前命令"的能力，又不会渗到之后。
        """
        if self.supervisor.is_emergency_stopped():
            return True
        if not self._cancel_requested.is_set():
            return False
        if not self._cancel_requested_at:
            return False
        return (time.time() - self._cancel_requested_at) <= CANCEL_ABORT_WINDOW_S

    def _should_abort_run(self, run: RunState) -> str:
        """返回中止原因，"" 表示继续。

        固定序列执行路径的唯一"要不要停"入口：_run_plan 以前只看 supervisor 的
        pause/emergency 状态、不看取消，操作员点停止后剩下的步骤照跑，还会把
        run.status 写回 running、覆盖掉 _cancel_active_work 标注的 cancelled。
        """
        if self.supervisor.is_emergency_stopped():
            return "emergency stop"
        if self._is_run_cancelled(run.run_id):
            return "operator cancelled task"
        return ""

    def _run_owns_current(self, run: RunState | None) -> bool:
        """这个 run 是否仍是"当前活跃任务"。"""
        if run is None:
            return False
        lock = getattr(self, "_lock", None)
        if lock is None:
            return False
        with lock:
            current = getattr(self, "_current", None)
            return current is not None and current.run_id == run.run_id

    def _register_current_run(self, run: RunState) -> bool:
        """登记当前 run；返回是否拿到所有权。

        计划预览不占执行槽，而这里以前是无条件覆盖 _current 的：操作员先提交
        预览、再提交执行时，预览返回后会把自己写成"当前任务"，把正在飞行的
        任务从运行态顶掉，收尾时还会连带关掉那个任务的包线看门狗、清掉它的
        补充指令与取消请求，之后 `_on_agent_loop_state` 因 run_id 不匹配早退，
        看门狗再也无法重挂。所以非执行的 run 只有在没有活跃执行任务时才能成为
        当前任务；执行 run 之间由执行槽互斥，不会重叠。
        """
        active = {"queued", "running", "paused", "awaiting_approval", "responding"}
        with self._lock:
            current = self._current
            if (
                current is not None
                and current.run_id != run.run_id
                and str(getattr(current, "mode", "") or "") == "execute"
                and str(getattr(current, "status", "") or "") in active
                and str(getattr(run, "mode", "") or "") != "execute"
            ):
                return False
            self._current = run
            self._pending_run_ids.discard(run.run_id)
        return True
