"""The operator approval gate and tool risk classification.

Which actions need a human signature on a real vehicle (risk level), and the
blocking wait that holds the worker until the operator decides. Moved verbatim
out of runtime.py.
"""

from __future__ import annotations

from .run_state import (
    RunState,
    ToolApprovalRequest,
    _REAL_VEHICLE_APPROVAL_TOOLS,
)
from .tool_cards import TOOL_CARDS
from src.modules.formation import FLIGHT_ACTIONS as FORMATION_FLIGHT_ACTIONS
from typing import Any
import time


class ApprovalsMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    @staticmethod
    def _approval_reason(tool: str, params: dict[str, Any]) -> str:
        """Approval reason including the target vehicle(s) so the operator
        sees exactly what will be controlled (multi-vehicle aware)."""
        vehicle = str((params or {}).get("vehicle_name") or "")
        base = f"governed high-risk tool call: {tool}"
        if tool == "formation_command":
            action = str((params or {}).get("action") or "")
            ids = str((params or {}).get("vehicle_ids") or "")
            detail = f"action={action}"
            if ids:
                detail += f", vehicles={ids}"
            return f"{base} ({detail})"
        if not vehicle:
            return base
        return f"{base} (vehicle={vehicle})"

    def _await_tool_approval(
        self,
        run: RunState,
        tool: str,
        params: dict[str, Any],
        risk_level: str,
        reason: str = "",
    ) -> bool:
        """Block until the operator approves/rejects one governed tool call.

        Returns True if approved, False if rejected or timed out. Updates
        ``run.status`` / ``run.failure_reason`` accordingly.
        """
        req = ToolApprovalRequest(
            run_id=run.run_id,
            command=run.command,
            tool=tool,
            params=dict(params),
            risk_level=risk_level,
            reason=reason or f"high-risk tool: {tool}",
        )
        with self._lock:
            self._pending_approvals[run.run_id] = req
            run.status = "awaiting_approval"
            run.phase = "awaiting_approval"
        self._append_event(
            "warning",
            "safety",
            f"等待操作员确认: {tool}",
            {
                "run_id": run.run_id,
                "approval": req.to_dict(),
                "message": "真机环境高风险操作，需操作员审批后方可执行",
            },
        )
        self._publish_run_update(run)
        self._publish("approval_required", {"approval": req.to_dict()})

        # Block until decision or timeout. Poll every 1s so emergency_stop and
        # operator cancel can interrupt.
        deadline = req.created_at + req.timeout_seconds
        while True:
            abort_reason = ""
            if self.supervisor.is_emergency_stopped():
                abort_reason = "emergency stop during approval"
                level = "danger"
                text = "审批期间触发急停，任务取消"
            elif self._is_run_cancelled(run.run_id):
                # 等待审批时点停止：以前只查急停和超时，于是审批请求一直挂着，
                # 操作员（或前端重试）再点"批准"就会把已取消的任务复活并执行
                # 这个高危飞控动作。
                abort_reason = "operator cancelled task during approval"
                level = "warning"
                text = "审批期间任务被中断，已作废该审批请求"
            if abort_reason:
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = abort_reason
                    run.finished_at = time.time()
                self._append_event(level, "safety", text, {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            remaining = deadline - time.time()
            if remaining <= 0:
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = "approval timeout"
                    run.finished_at = time.time()
                self._append_event("warning", "safety", "审批超时，任务取消", {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            if req.event.wait(timeout=1.0):
                break

        approved = bool(req.approved)
        # 批准之后、真正执行之前再核对一次：取消与急停都可能发生在 wait 返回
        # 到这里的瞬间，不能把审批当成"绕过取消"的后门。
        if approved:
            blocked = self._should_abort_run(run)
            if blocked:
                approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = blocked
                    run.finished_at = time.time()
                self._append_event(
                    "warning", "safety",
                    f"批准到达时任务已被中止（{blocked}），不执行该动作",
                    {"run_id": run.run_id, "tool": tool},
                )
                self._cleanup_approval(run.run_id)
                self._publish_run_update(run)
                return False
        with self._lock:
            if approved:
                run.status = "running"
                run.phase = "executing"
                self._append_event("info", "safety", f"操作员已确认，开始执行: {tool}", {"run_id": run.run_id})
            else:
                run.status = "cancelled"
                run.phase = "cancelled"
                run.failure_reason = "operator rejected"
                run.finished_at = time.time()
                self._append_event("warning", "safety", "操作员拒绝，任务取消", {"run_id": run.run_id})
        self._cleanup_approval(run.run_id)
        self._publish_run_update(run)
        return approved

    def _cleanup_approval(self, run_id: str) -> None:
        with self._lock:
            self._pending_approvals.pop(run_id, None)

    def approve_run(self, run_id: str) -> dict[str, Any]:
        """Operator approves a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = True
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "approved"}

    def reject_run(self, run_id: str) -> dict[str, Any]:
        """Operator rejects a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = False
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "rejected"}

    def _tool_risk_level(
        self,
        tool: str,
        capabilities: dict[str, Any],
        run: RunState | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        card = TOOL_CARDS.get(tool)
        if card is not None:
            card_risk = str(card.risk)
        elif tool in self.tools.CONTROL_TOOLS:
            # 缺卡片的飞控工具不能默认成 low——审批门只在 high 时开，缺卡片
            # 等于高危动作无签字直通。按 fail-safe 判为 high；卡片缺失本身由
            # tool_cards 契约测试负责补齐。
            card_risk = "high"
        else:
            card_risk = "low"
        if run and run.route_strategy == "direct" and run.plan and any(step.tool == tool for step in run.plan.steps):
            route_risk = {
                "safe": "low",
                "elevated": "medium",
                "high": "high",
            }.get(str(run.risk_level), str(run.risk_level))
            risk = self._max_risk(route_risk, card_risk)
        else:
            risk = card_risk
        if capabilities.get("real_vehicle"):
            # 真机上"改变位置/模式"的动作必须签字：这些工具的卡片风险是
            # medium，而审批门只在 high 时开，于是模型可以不经确认把飞机挪到
            # 围栏内任意位置，或切到 AUTO（AUTO 会启动已上传的航线）。降落早已
            # 特判为 high，这里把同类动作补齐；drone_disarm 在空中等于坠机，
            # 同样必须签字。
            if tool in _REAL_VEHICLE_APPROVAL_TOOLS:
                return "high"
            if tool == "drone_rotate_to":
                risk = self._max_risk(risk, "medium")
        if tool == "drone_land" and capabilities.get("real_vehicle"):
            return "high"
        if tool == "formation_command" and capabilities.get("real_vehicle"):
            action = str((params or {}).get("action") or "status")
            if action in {"set_drones", "set_formation"} or action in FORMATION_FLIGHT_ACTIONS:
                return "high"
        return risk if risk in {"low", "medium", "high"} else "medium"

    def _max_risk(self, first: str, second: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        first = first if first in order else "medium"
        second = second if second in order else "medium"
        return first if order[first] >= order[second] else second

    def _preapprove_first_high_risk_tool(self, run: RunState) -> dict[str, Any] | None:
        if not run.plan or run.risk_level != "high":
            return None
        runtime = self.tools.status_snapshot()
        capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
        if not capabilities.get("requires_operator_approval"):
            return None
        for step in run.plan.steps:
            risk_level = self._tool_risk_level(step.tool, capabilities, run)
            if risk_level != "high":
                continue
            approved = self._await_tool_approval(
                run,
                step.tool,
                dict(step.params),
                risk_level,
                reason=self._approval_reason(step.tool, dict(step.params)),
            )
            return {
                "approved": approved,
                "tool": step.tool,
                "params": dict(step.params),
                "risk_level": risk_level,
            }
        return None
