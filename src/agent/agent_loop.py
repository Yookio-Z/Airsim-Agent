"""Lightweight observe-decide-act loop for advanced UAV tasks."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from .command_slots import MOTION_TERMS, extract_intents
from .llm import LLMMissionPlanner, _STEPS_DIGEST_TIGHT_LIMIT
from .loop_types import LoopActionResult, LoopDecision, LoopObservation, LoopState
from .memory import AgentMemory
from .planner import MissionPlan
from .skill_registry import SkillRegistry
from .tool_executor import ToolCallResult, ToolRuntime
from ..logging_config import get_logger

logger = get_logger(__name__)


LoopEventCallback = Callable[[str, str, str, dict[str, Any]], None]
LoopStopCheck = Callable[[], bool]
LoopPauseCheck = Callable[[], bool]
AgentToolExecutor = Callable[[str, dict[str, Any], bool], ToolCallResult]
LoopStateCallback = Callable[[LoopState], None]
# 执行中由操作员追加的补充指令（steer）：每次调用返回并清空待注入的指令。
LoopSteerProvider = Callable[[], list[str]]

# 计划中"可验证目标达成"的运动类工具：LLM 声明任务完成前，这些动作
# 必须在该循环内留下一次成功执行记录（或被状态回读证明目标已达成）。
_PLANNED_MOTION_TOOLS = {
    "drone_takeoff",
    "drone_hover",
    "drone_move_relative",
    "drone_fly_to",
    "drone_fly_path",
    "drone_land",
    "drone_rotate_to",
}

# 计划步骤"连续偏离"多少次后才放弃该步。第一次偏离只是把控制权交回 LLM 做一次
# 纠错（例如先搜索/对准目标），下一轮回到原步骤重新判断；只有同一步骤连续偏离
# 到这个次数，才认为它当前确实不可行并跳过。
_PLAN_DEVIATION_RETRIES = 2

# 依赖相机画面的工具：任务用过其中任意一个，感知停滞就必须让任务停下来。
# 检测类工具已随 YOLO 一起移除；现在依赖画面的是"看图"和"几何测距"这两条。
_PERCEPTION_TOOLS = {
    "perception_status",
    "locate_image_point",
    "inspect_current_frame",
}

# 画面停滞多久算异常。相机 4~5 FPS，正常帧龄 < 1s；AirSim 卡住时采集会持续
# 超时，帧龄单调增长。20 秒留足瞬时抖动的余量，避免误停任务。
_PERCEPTION_STALL_S = 20.0

# 只读观察工具：在"上一步要求先观察"的那一轮，模型可能先补一次观察（读深度、
# 再看一眼）而不是直接动作。这时计划游标既不推进也不丢弃——下一轮继续用新观察
# 决定动作，避免"补观察"被当成偏离计划而把整份计划作废。
_OBSERVATION_TOOLS = {
    "drone_get_status",
    "perception_status",
    "locate_image_point",
    "inspect_current_frame",
}

# 视觉观察工具：它们的**结果内容**决定下一步运动参数（视觉给点、深度定步长）。
# 计划里"视觉观察 → 运动"这个形状意味着运动参数必须由观察推算，不能执行计划里
# 猜好的字面参数——判据是结构性的，不依赖模型有没有给步骤标 needs_observation。
_VISUAL_OBSERVATION_TOOLS = {
    "inspect_current_frame",
    "locate_image_point",
}

# "只观察不动作"的连续轮数上限。到了上限就按计划把那一步执行掉（有界单步、安全层
# 照常校验），不再把决定权踢回模型空转——实测模型能连着 6 轮只读深度/状态/技能
# 指导，一次都不动，最后撞上模型空回复让整个任务失败。
_OBSERVATION_GAP_LIMIT = 3

# 近距接近的站离（米）：既用于判断"够近了吗"，也用于否决"会飞到/飞过目标"的
# drone_fly_to（见 _approach_overshoot_reason）。
_APPROACH_STANDOFF_M = 2.0

# 否决"飞过目标"动作的次数上限：到上限后放行并留一条警告（模型已经被告知三次）。
_OVERSHOOT_VETO_LIMIT = 3

# "已经到位"后仍然继续逼近的否决上限。到上限由运行时收尾——这就是"怎么算做到了"
# 的判定标准：不能只由模型自己认定（2026-10-13 实跑：模型一路微调逼近，28 步撞上限）。
_REACHED_VETO_LIMIT = 2

# 无进展（stagnation）监控：任务声明的**可测量必需判据**连续多轮没有改善 → 认为卡住。
# 这是通用机制（不含任何任务类型常数）：文献里"无进展检测 + 升级阶梯"的做法——
# 先提醒换策略、重复动作被否决、仍无进展则如实收尾。实测的"无限逼近/反复观察空转"
# 正是这条要收口的东西。
_STALL_WINDOW = 4  # 连续多少轮没改善算一个"停滞窗口"
_STALL_LIMIT = 2  # 连续多少个停滞窗口后如实收尾
_STALL_FILL_MARGIN = 0.05  # 画面比例的最小改善量
_STALL_DISTANCE_MARGIN_M = 0.5  # 距离的最小改善量（米）
_STALL_REPEAT_LOOKBACK = 4  # 判"重复动作"时回看的执行条数
# 到位后允许的最大微调步长（米）：贴着目标再走大步会丢目标/撞上去。
_REACHED_MICRO_STEP_M = 1.0


class AgentLoop:
    """Small ReAct-style loop that stays close to the existing tool runtime."""

    def __init__(
        self,
        tools: ToolRuntime,
        planner: LLMMissionPlanner,
        memory: AgentMemory,
        on_event: LoopEventCallback | None = None,
        should_stop: LoopStopCheck | None = None,
        should_pause: LoopPauseCheck | None = None,
        skills: SkillRegistry | None = None,
        execute_tool: AgentToolExecutor | None = None,
        on_state: LoopStateCallback | None = None,
        steer_provider: LoopSteerProvider | None = None,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.skills = skills or SkillRegistry()
        self.on_event = on_event
        self.should_stop = should_stop
        self.should_pause = should_pause
        self.execute_tool = execute_tool
        self.on_state = on_state
        self.steer_provider = steer_provider
        # 观察者异常只记录、不上抛：回显/UI 镜像跑在循环的调用栈上，让它把异常
        # 抛穿等于"界面出错就毁掉任务"（实测：一个格式化辅助函数签名写错，整个
        # execute 任务在 Agent Loop 第一次出结果时崩掉，飞机停在半路）。这里把它
        # 有界地记下来，并在时间线上留一条事件，循环继续跑。
        self._observer_failures: list[dict[str, Any]] = []
        self._observer_failure_announced = False

    def run(
        self,
        run_id: str,
        command: str,
        capabilities: dict[str, Any],
        tool_cards: list[dict[str, Any]],
        initial_plan: MissionPlan | None = None,
        model_id: str | None = None,
        max_steps: int = 10,
        initial_plan_cursor: int = 0,
        execute: bool = True,
        attachments: list[dict[str, Any]] | None = None,
        require_llm: bool = False,
        conversation_context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        fallback_enabled: bool = True,
        reactive: bool = False,
        keep_reacting: bool = False,
    ) -> LoopState:
        # 观察者失败是按"本次运行"计的：AgentLoop 实例是常驻的，不清空会把上次
        # 运行的界面故障算到这次头上。
        self._observer_failures = []
        self._observer_failure_announced = False
        state = LoopState(
            run_id=run_id,
            command=command,
            status="running",
            original_plan=initial_plan.to_dict() if initial_plan else None,
            max_steps=max_steps,
        )
        memory_snapshot = self.memory.snapshot()
        guidance_loader = getattr(self.skills, "guidance_cards", None)
        skill_guidance = guidance_loader(command, capabilities, memory_snapshot) if callable(guidance_loader) else []
        # 技能卡片必须进决策面：技能是"这类任务该怎么做"的指导，模型激活后会拿到
        # 完整步骤说明再调用底层工具。以前这里固定传空列表，技能永远进不了循环的
        # 候选动作，操作员也就永远看不到技能参与（实测反馈）。
        decision_cards = self._decision_cards(
            command, self._skill_decision_cards(capabilities), tool_cards, capabilities
        )
        # 白名单必须来自"真实注册的工具"，而不是提示用的卡片列表——卡片为了
        # 控制 prompt 体积会截断（32 张），一旦某工具被挤出，LLM 选了它就会
        # 被误判成"当前后端不可用"。
        allowed_tools = self._allowed_tools(decision_cards)
        registered = getattr(self.tools, "list_tools", None)
        if callable(registered):
            try:
                names = {
                    str(item.get("name") or "")
                    for item in registered()
                    if isinstance(item, dict)
                }
                names.discard("")
                if names:
                    allowed_tools = names
            except Exception:
                pass
        # Skill documents are activatable actions, not just prompt text: the
        # model loads a skill's guidance by emitting its action name, so the
        # name has to survive _sanitize_decision. Added after the registered-tool
        # replacement above, which would otherwise wipe them.
        activatable_loader = getattr(self.skills, "activatable", None)
        if callable(activatable_loader):
            try:
                allowed_tools |= {str(name) for name in activatable_loader(capabilities) if name}
            except Exception:
                pass
        last_result: dict[str, Any] | None = None
        failure_count = 0
        connection_failures = 0
        unresolved_failure = False
        replan_count = 0
        verify_corrected = False
        # "只观察不动作"的连续轮数（见 _OBSERVATION_GAP_LIMIT）。
        gap_rounds = 0
        # 上下文压力事件每次运行最多报一次。
        context_pressure_announced = False
        # "会飞过目标"的动作被否决的次数（见 _OVERSHOOT_VETO_LIMIT）。
        overshoot_vetoes = 0
        # "到位"判定是否已经播报过（每次运行一次）+ 到位后继续逼近被否决的次数。
        approach_reached_announced = False
        reached_vetoes = 0
        # 判据状态的播报记录（变化才播，避免每轮刷屏）。
        criteria_status_seen: dict[str, bool] = {}
        # 判据"持续成立"的起点时间（hold_s 用；每次运行清空）。
        self._criteria_hold: dict[str, float] = {}
        # 无进展监控的运行态（每次运行清空）。
        self._stall_series: dict[str, float] = {}
        stall_rounds = 0
        stall_vetoes = 0
        # 计划游标：>0 表示正在按 LLM 已给出的计划顺序执行，无需每步再花一次
        # LLM 决策（dots-studio 单次 15~40s，逐步思考会把普通任务拖到几分钟）。
        # 一旦步骤失败/偏离计划就置 -1，交回 LLM 做 ReAct 纠错。
        # 计划游标起点：纠错重入时从"已完成前缀"之后继续，避免把已经成功
        # 执行过的步骤（连接/起飞/检测…）整条重跑。
        plan_cursor = max(0, int(initial_plan_cursor or 0))
        # 每个计划步骤的连续偏离计数（见 _PLAN_DEVIATION_RETRIES）。
        plan_deviation_counts: dict[int, int] = {}

        for step_index in range(1, max_steps + 1):
            if self._should_stop():
                state.status = "blocked"
                state.failure_reason = "supervisor emergency stop"
                break
            self._wait_if_paused()
            steered = self._take_steer()
            if steered:
                # 操作员在执行中追加的要求：并入命令文本后模型下一轮就能看到，
                # 任务、计划和已完成的动作全部保留——不需要推倒重来，也就不会
                # 触发中断收尾的降落。
                command = "\n".join([command, *[f"[操作员补充指令] {item}" for item in steered]])
                self._event(
                    "info", "agent_loop",
                    "已并入操作员补充指令",
                    {"step": step_index, "steer": steered},
                    kind="steer",
                )

            observation = LoopObservation(
                step_index=step_index,
                world_state=self.tools.status_snapshot(),
                last_action_result=last_result,
                elapsed_since_start=time.time() - state.started_at,
                frame_age_s=self._latest_image_age(state),
            )
            state.observations.append(observation)
            self._notify_state(state)
            self._event("info", "agent_loop", f"Observation {step_index}", observation.to_dict(), kind="observation")

            stall = self._perception_stall(observation, state)
            if stall:
                # 相机链路卡住但飞控心跳照旧时，循环会继续基于陈旧画面决策。
                # 依赖感知的任务必须停下来（保持悬停），而不是接着盲飞。
                state.status = "blocked"
                state.failure_reason = stall
                self._event(
                    "danger", "agent_loop",
                    "感知画面长时间没有更新，任务已停止（飞机保持悬停）",
                    {"detail": stall, "step": step_index},
                    kind="perception",
                )
                break

            decision = None if require_llm else self._preemptive_guard_decision(command, state, observation, allowed_tools, capabilities)
            plan_decision: LoopDecision | None = None
            deviation_reason = ""
            resume_cursor = -1
            # 计划里标了 needs_observation 的步骤，其**结果**决定下一步的参数
            # （视觉给点、深度定步长），所以紧跟其后的这一步必须由模型基于最新
            # 观察重新给出，不能照计划里猜好的字面参数执行。以前这个字段只被
            # 解析、从不消费——表现就是"Agent 不看刚量到的距离就飞"。
            previous_step = self._plan_step_at(initial_plan, plan_cursor - 1)
            upcoming_step = self._plan_step_at(initial_plan, plan_cursor)
            previous_tool = str(getattr(previous_step, "tool", "") or "")
            upcoming_tool = str(getattr(upcoming_step, "tool", "") or "")
            previous_ran = bool(previous_tool) and self._has_successful_tool(state, previous_tool)
            # 交回模型的目的**只有一个**：下一步是运动，而它的参数要由刚才那次观察
            # 推算。所以判据必须同时要求"下一步是运动步骤"——否则给 telemetry /
            # 深度这类观察步骤标了 needs_observation，也会把下一轮白交给模型空转
            # （实测跑飞点：get_status 之后一轮、inspect 之后的 depth 又来一轮）。
            observation_gap = bool(
                upcoming_tool in _PLANNED_MOTION_TOOLS
                and previous_ran
                and (
                    # 显式声明：模型自己标了"这一步的结果要先看"
                    bool(getattr(previous_step, "needs_observation", False))
                    # 结构判据：视觉观察之后紧跟运动步骤。运行时注入的观察步骤
                    # （以及模型忘标 flag 的情况）靠这一条兜住。
                    or previous_tool in _VISUAL_OBSERVATION_TOOLS
                )
            )
            if decision is None:
                # 优先按计划执行（快）：计划是 LLM 已经想好的，逐字执行不必每步
                # 再问一次。只在"观察偏离计划假设"或"上一步要求先观察"时才交给
                # LLM 重新决策，即按需 ReAct。
                plan_step = self._plan_step_at(initial_plan, plan_cursor)
                if reactive and plan_step is not None and not observation_gap:
                    deviation_reason = self._plan_step_deviation(plan_step, state)
                if deviation_reason:
                    planned_tool = str(getattr(plan_step, "tool", ""))
                    attempts = plan_deviation_counts.get(plan_cursor, 0) + 1
                    plan_deviation_counts[plan_cursor] = attempts
                    if attempts >= _PLAN_DEVIATION_RETRIES:
                        # 同一步骤反复偏离：它当前确实不可行，跳过以免死循环。
                        plan_deviation_counts.pop(plan_cursor, None)
                        resume_cursor = plan_cursor + 1
                        self._event(
                            "warning", "agent_loop",
                            f"持续与计划不符（{deviation_reason}）：跳过这一步",
                            {"step": step_index, "planned_tool": planned_tool, "attempts": attempts},
                            kind="replan",
                        )
                    else:
                        # 保留游标：本轮交给 LLM 纠错（搜索/对准），下一轮回到这一步
                        # 重新判断，而不是把关键动作永久跳过。
                        resume_cursor = plan_cursor
                        self._event(
                            "warning", "agent_loop",
                            f"画面与计划不符（{deviation_reason}）：交给模型重新决定，稍后回到这一步",
                            {"step": step_index, "planned_tool": planned_tool, "attempts": attempts},
                            kind="replan",
                        )
                elif observation_gap and gap_rounds >= _OBSERVATION_GAP_LIMIT:
                    # 连续 N 轮"只观察不动作"：不再把球踢回模型空转，直接按计划执行
                    # 这一步（有界单步、安全层照常校验）。实测：模型能连着 6 轮只读
                    # 深度/状态/技能指导、一次都不动，最后撞上模型空回复而失败。
                    plan_decision = self._plan_step_decision(initial_plan, plan_cursor, allowed_tools)
                    decision = plan_decision
                    self._event(
                        "warning", "agent_loop",
                        f"连续 {gap_rounds} 轮只看不动：按计划执行 {str(getattr(plan_decision, 'action', '') or '下一步')}",
                        {"step": step_index, "gap_rounds": gap_rounds, "planned_tool": upcoming_tool},
                        kind="replan",
                    )
                elif not observation_gap:
                    plan_decision = self._plan_step_decision(initial_plan, plan_cursor, allowed_tools)
                    decision = plan_decision
            if decision is None:
                if observation_gap:
                    gap_rounds += 1
                    # 交回模型前留一条可见的说明：操作员要能看出这一轮为什么没按
                    # 计划走（上一步是"先观察"的步骤）。
                    self._event(
                        "info", "agent_loop",
                        "看清目标后：下一步改由模型按最新数据决定（不用计划里预想的参数）",
                        {
                            "step": step_index,
                            "observed_tool": previous_tool,
                            "planned_next": upcoming_tool,
                            "gap_rounds": gap_rounds,
                        },
                        kind="replan",
                    )
                decision = self.planner.decide_next_step(
                    command=command,
                    loop_state=state.to_dict(),
                    observation=observation.to_dict(),
                    tool_cards=decision_cards,
                    capabilities=capabilities,
                    memory=memory_snapshot,
                    model_id=model_id,
                    attachments=attachments or [],
                    require_llm=require_llm,
                    skill_guidance=skill_guidance,
                    tools=self._tool_specs(),
                    system_prompt=system_prompt,
                    fallback_enabled=fallback_enabled,
                    conversation_context=conversation_context,
                )
                decision = self._guard_decision(command, state, observation, decision, allowed_tools, capabilities)
                # 上下文接近模型窗口：本轮起已自动压缩历史（见 planner 的
                # _context_pressure）。这件事必须看得见，否则下次又是一句
                # 莫名其妙的"变慢了/想不起来了"。每次运行最多报一次。
                pressure = getattr(self.planner, "last_context_pressure", None)
                if pressure and not context_pressure_announced:
                    context_pressure_announced = True
                    self._event(
                        "warning",
                        "agent_loop",
                        (
                            f"对话变长接近模型上限（{pressure.get('prompt_tokens')}/"
                            f"{pressure.get('context_window')} tokens）：本轮起压缩历史"
                            f"）：已自动精简历史，不影响任务（摘要 {_STEPS_DIGEST_TIGHT_LIMIT} 行）"
                        ),
                        {"step": step_index, **pressure},
                        kind="replan",
                    )
                if observation_gap:
                    current_step = self._plan_step_at(initial_plan, plan_cursor)
                    current_tool = str(getattr(current_step, "tool", "") or "")
                    if current_tool and decision.action == current_tool:
                        # 模型按计划选了同一个工具（参数来自最新观察）→ 该步骤已兑现
                        plan_cursor = plan_cursor + 1
                        gap_rounds = 0
                    elif decision.action in _OBSERVATION_TOOLS or str(decision.action or "").startswith("skill:"):
                        # 补一次观察 / 重读技能指导 → 都不算偏离计划：保留游标，
                        # 下一轮继续（计数上一轮已经加过，连续空转由
                        # _OBSERVATION_GAP_LIMIT 兜底）。
                        # 实测教训：模型在这里重新激活了同一个技能而不是给出动作，
                        # 旧逻辑把它当"偏离"→ 整份计划作废 → 之后 6 轮纯空转。
                        pass
                    else:
                        # 换了别的动作 → 视为偏离计划，后续交回 LLM
                        plan_cursor = -1
                elif resume_cursor >= 0:
                    # 因"观察偏离计划"而临时转 ReAct：本轮由 LLM 决策，但保留计划
                    # 游标，下一轮继续按原计划推进（避免一次偏离就把整个任务变成
                    # 每步都问模型）。
                    plan_cursor = resume_cursor
                else:
                    # 计划走完/不可用等：后续不再自动按计划推进
                    plan_cursor = -1
            decision = self._sanitize_decision(decision, allowed_tools)
            # 防"飞过目标"（实测事故：直飞一个从路面深度推出来的坐标，掠过目标、
            # 目标出画）。否决时交回模型并说明该怎么走，不偷偷改它的参数。
            overshoot = self._approach_overshoot_reason(decision, state, observation, command)
            if overshoot:
                if overshoot_vetoes < _OVERSHOOT_VETO_LIMIT:
                    overshoot_vetoes += 1
                    self._event(
                        "warning",
                        "agent_loop",
                        f"否决一次可能飞过目标的动作：{overshoot}",
                        {"step": step_index, "veto": overshoot_vetoes, "action": decision.action},
                        kind="replan",
                    )
                    decision = LoopDecision(
                        action="",
                        reason=overshoot,
                        is_complete=False,
                        needs_replan=True,
                        source="guard",
                    )
                else:
                    self._event(
                        "warning",
                        "agent_loop",
                        "同一个「飞过目标」的动作已被否决 3 次：放行这一次，请人工关注画面",
                        {"step": step_index, "action": decision.action},
                        kind="replan",
                    )

            # "到位"判据（运行时的停止标准）：到位后拒绝继续大步逼近；连续拒绝到上限
            # 就由运行时收尾。判定不能只由模型自己认定——这就是"怎么算做到了"。
            # 判据可视化：状态变化时播报一次（"怎么算做到、现在到哪了"）。
            for row in self._declared_criteria_status(state, initial_plan, observation):
                key = f"{row['metric']}:{row['detail']}"
                if criteria_status_seen.get(row["metric"]) == row["satisfied"]:
                    continue
                criteria_status_seen[row["metric"]] = row["satisfied"]
                self._event(
                    "info",
                    "agent_loop",
                    f"完成判据 {row['metric']}：{'已满足' if row['satisfied'] else '未满足'}（{row['detail']}）",
                    {"step": step_index, **row},
                    kind="replan",
                )
            reached, reached_source = self._stopping_criterion_status(state, initial_plan, observation)
            if reached and not approach_reached_announced:
                approach_reached_announced = True
                self._event(
                    "info",
                    "agent_loop",
                    (
                        f"已到达任务判据（{reached}）：接下来悬停/汇报即可，不再继续逼近"
                        if reached_source == "declared"
                        else f"已到达目标身边（{reached}）：接下来悬停/汇报即可，不再继续逼近"
                    ),
                    {"step": step_index, "criterion": reached, "criterion_source": reached_source},
                    kind="replan",
                )
            if reached and self._is_further_approach(decision, state, observation):
                if reached_vetoes < _REACHED_VETO_LIMIT:
                    reached_vetoes += 1
                    reason = (
                        f"已经到位（{reached}）：不要再继续逼近——继续靠近会飞出画面或撞上去。"
                        f"必要时最多只允许 ≤{_REACHED_MICRO_STEP_M:.0f} m 的微调，然后悬停并汇报。"
                    )
                    self._event(
                        "warning",
                        "agent_loop",
                        f"否决一次继续逼近的动作：{reason}",
                        {"step": step_index, "veto": reached_vetoes, "action": decision.action},
                        kind="replan",
                    )
                    decision = LoopDecision(
                        action="",
                        reason=reason,
                        is_complete=False,
                        needs_replan=True,
                        source="guard",
                    )
                else:
                    self._event(
                        "info",
                        "agent_loop",
                        f"已到位（{reached}）且模型仍在逼近：运行时按到达收尾",
                        {"step": step_index, "vetoes": reached_vetoes},
                        kind="replan",
                    )
                    decision = LoopDecision(
                        action="",
                        reason=f"已到达目标身边（{reached}），任务收尾。",
                        is_complete=True,
                        source="guard",
                    )

            # 无进展监控（通用，不含任务类型常数）：任务声明的**可测量必需判据**连续
            # 多轮没有改善 → 提醒换策略、否决重复动作、仍无进展则如实收尾。
            # 这是对"反复微调/原地观察"这类空转的运行时收口，且不替任务发明标准——
            # 没有声明判据时这条永远不触发。
            stall = self._stall_reason(state, initial_plan, observation)
            if stall:
                stall_rounds += 1
                if stall_rounds >= _STALL_WINDOW * _STALL_LIMIT:
                    self._event(
                        "info",
                        "agent_loop",
                        f"连续没有进展（{stall}）：按现状收尾，未满足的判据会如实报告",
                        {"step": step_index, "rounds": stall_rounds, "detail": stall},
                        kind="verification",
                    )
                    decision = LoopDecision(
                        action="",
                        reason=f"没有进展（{stall}），按现状收尾。",
                        is_complete=True,
                        source="guard",
                    )
                elif self._is_repeat_action(decision, state) and stall_vetoes < _STALL_LIMIT:
                    stall_vetoes += 1
                    reason = (
                        f"没有进展（{stall}），而这一步与刚做过的动作完全相同：换一个策略——"
                        "换角度/换工具/或如实报告卡在哪里，不要重复同一个动作。"
                    )
                    self._event(
                        "warning",
                        "agent_loop",
                        f"否决一次重复动作：{reason}",
                        {"step": step_index, "veto": stall_vetoes, "action": decision.action},
                        kind="replan",
                    )
                    decision = LoopDecision(
                        action="", reason=reason, is_complete=False, needs_replan=True, source="guard"
                    )
                elif stall_rounds % _STALL_WINDOW == 0:
                    self._event(
                        "warning",
                        "agent_loop",
                        f"没有进展（{stall}）：请换策略（换角度 / 换工具 / 或如实报告）",
                        {"step": step_index, "rounds": stall_rounds, "detail": stall},
                        kind="replan",
                    )
            else:
                stall_rounds = 0
                stall_vetoes = 0
            if plan_decision is not None and decision.action != plan_decision.action:
                # 守卫/清理改写了动作 → 视为偏离计划，后续交回 LLM
                plan_cursor = -1
            # 本轮决策是在一次 15~40 秒的模型往返之前取得的观察上做出的，期间
            # 操作员可能已经暂停/取消/急停。以前这里不复查：UI 已经显示"已暂停"，
            # 模型返回后那个动作（可能是起飞/移动）仍然会执行。决策已过期就丢弃，
            # 回到循环顶部重新观察后再决策。
            if self._should_stop():
                state.status = "blocked"
                state.failure_reason = "supervisor emergency stop"
                break
            if self._paused_now():
                self._event(
                    "info", "agent_loop",
                    "决策期间任务被暂停，丢弃本轮决策，等待恢复后重新观察",
                    {"step": step_index, "discarded_action": decision.action},
                    kind="loop.decision",
                )
                continue
            # 本轮模型思考必须先于决策/工具行写入时间线，否则前端会出现
            # "先调工具、后显示思考"的顺序倒错。两种决定不发思考事件：
            # - plan：按计划推进，没有新的模型思考（避免复用上一轮的陈旧推理）；
            # - guard：reason/reflection 是守卫写的，不是模型的思考。以前它混进
            #   "模型思考"里，操作员看到的就是"模型一直说要抵近"——那句话其实是
            #   守卫的字符串。守卫决定单独显示为"运行时守卫"，这里只保留模型
            #   这一轮真实产生过的推理文本（如果有）。
            decision_source = str(getattr(decision, "source", "llm") or "llm")
            if decision_source != "plan":
                reasoning_text = str(getattr(self.planner, "last_reasoning", "") or "").strip()
                if decision_source == "guard":
                    combined_reasoning = reasoning_text
                else:
                    decision_rationale = str(decision.reason or decision.reflection or "").strip()
                    combined_reasoning = "\n".join(part for part in (reasoning_text, decision_rationale) if part)
                if combined_reasoning:
                    self._event("info", "model_reasoning", combined_reasoning[:1500], {"step": step_index})
            state.decisions.append(decision)
            self._notify_state(state)
            self._event("info", "agent_loop", f"Loop decision {step_index}: {decision.action or 'complete'}", decision.to_dict(), kind="loop.decision")

            if decision.is_complete:
                goal = self._run_goal(initial_plan)
                verification = self._verify_completion(goal, state, observation)
                if verification["criteria"]:
                    if not verification["satisfied"] and not verify_corrected:
                        corrective = self._corrective_decision(verification, state, allowed_tools)
                        if corrective is not None:
                            verify_corrected = True
                            corrective = self._sanitize_decision(corrective, allowed_tools)
                            self._event(
                                "warning",
                                "verification",
                                f"完成判据未满足，先执行纠正动作: {corrective.action or '状态回读'}",
                                verification,
                                kind="verification",
                            )
                            decision = corrective
                            # record the corrective decision in the audit trail
                            state.decisions.append(corrective)
                            self._notify_state(state)
                        else:
                            state.verification_status = "failed"
                            self._event("warning", "verification", "完成判据无法满足且无纠正路径", verification, kind="verification")
                    elif not verification["satisfied"]:
                        state.verification_status = "failed"
                        self._event("warning", "verification", "完成判据仍未满足（已执行一次纠正）", verification, kind="verification")
                    else:
                        # 判据满足 ≠ 全部判据都被评估过：缺遥测时判据会被判为
                        # "无法评估"，此时运行照常收尾，但状态必须如实标成
                        # unverified，否则"没有证据"会被读成"已验证通过"。
                        unevaluated = list(verification.get("unevaluated") or [])
                        if unevaluated:
                            state.verification_status = "unverified"
                            state.verification_unevaluated = unevaluated
                            self._event(
                                "warning",
                                "verification",
                                "任务收尾，但有判据无法评估（缺遥测等），不能视为已验证",
                                verification,
                                kind="verification",
                            )
                        else:
                            state.verification_status = "ok"
            if decision.is_complete:
                if unresolved_failure:
                    state.status = "failed"
                    state.failure_reason = decision.reflection or decision.reason or "task stopped without recovering from the previous failure"
                elif state.verification_status == "failed":
                    # 判据未满足（给过一次纠正机会）。这里保留 completed：循环层的
                    # status 表达"模型请求收尾"，机器否决由 verification_status 承载，
                    # 运行层的 gate（execution.py 的 failed-verification gate）据此把
                    # 整次运行判成未通过。报告口径见 _summary/_caveat_note——状态语义
                    # 不动，但话不能说得像成功。
                    state.status = "completed"
                    state.failure_reason = ""
                else:
                    state.status = "completed"
                    # a recovered earlier failure must never leak into the
                    # final state: "completed" carries no failure reason
                    state.failure_reason = ""
                    state.summary = decision.reason or decision.reflection or "agent loop completed"
                break
            if not decision.action:
                if decision.needs_replan and replan_count < 2:
                    replan_count += 1
                    handback_reason = decision.reflection or decision.reason or "运行时拒绝了本轮动作"
                    last_result = {
                        "ok": False,
                        "data": {"status": "replan_requested", "message": handback_reason},
                    }
                    # 交回模型必须可见：操作员要看得到"运行时拒绝了什么、为什么、
                    # 现在由模型重新决策"，而不是只在事件流水里看到一句英文占位。
                    self._event(
                        "warning",
                        "agent_loop",
                        f"运行时交回模型重新决策（{replan_count}/2）：{handback_reason}",
                        decision.to_dict(),
                        kind="replan",
                    )
                    continue
                state.status = "failed"
                state.failure_reason = decision.reflection or decision.reason or "agent loop produced no executable action"
                break

            result = self._execute_action(decision, dry_run=not execute)
            result_row = LoopActionResult(
                step_index=step_index,
                tool=result["tool"],
                params=result["params"],
                ok=bool(result["ok"]),
                data=result["data"],
                safety=result.get("safety"),
                duration_ms=result["duration_ms"],
            )
            state.results.append(result_row)
            self._notify_state(state)
            self.memory.remember_tool_call(result_row.tool, result_row.ok)
            if result_row.ok and result_row.tool in _PLANNED_MOTION_TOOLS:
                # 真的动作落地了 → "只观察不动作"的连续计数清零（下一段观察重新计）
                gap_rounds = 0
            last_result = result_row.to_dict()
            self._event("info" if result_row.ok else "warning", "tool", f"Loop action {result_row.tool}", result["raw"], kind="tool.result")

            if not result_row.ok:
                failure_count += 1
                unresolved_failure = True
                # 步骤失败：停止按计划推进，后续交回 LLM 做 ReAct 纠错
                plan_cursor = -1
                state.failure_reason = str(result_row.data.get("message") or f"{result_row.tool} failed")
                # 连接熔断：后端断连/超时后继续决策只会烧 token（重连、拍照、
                # 再重连的无限循环）。连续两次连接类失败直接终止任务，让操作
                # 员先恢复链路。
                error_code = str(result_row.data.get("error_code") or "")
                message_l = state.failure_reason.lower()
                connection_failure = error_code in {"NOT_CONNECTED", "CONNECTION", "TIMEOUT", "LINK_STALE"} or any(
                    term in message_l for term in ("not connected", "connection", "timed out", "timeout", "未连接", "连接")
                )
                if connection_failure:
                    # 连接类失败：允许一次恢复机会（MAVLink 心跳抖动/瞬时
                    # 掉线很常见，单次失败就终止会让任务极不可靠）。连续两次
                    # 才熔断，避免"重连→再失败"的空转烧 token。
                    connection_failures += 1
                    if connection_failures >= 2:
                        state.status = "failed"
                        state.failure_reason = (
                            "连续检测到后端连接断开，任务已终止。"
                            "AirSim/飞控服务可能已断开，请检查服务后在连接面板重新连接，再重新下发任务。"
                        )
                        self._event(
                            "danger",
                            "agent_loop",
                            "连接连续失败，任务已终止（请检查 AirSim/飞控服务）",
                            {"tool": result_row.tool, "message": state.failure_reason[:160]},
                        )
                        break
                    # 第一次：给一轮恢复机会，并提示模型先回读连接状态
                    self._event(
                        "warning",
                        "agent_loop",
                        "连接类失败，先尝试恢复（回读连接状态）",
                        {"tool": result_row.tool, "message": state.failure_reason[:160]},
                        kind="tool.result",
                    )
                    failure_count = max(0, failure_count - 1)  # 不计入普通失败配额
                    continue
                connection_failures = 0
                if failure_count >= 3:
                    state.status = "failed"
                    break
                self._event(
                    "warning",
                    "agent_loop",
                    f"Action failed; observation/recovery turn allowed ({failure_count}/3)",
                    result_row.to_dict(),
                    kind="tool.result",
                )
                continue
            if result_row.tool not in {"drone_get_status"}:
                unresolved_failure = False

            # 本步由计划驱动且成功 → 计划游标前移，继续按计划执行下一步
            if plan_decision is not None and plan_cursor >= 0:
                plan_cursor += 1
                # 步骤已经推进，之前的偏离计数不再适用
                plan_deviation_counts.clear()

            # 确定性收敛：计划内的实质步骤（检测/视觉/运动等）都已成功执行过，
            # 就结束任务并汇报。否则 LLM 收尾时容易反复存记忆/回读状态，
            # 把一次普通任务拖成几分钟（每轮决策 15~40s）。
            # keep_reacting（持续追踪类任务）不适用：那时"计划列的动作都跑过"
            # 不等于目标仍被锁定，需要继续观察-响应直到模型判定完成。
            if execute and not keep_reacting and self._plan_steps_satisfied(state, initial_plan):
                # "计划步骤都跑过" ≠ "目标已经到达"。近距视觉任务（"飞向/靠近某个
                # 看得见的东西"）里，一次 3 m 的移动也能让所有步骤"成功"，但目标
                # 还在 20 m 外——以前就在这里收敛并汇报完成，操作员看到的是
                # "Agent 说到了，其实没动几米"。有界地交回模型继续靠近，而不是直接
                # 盖章完成；超过次数上限才按原逻辑收敛（模型仍可自行宣布完成）。
                # 收敛前必须过一遍计划自己声明的成功判据。以前这里直接盖章
                # `verification_status = "ok"`：计划写了"必须确认落地"这类判据，
                # 收敛路径却能绕过它——"动作都执行过"不等于"目标达成"。
                convergence_verification = self._verify_completion(self._run_goal(initial_plan), state, observation)
                unevaluated = list(convergence_verification.get("unevaluated") or [])
                if not convergence_verification["satisfied"]:
                    state.status = "blocked"
                    state.failure_reason = (
                        f"计划步骤已执行完，但完成判据未满足：{convergence_verification.get('reason') or '未达成'}"
                    )
                    self._event(
                        "warning",
                        "agent_loop",
                        "计划步骤已全部执行，但完成判据未满足，交回上层处理",
                        convergence_verification,
                        kind="verification",
                    )
                else:
                    state.status = "completed"
                    state.failure_reason = ""
                    if unevaluated:
                        state.verification_status = "unverified"
                        state.verification_unevaluated = unevaluated
                    else:
                        state.verification_status = state.verification_status or "ok"
                    self._event(
                        "info",
                        "agent_loop",
                        "计划内步骤已全部成功执行，任务收敛",
                        {"step": step_index, "tools": [r.tool for r in state.results if r.ok]},
                )
                break

            if decision.parallel_actions and state.status != "failed":
                batch_state = self._execute_batch_actions(
                    decision.parallel_actions,
                    command,
                    state,
                    observation,
                    allowed_tools,
                    capabilities,
                    step_index,
                    execute,
                    failure_count,
                    unresolved_failure,
                )
                failure_count = batch_state["failure_count"]
                unresolved_failure = batch_state["unresolved_failure"]
                if state.status == "failed":
                    break
        else:
            state.status = "blocked"
            state.failure_reason = f"agent loop reached max_steps={max_steps}"
            if state.results:
                # 终态先行：不管后面接模板还是模型正文，第一句都是机器结论。
                state.summary = f"{self._outcome_headline(state)}{self._step_limit_summary(state, max_steps)}"
                # smolagents provide_final_answer pattern: let the model review
                # the attempt and produce an operator-facing report instead of
                # a template line. Only when the LLM is already in use.
                if require_llm:
                    model_summary = self._llm_attempt_summary(state, model_id)
                    if model_summary:
                        # 模型写的只是正文：机器结论必须写在前面。否则"我尝试了
                        # 什么"的口吻会被读成"任务已完成"——终态由运行时判定，
                        # 不由报告的语气决定。
                        state.summary = (
                            f"{self._outcome_headline(state)}{model_summary}{self._caveat_note(state)}"
                        )

        if state.status == "created" or state.status == "running":
            state.status = "completed"
        state.finished_at = time.time()
        if not state.summary:
            state.summary = self._summary(state)
        self._notify_state(state)
        return state

    def _llm_attempt_summary(self, state: LoopState, model_id: str | None) -> str:
        """Model-generated final report for a step-budget-exhausted loop.

        Returns "" on any failure so the caller keeps the local template
        summary — the summary is an enhancement, never a failure path.
        """
        summarize = getattr(self.planner, "summarize_attempt", None)
        if not callable(summarize):
            return ""
        try:
            return summarize(state.command, state.to_dict(), model_id=model_id or None)
        except Exception:
            return ""

    def _sanitize_decision(self, decision: LoopDecision, allowed_tools: set[str]) -> LoopDecision:
        if decision.is_complete:
            return decision
        if not decision.action:
            return decision
        if decision.action in allowed_tools:
            decision.params = dict(decision.params or {})
            return decision
        return LoopDecision(
            action="",
            reason=(
                f"Tool '{decision.action}' is not available for the current backend. "
                "Pick one of the tools in available_tool_cards instead."
            ),
            is_complete=False,
            reflection="Unavailable tools are treated as execution failures, not successful completion.",
            # 不可用工具是"本轮动作无效"，不是任务终态：交回模型换一个工具，
            # 而不是把整次任务判失败（旧记忆里可能还推荐着已删除的工具）。
            needs_replan=True,
            source="guard",
        )

    def _guard_decision(
        self,
        command: str,
        state: LoopState,
        observation: LoopObservation,
        decision: LoopDecision,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision:
        """守卫只在模型**没有**给出可执行动作时兜底，绝不替模型挑工具。

        它注入的决定一律标 ``source="guard"``：前端据此显示为"运行时守卫"，而
        不是"模型思考"。需要模型自己重新决策时用 ``needs_replan=True``（空动作
        + 交回模型），而不是把整次任务判失败——"检测没开/工具不可用"从来不是
        任务的终态。
        """
        if not self._is_visual_request(command):
            return decision

        has_capture_tool = bool(capabilities.get("image_capture")) or "inspect_current_frame" in allowed_tools
        if not has_capture_tool:
            if not self._wants_open_image_analysis(command):
                return decision
            return LoopDecision(
                action="",
                reason="The active backend has no image capture tool for this visual request.",
                is_complete=False,
                reflection="Cannot answer or act on camera imagery without an image source.",
                needs_replan=True,
                source="guard",
            )

        has_image = self._has_recent_image(state)
        needs_guard_action = not decision.action or decision.is_complete or decision.action not in allowed_tools
        if (
            not self._has_successful_tool(state, "inspect_current_frame")
            and "inspect_current_frame" in allowed_tools
            and needs_guard_action
        ):
            # 视觉请求先看一眼：看图工具自己会取帧（感知轴尚未出帧时有直连兜底）。
            # 已经看过就不再重复注入——拍照已并入本工具，"有没有图"不再由单独
            # 的取帧工具决定，重复注入会让循环空转。
            return LoopDecision(
                "inspect_current_frame",
                {"question": command},
                "让视觉模型取当前帧并回答这次视觉请求。",
                source="guard",
            )

        wants_open_analysis = self._wants_open_image_analysis(command)
        wants_target = self._wants_target_confirmation(command)
        # inspect_current_frame 是视觉分析的唯一入口：开放问题和目标确认都走它。
        has_analysis = self._has_successful_tool(state, "inspect_current_frame")

        if has_image and wants_open_analysis and not has_analysis and "inspect_current_frame" in allowed_tools and needs_guard_action:
            return LoopDecision(
                "inspect_current_frame",
                {"question": command},
                "Analyze the current camera frame with the selected multimodal model.",
                source="guard",
            )

        if has_image and wants_target and not has_analysis and "inspect_current_frame" in allowed_tools and needs_guard_action:
            return LoopDecision(
                "inspect_current_frame",
                {"question": self._confirmation_question(self._target_description(command))},
                "Confirm the requested target in the captured frame before any movement.",
                source="guard",
            )

        # 守卫**不再**注入"靠近"动作。以前它无条件把模型的选择改写成
        # drone_approach_target（连撞三次后模型放弃并降落，实测 2026-10-12），
        # 后来改成受 needs_guard_action 约束——现在这个工具本身已经删掉：
        # 靠近由模型按技能用"VLM 取点 + 深度 + 有界 drone_move_relative"完成，
        # 运行时不再持有任何"抵近"策略。

        if (wants_open_analysis and has_analysis) or (wants_target and has_analysis and not self._wants_visual_approach(command)):
            if not decision.action or decision.is_complete:
                return LoopDecision(
                    action="",
                    reason="Visual analysis/confirmation has completed.",
                    is_complete=True,
                    reflection="The final answer can use the latest VLM result.",
                    source="guard",
                )
        return decision

    def _preemptive_guard_decision(
        self,
        command: str,
        state: LoopState,
        observation: LoopObservation,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision | None:
        if not self._is_visual_request(command):
            return None
        lower = command.lower()
        has_motion_goal = any(term in lower for term in MOTION_TERMS)
        visual_target_approach = self._wants_visual_approach(command) and self._wants_target_confirmation(command)
        flight_progress = any(
            self._has_successful_tool(state, tool)
            for tool in ("drone_takeoff", "drone_move_relative", "drone_fly_to", "drone_rotate_to")
        )
        if has_motion_goal and not visual_target_approach and not flight_progress:
            return None
        neutral = LoopDecision(action="", reason="", is_complete=False)
        guarded = self._guard_decision(command, state, observation, neutral, allowed_tools, capabilities)
        if guarded.action or guarded.is_complete or guarded.reflection:
            return guarded
        return None

    def _skill_decision_cards(self, capabilities: dict[str, Any]) -> list[dict[str, Any]]:
        """可激活的技能，作为决策卡片暴露给模型（名称 + 一句话用途）。

        只列当前后端能力满足、且未被禁用的技能；模型激活某个技能时会拿到它完整
        的 SKILL.md（渐进披露第二层），然后按里面的步骤调用底层工具。
        """
        loader = getattr(self.skills, "usable_doc_cards", None)
        if not callable(loader):
            return []
        try:
            cards = loader(capabilities) or []
        except Exception:
            return []
        result: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "").strip()
            if not name:
                continue
            result.append(
                {
                    "name": name,
                    "purpose": str(card.get("description") or card.get("when_to_use") or "").strip()
                    or "技能操作指导（激活后给出这类任务的完整步骤）",
                    "kind": "skill",
                }
            )
        return result

    def _decision_cards(
        self,
        command: str,
        skill_cards: list[dict[str, Any]],
        atomic_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cards = list(skill_cards)
        for card in atomic_cards:
            if isinstance(card, dict):
                cards.append(card)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            name = str(card.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(card)
        # 提示卡片预算：控制 prompt 体积；白名单不依赖它（见 run() 的注释）。
        return deduped[:44]

    def _allowed_tools(self, tool_cards: list[dict[str, Any]]) -> set[str]:
        card_names = {card.get("name") for card in tool_cards if isinstance(card, dict)}
        return {str(name) for name in card_names if name}

    def _is_visual_request(self, command: str) -> bool:
        return extract_intents(command)["visual"]

    def _wants_open_image_analysis(self, command: str) -> bool:
        return extract_intents(command)["open_image_analysis"]

    def _wants_target_confirmation(self, command: str) -> bool:
        return extract_intents(command)["target_confirmation"]

    def _wants_visual_approach(self, command: str) -> bool:
        return extract_intents(command)["visual_approach"]

    def _target_description(self, command: str) -> str:
        text = command.strip()
        if text:
            return text[:240]
        return "target"

    @staticmethod
    def _confirmation_question(target: str) -> str:
        """把目标确认转成 inspect_current_frame 的问题。"""
        target = target.strip() or "目标"
        return f"画面中是否有{target}？请确认目标是否存在，并简述其位置和外观。"

    @staticmethod
    def _visual_target_found(data: dict[str, Any]) -> bool:
        """从 inspect_current_frame 的结果里判断目标是否被视觉确认。

        视觉模型返回 target_found 时以它为准；否则以确认问题下的
        target_candidates 是否为空作为依据（开放问题同样可能给出候选，
        这里保持宽松——它只用于阻断/放行"视觉确认后才抵近"的决策）。
        """
        if not isinstance(data, dict):
            return False
        answer = data.get("answer") if isinstance(data.get("answer"), dict) else data
        if answer.get("target_found") is not None:
            return answer.get("target_found") is True
        candidates = answer.get("target_candidates")
        if isinstance(candidates, list):
            return bool(candidates)
        status = str(answer.get("status") or "").lower()
        return status in {"target_confirmed", "confirmed", "found", "locked"}

    def _has_successful_tool(self, state: LoopState, tool: str) -> bool:
        return any(item.get("tool") == tool and bool(item.get("ok")) for item in self._iter_tool_results(state))

    def _has_recent_image(self, state: LoopState) -> bool:
        now = time.time()
        for result in reversed(state.results):
            if (
                result.ok
                and (now - result.timestamp) <= self._FRAME_MAX_AGE_S
                and self._result_contains_image(result.data)
            ):
                return True
        return False

    def _perception_stall(self, observation: LoopObservation, state: LoopState) -> str:
        """画面长时间不更新时返回停止原因（"" 表示正常）。

        判据用感知轴自己上报的帧龄，而不是"最后一次图像结果的时间戳"：工具调用
        可以成功返回，但里面的画面可能早就不动了（AirSim 卡住时就是这样）。
        """
        perception = (observation.world_state or {}).get("perception") or {}
        if not perception.get("enabled"):
            return ""
        try:
            age = float(perception.get("capture_age_s"))
        except (TypeError, ValueError):
            return ""
        if age < _PERCEPTION_STALL_S:
            return ""
        used_perception = any(str(result.tool) in _PERCEPTION_TOOLS for result in state.results)
        if not used_perception:
            return ""
        return (
            f"感知画面已 {age:.0f} 秒没有更新（相机链路可能卡住，飞控心跳仍正常），"
            "任务已停止以免基于陈旧画面继续动作。"
        )

    def _latest_image_age(self, state: LoopState) -> float | None:
        """Seconds since the newest image-bearing result (None if no image)."""
        now = time.time()
        ages = [
            now - result.timestamp
            for result in reversed(state.results)
            if result.ok and self._result_contains_image(result.data)
        ]
        return min(ages) if ages else None

    def _result_contains_image(self, value: Any, _depth: int = 0) -> bool:
        if _depth > 24:
            return False
        if isinstance(value, dict):
            if any(value.get(key) for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._result_contains_image(item, _depth + 1) for item in value.values())
        if isinstance(value, list):
            return any(self._result_contains_image(item, _depth + 1) for item in value)
        return False

    def _latest_result_data(self, state: LoopState, tool: str) -> dict[str, Any]:
        for item in self._iter_tool_results(state):
            if item.get("tool") != tool:
                continue
            data = item.get("data")
            if isinstance(data, dict):
                return data
        return {}

    def _iter_tool_results(self, state: LoopState) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for result in reversed(state.results):
            items.append({"tool": result.tool, "ok": result.ok, "data": result.data})
            items.extend(self._iter_nested_tool_results(result.data))
        return items

    def _iter_nested_tool_results(self, value: Any, _depth: int = 0) -> list[dict[str, Any]]:
        if _depth > 24:
            return []
        items: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("tool"):
                items.append(value)
            for key in ("tool_results", "results"):
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in reversed(nested):
                        items.extend(self._iter_nested_tool_results(item, _depth + 1))
            data = value.get("data")
            if isinstance(data, (dict, list)):
                items.extend(self._iter_nested_tool_results(data, _depth + 1))
        elif isinstance(value, list):
            for item in reversed(value):
                items.extend(self._iter_nested_tool_results(item, _depth + 1))
        return items

    # 这里原来还有两个"守卫替模型挑工具"的决策函数：_approach_decision_from_confirmation
    # （注入 drone_approach_target 或读深度）和 _search_decision_after_confirmation
    # （注入 skill:search）。随检测工具一起删除：靠近不再有运行时策略，由模型按技能
    # 用"VLM 取点 + 深度 + 有界 drone_move_relative"完成；搜索同理。

    def _tool_specs(self) -> list[dict[str, Any]] | None:
        """Runtime tool specs (annotations/defaults) for schema synthesis."""
        try:
            return self.tools.list_tools() if hasattr(self.tools, "list_tools") else None
        except Exception:
            return None

    # 一次决策里最多串联多少个后续动作（防畸形响应；正常用法 2~4 个）。
    _BATCH_CHAIN_LIMIT = 8

    # Task-contract verification: machine-checkable completion criteria that
    # gate model-declared completion ("LLM 提议 + 确定性验证").
    _CORRECTIVE_TOOLS = {
        "status_ok": "drone_get_status",
        "landed": "drone_get_status",
        "flying_at": "drone_get_status",
        "position_reached": "drone_get_status",
        "photo_taken": "inspect_current_frame",
        "mission_progress_complete": "drone_get_mission_progress",
        "target_confirmed": "inspect_current_frame",
        "formation_stable": "formation_command",
    }
    # Frames older than this are treated as "no recent image" by the guards.
    _FRAME_MAX_AGE_S = 60.0

    @staticmethod
    def _run_goal(initial_plan: MissionPlan | None) -> dict[str, Any]:
        if initial_plan is None:
            return {}
        plan_dict = initial_plan.to_dict() if hasattr(initial_plan, "to_dict") else {}
        goal = dict(plan_dict.get("goal") or {})
        criteria = list(goal.get("success_criteria") or [])
        # 自动补完成判据：LLM 经常不提供 success_criteria，导致"声明完成即
        # 完成"畅行无阻（例如向前移动被安全层拦截后仍被标记 completed）。
        # 计划中含有运动步骤时，注入"这些动作必须成功执行过"的机器校验判据。
        if not any(c.get("metric") == "planned_motion_steps_ok" for c in criteria):
            planned_motion = [
                s.tool for s in (initial_plan.steps or [])
                if getattr(s, "tool", "") in _PLANNED_MOTION_TOOLS
            ]
            if planned_motion:
                criteria.append({
                    "metric": "planned_motion_steps_ok",
                    "tools": list(dict.fromkeys(planned_motion)),
                    "detail": "计划中的运动步骤必须成功执行：{}".format(
                        ", ".join(dict.fromkeys(planned_motion))
                    ),
                })
        if criteria:
            goal["success_criteria"] = criteria
        return goal

    # 计划收敛时忽略的"辅助/只读"工具：它们不构成任务目标，只用于准备或
    # 读数；缺少它们不算任务未完成，避免收敛判据永远不成立。
    _CONVERGENCE_IGNORED_TOOLS = {
        "drone_connect",
        "drone_get_status",
        "perception_status",
    }

    # 反应式任务里允许"照计划自动推进"的确定性准备步骤；其余动作（确认、机动、
    # 降落、汇报）一律交回 LLM 基于最新观察决策。
    _REACTIVE_AUTO_TOOLS = {
        "drone_connect",
        "drone_get_status",
        "drone_arm",
        "drone_set_mode",
        "drone_takeoff",
    }

    @classmethod
    def _plan_step_at(cls, initial_plan: Any, cursor: int) -> Any | None:
        if cursor < 0:
            return None
        steps = list(getattr(initial_plan, "steps", None) or [])
        if cursor >= len(steps):
            return None
        return steps[cursor]

    def _plan_step_deviation(self, step: Any, state: LoopState) -> str:
        """判断"当前观察是否已经偏离计划假设"，偏离才需要交回 LLM 重新决策。

        这是"按需 ReAct"的核心：计划照常快速执行，只有当世界与计划假设不一致
        （视觉确认没通过）才让模型基于最新观察重新决定，而不是每一步都调用一次
        模型。

        **运动步骤不再有前置条件**：以前这里要求 drone_approach_target /
        drone_fly_to / drone_move_relative 必须有检测锁定（否则返回"当前画面
        没有锁定目标，不能执行抵近/飞向动作"），那是"不开检测就跑不动"的最后
        一处卡点。检测随 YOLO 一起移除后，是否该往某个方向飞完全由模型基于
        技能与最新观察决定；运行时只保留飞行安全（包线/高度/围栏）这一层。
        """
        tool = str(getattr(step, "tool", "") or "")
        if tool == "inspect_current_frame":
            data = self._latest_result_data(state, tool)
            if data:
                answer = data.get("answer") if isinstance(data.get("answer"), dict) else data
                status = str(answer.get("status") or "").lower()
                question = str(data.get("question") or "")
                if answer.get("target_found") is False or status in {"target_not_confirmed", "not_found"}:
                    return "视觉确认未通过（目标未确认），应改为抵近或换角度再确认"
                # 确认型提问（而不是开放描述）没有得到候选目标，同样视为未通过。
                if ("是否有" in question or "确认" in question) and not self._visual_target_found(data):
                    return "视觉确认未通过（目标未确认），应改为抵近或换角度再确认"
        return ""

    @classmethod
    def _plan_step_decision(
        cls,
        initial_plan: Any,
        cursor: int,
        allowed_tools: set[str],
    ) -> LoopDecision | None:
        """按计划取下一步决策（不调用 LLM）。

        计划是 LLM 已经给出的，按序执行不需要每步再思考一次；只有步骤失败、
        计划走完、或计划里出现了当前不可用的工具时，才把控制权交回 LLM。

        "何时需要 LLM 重新决策"由 run() 里的偏离检测（_plan_step_deviation）
        负责：只有观察与计划假设不符时才转 ReAct，否则一路按计划推进。
        """
        if cursor < 0:
            return None
        steps = list(getattr(initial_plan, "steps", None) or [])
        if cursor >= len(steps):
            return None
        step = steps[cursor]
        tool = str(getattr(step, "tool", "") or "")
        if not tool:
            return None
        if tool not in allowed_tools and tool not in cls._CONVERGENCE_IGNORED_TOOLS:
            return None
        params = dict(getattr(step, "params", {}) or {})
        label = str(getattr(step, "label", "") or tool)
        return LoopDecision(
            action=tool,
            params=params,
            reason=label,
            is_complete=False,
            source="plan",
        )

    @classmethod
    def _plan_steps_satisfied(cls, state: LoopState, initial_plan: Any) -> bool:
        """计划中的实质步骤是否都已成功执行过一次。

        用于确定性收敛：LLM 已经规划并驱动了每一步，runtime 只需在目标动作
        全部落地后结束任务，避免收尾阶段反复空转。
        """
        steps = list(getattr(initial_plan, "steps", None) or [])
        if not steps:
            return False
        required = [
            str(getattr(step, "tool", "") or "")
            for step in steps
            if getattr(step, "tool", "") and step.tool not in cls._CONVERGENCE_IGNORED_TOOLS
        ]
        if not required:
            return False
        ok_tools = {str(result.tool) for result in state.results if result.ok}
        return all(tool in ok_tools for tool in dict.fromkeys(required))

    def _latest_located_target(self, state: LoopState) -> tuple[float, float] | None:
        """最近一次成功定位的目标点（NED 水平坐标）；没有返回 None。"""
        for item in self._iter_tool_results(state):
            if item.get("tool") != "locate_image_point" or not item.get("ok"):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if data.get("valid") is False:
                continue
            # "框内外深度一致" = 那个坐标是地面/墙面这类连续表面，不是目标 → 不能
            # 拿它当"目标位置"来否决别的动作（否则会把模型正确的移动也拦下来）。
            if str(data.get("surface_check") or "") == "same_surface":
                continue
            world = data.get("world_pos")
            if not isinstance(world, dict):
                continue
            try:
                return (float(world.get("x")), float(world.get("y")))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _observed_position(observation: LoopObservation) -> tuple[float, float] | None:
        """当前水平位置（NED x/y）；拿不到返回 None。"""
        drone = (observation.world_state or {}).get("drone")
        if isinstance(drone, dict):
            position = drone.get("position_ned")
            if isinstance(position, dict):
                try:
                    return (float(position.get("x")), float(position.get("y")))
                except (TypeError, ValueError):
                    pass
        return None

    def _approach_overshoot_reason(
        self,
        decision: LoopDecision,
        state: LoopState,
        observation: LoopObservation,
        command: str,
    ) -> str:
        """'这一飞会掠过目标'的否决理由；不适用时返回 ""。

        实测事故（2026-10-13）：模型用 `locate_image_point` 拿到一个"目标"坐标
        —— 距离本身是对的（30 m），但那个点是**路面**：视线朝下 9°，深度射线打在
        目标前面的地上。模型据此 `drone_fly_to` 直飞过去，掠过汽车、目标出画，
        之后一路空转找不到它。

        规则：近距视觉任务里，如果 `drone_fly_to` 的落点在"已定位目标点"的站离圈
        以内、或落在它后面（即飞进/飞过目标），否决这一次动作并**交回模型**——
        不偷偷改它的参数，而是告诉它该怎么走。侧向绕行不算飞过。
        """
        if str(getattr(decision, "action", "") or "") != "drone_fly_to":
            return ""
        try:
            intents = extract_intents(command)
        except Exception:
            return ""
        if not (intents.get("visual") and intents.get("visual_approach")):
            return ""
        located = self._latest_located_target(state)
        drone = self._observed_position(observation)
        params = decision.params if isinstance(decision.params, dict) else {}
        if located is None or drone is None:
            return ""
        try:
            dest = (float(params.get("x")), float(params.get("y")))
        except (TypeError, ValueError):
            return ""
        import math

        dx, dy = located[0] - drone[0], located[1] - drone[1]
        span = math.hypot(dx, dy)
        if span < 1.0:
            return ""  # 已经在目标附近，"飞到目标"不再有掠过的意义
        ux, uy = dx / span, dy / span
        proj = (dest[0] - drone[0]) * ux + (dest[1] - drone[1]) * uy
        lateral = abs((dest[0] - drone[0]) * (-uy) + (dest[1] - drone[1]) * ux)
        if lateral > max(3.0, 0.25 * span):
            return ""  # 明显从旁边绕过去，不算飞过
        if proj < span - _APPROACH_STANDOFF_M:
            return ""  # 停在目标前方，安全
        return (
            f"这一飞会在距目标 {max(0.0, span - proj):.1f} m 处到达（站离要求 "
            f"{_APPROACH_STANDOFF_M:.0f} m），会飞进/飞过目标——而那个目标坐标本来就是"
            "单点深度推出来的，可能落在目标前面的地面或背景上（实测过：视线下倾 9°，"
            "深度打中的是 30 m 外的路面）。改用 drone_move_relative 做有界相对步进，"
            "每步复核目标仍在画面里；一定要飞过去也要停在目标前方。"
        )

    def _is_close_range_visual(self, command: str) -> bool:
        try:
            intents = extract_intents(command)
        except Exception:
            return False
        return bool(intents.get("visual") and intents.get("visual_approach"))

    def _approach_measure(self, state: LoopState) -> tuple[float | None, float | None, bool]:
        """最近一次视觉确认的（目标占画面高 h, 距离 m, 是否确认到目标）。

        两者都从工具结果里取：h 来自 `inspect_current_frame` 的 bbox_hint（视觉模型的
        粗值），距离来自 `locate_image_point`（深度几何）。
        "到位与否"的判定必须建立在这两个数上——否则判据只能由模型自己说。
        """
        height: float | None = None
        confirmed = False
        for item in self._iter_tool_results(state):
            if item.get("tool") != "inspect_current_frame" or not item.get("ok"):
                continue
            data = item.get("data") or {}
            answer = data.get("answer") if isinstance(data.get("answer"), dict) else data
            candidates = answer.get("target_candidates")
            if not isinstance(candidates, list) or not candidates:
                continue
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            hint = first.get("bbox_hint") if isinstance(first.get("bbox_hint"), dict) else {}
            try:
                height = float(hint.get("h"))
            except (TypeError, ValueError):
                height = None
            confirmed = True
            break
        distance: float | None = None
        for item in self._iter_tool_results(state):
            if item.get("tool") != "locate_image_point" or not item.get("ok"):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            for key in ("distance_m", "distance_median_m", "depth_m"):
                try:
                    distance = float(data.get(key))
                    break
                except (TypeError, ValueError):
                    continue
            if distance is not None:
                break
        return height, distance, confirmed

    def _criterion_holds(self, criterion: dict[str, Any], satisfied: bool, now: float) -> bool:
        """判据是否"持续成立"到可以作数（文献里的 hold_for / 滞后确认）。

        `hold_s` 由任务声明（默认 0 = 单次成立即可）。为什么要它：单次读数可能是噪声
        （视觉尺寸是粗值、深度偶发跳点），一次"满足"就收工会早停；反过来"满足→下次不
        满足"来回跳会让收尾与播报反复。只在持续成立够久时才认。

        状态挂在实例上、每次 run 清空（`_criteria_hold`）。
        """
        try:
            hold_s = float(criterion.get("hold_s") or 0.0)
        except (TypeError, ValueError):
            hold_s = 0.0
        key = json.dumps(
            {k: v for k, v in criterion.items() if k != "hold_s"}, sort_keys=True, ensure_ascii=False, default=str
        )
        hold_map = getattr(self, "_criteria_hold", None)
        if not isinstance(hold_map, dict):
            hold_map = {}
            self._criteria_hold = hold_map
        if not satisfied:
            hold_map.pop(key, None)
            return False
        if hold_s <= 0.0:
            return True
        first = hold_map.setdefault(key, now)
        return (now - first) >= hold_s

    def _stall_reason(
        self,
        state: LoopState,
        plan: Any,
        observation: LoopObservation,
    ) -> str:
        """卡住的理由；没有声明的可测量必需判据、或还在改善 → ""。

        **只对任务声明的判据生效**（没有声明就不管——运行时不发明标准）。
        判据"还没满足"且连续 `_STALL_WINDOW` 轮没有改善（超过最小改善量）时算停滞。
        改善方向按判据本身定：`target_fill` 越大越好，`target_distance` 越小越好。
        """
        goal = getattr(plan, "goal", None)
        if not isinstance(goal, dict):
            return ""
        series = getattr(self, "_stall_series", None)
        if not isinstance(series, dict):
            series = {}
            self._stall_series = series
        for criterion in goal.get("success_criteria") or []:
            if not isinstance(criterion, dict) or not bool(criterion.get("required", True)):
                continue
            metric = str(criterion.get("metric") or "")
            if metric not in {"target_fill", "target_distance"}:
                continue
            result = self._verify_criterion(criterion, state, observation)
            if not result.get("evaluated", True):
                continue
            key = json.dumps(
                {k: v for k, v in criterion.items() if k not in {"hold_s", "required"}},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
            value = result.get("value")
            if result.get("satisfied") or value is None:
                series.pop(key, None)  # 满足/没数据：清零重新计
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            better_is_higher = metric == "target_fill"
            margin = _STALL_FILL_MARGIN if better_is_higher else _STALL_DISTANCE_MARGIN_M
            best = float(series[key]) if key in series else None
            if best is None or (number > best + margin if better_is_higher else number < best - margin):
                series[key] = number  # 有改善（或首次）：重置计数
                series[f"{key}:flat"] = 0
                continue
            flat = int(series.get(f"{key}:flat") or 0) + 1
            series[f"{key}:flat"] = flat
            if flat >= _STALL_WINDOW:
                return f"{metric} 连续 {flat} 轮没有改善（当前 {number}，最好 {best}）"
        return ""

    @staticmethod
    def _last_action_signature(decision: LoopDecision) -> str:
        params = decision.params if isinstance(decision.params, dict) else {}
        return f"{decision.action}|{json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)}"

    def _is_repeat_action(self, decision: LoopDecision, state: LoopState) -> bool:
        """这一步是不是最近刚做过的同一动作（同工具，且参数是同一组值）。

        比较用"决策参数的子集匹配"而不是整串相等：执行层会给工具补默认参数
        （velocity/right_m…），逐字比较会因为补出来的字段而永远判不出重复。
        """
        action = str(decision.action or "").strip()
        if not action:
            return False
        wanted = decision.params if isinstance(decision.params, dict) else {}
        for result in state.results[-_STALL_REPEAT_LOOKBACK:]:
            if str(result.tool) != action:
                continue
            recorded = dict(result.params or {})
            if all(recorded.get(key) == value for key, value in wanted.items()):
                return True
        return False

    def _declared_criteria_status(
        self,
        state: LoopState,
        plan: Any,
        observation: LoopObservation,
    ) -> list[dict[str, Any]]:
        """任务声明的判据 + 当前状态（给操作员看"怎么算做到、现在到哪了"）。

        只评估能在运行中机械算出来的那几条（画面比例/距离）；位置类与语义类由收尾时的
        `_verify_completion` 评估——运行中不猜。
        """
        goal = getattr(plan, "goal", None)
        if not isinstance(goal, dict):
            return []
        rows: list[dict[str, Any]] = []
        for criterion in goal.get("success_criteria") or []:
            if not isinstance(criterion, dict):
                continue
            metric = str(criterion.get("metric") or "")
            if metric not in {"target_fill", "target_distance"}:
                continue
            result = self._verify_criterion(criterion, state, observation)
            if not result.get("evaluated", True):
                continue  # 还没有数据：不播报"未满足"，免得把"没看到"说成"没达到"
            raw_satisfied = bool(result.get("satisfied"))
            held = self._criterion_holds(criterion, raw_satisfied, time.time())
            detail = str(result.get("detail") or "")
            if raw_satisfied and not held:
                hold_s = float(criterion.get("hold_s") or 0.0)
                detail = f"{detail}（需持续 {hold_s:.1f}s 才作数）"
            rows.append({"metric": metric, "satisfied": held, "detail": detail})
        return rows

    def _stopping_criterion_status(
        self,
        state: LoopState,
        plan: Any,
        observation: LoopObservation,
    ) -> tuple[str, str]:
        """（到位理由, 来源）；来源是 "declared"（计划声明的判据）或 "default"。

        **判定标准属于任务，不属于运行时**：计划可以在 `goal.success_criteria` 里声明
        这个任务什么时候算做完（`target_fill` / `target_distance` / `position_reached` /
        `flying_at` / `landed`），运行时只负责机械评估、播报、据此停止或放行。只有**没有
        声明**时才退回任务类型的默认（靠近类任务：目标占画面 ≥25%）。

        位置类判据（`position_reached`/`landed`/`flying_at`）出现时，**不做**"占画面比例"
        的到位判定——"落在 H 点上"要的是位置精度，不是靠得够近；那些判据由
        `_verify_completion` 负责，运行时不在这里替它下结论。
        """
        criteria: list[dict[str, Any]] = []
        goal = getattr(plan, "goal", None)
        if isinstance(goal, dict):
            criteria = [item for item in (goal.get("success_criteria") or []) if isinstance(item, dict)]
        metrics = {str(item.get("metric") or "") for item in criteria}
        if metrics & {"position_reached", "landed", "flying_at"}:
            return "", "declared"
        for criterion in criteria:
            metric = str(criterion.get("metric") or "")
            if metric not in {"target_fill", "target_distance"}:
                continue
            result = self._verify_criterion(criterion, state, observation)
            if result.get("evaluated", True) and self._criterion_holds(
                criterion, bool(result.get("satisfied")), time.time()
            ):
                return f"{result.get('detail') or metric}（计划声明的判据）", "declared"
            return "", "declared"
        # 什么都没声明：运行时不替任务发明标准（既不算到位也不阻止模型的判断）。
        # "判定标准属于任务"这条要彻底——上一版在这里退回了一个"靠近类默认"，等于把
        # 一种任务的标准强加给所有任务；操作员明确否掉了这种做法。
        return "", "none"

    def _is_further_approach(
        self,
        decision: LoopDecision,
        state: LoopState,
        observation: LoopObservation,
    ) -> bool:
        """这一步是不是"继续大幅逼近"（到位后要拒绝的那种）。"""
        import math

        action = str(getattr(decision, "action", "") or "")
        params = decision.params if isinstance(decision.params, dict) else {}
        if action == "drone_move_relative":
            try:
                return float(params.get("forward_m") or 0.0) > _REACHED_MICRO_STEP_M
            except (TypeError, ValueError):
                return False
        if action == "drone_fly_to":
            located = self._latest_located_target(state)
            drone = self._observed_position(observation)
            if located is None or drone is None:
                return False
            try:
                dest = (float(params.get("x")), float(params.get("y")))
            except (TypeError, ValueError):
                return False
            before = math.hypot(located[0] - drone[0], located[1] - drone[1])
            after = math.hypot(located[0] - dest[0], located[1] - dest[1])
            return after < before - 1.0
        return False

    def _verify_completion(self, goal: dict[str, Any], state: LoopState, observation: LoopObservation) -> dict[str, Any]:
        criteria = (goal or {}).get("success_criteria") or []
        if not isinstance(criteria, list) or not criteria:
            # 一个判据都没有时的兜底：至少要**成功执行过一个动作**才算完成。
            # 否则"模型声明完成 + 什么都没做"会直接通过——实测空循环声明完成就是
            # completed。status_ok 这个判据本来就是为这个场景写的，只是以前只在
            # 计划自带判据时才被评估。
            criteria = [{"metric": "any_action_succeeded", "detail": "至少成功执行一个动作"}]
        results = [self._verify_criterion(criterion, state, observation) for criterion in criteria]
        # Unevaluated criteria (missing telemetry) do not fail the run; they are
        # recorded as warnings per the design ("无法评估 → 接受完成 + warning").
        failed = [item for item in results if not item["satisfied"] and item.get("evaluated", True)]
        unevaluated = [item for item in results if not item.get("evaluated", True)]
        satisfied = not failed
        reason = "; ".join(f"{item['metric']}: {item['detail']}" for item in failed)
        if unevaluated:
            note = "; ".join(f"{item['metric']}: {item['detail']}" for item in unevaluated)
            reason = f"{reason}; unevaluated: {note}".strip("; ")
        return {"satisfied": satisfied, "criteria": criteria, "results": results, "reason": reason, "unevaluated": unevaluated}

    def _verify_criterion(self, criterion: dict[str, Any], state: LoopState, observation: LoopObservation) -> dict[str, Any]:
        metric = str(criterion.get("metric") or "")
        base: dict[str, Any] = {"metric": metric, "satisfied": False, "detail": "", "evaluated": True}
        if metric == "any_action_succeeded":
            # 兜底判据：只为拦住"一个动作都没做就宣称完成"。它刻意比 status_ok
            # 宽松——运行末尾带着一次失败动作不该由这条判据来处置（那有 3 次
            # 失败窗口、纠正路径和上层 gate 管），这里只防"空声明"。
            any_ok = any(result.ok for result in state.results)
            base.update(satisfied=any_ok, detail=f"successful_actions={sum(1 for r in state.results if r.ok)}")
        elif metric == "status_ok":
            failed = [result for result in state.results if not result.ok]
            # "无失败工具" plus at least one executed action: an empty loop that
            # declares completion without doing anything must not pass the gate.
            base["satisfied"] = bool(state.results) and not failed
            base["detail"] = f"results={len(state.results)} failed={len(failed)}"
        elif metric == "planned_motion_steps_ok":
            # 计划中的每个运动步骤必须至少留下一次成功执行记录；被安全层
            # 拦截/失败的动作用 fresh 状态回读证明目标已达成也无法通过——
            # 必须真正补做成功（LLM 提示词里已声明此纪律）。
            tools = list(criterion.get("tools") or [])
            ok_tools = {str(result.tool) for result in state.results if result.ok}
            missing = [t for t in tools if t not in ok_tools]
            base.update(
                satisfied=not missing,
                detail=f"missing={','.join(missing)}" if missing else "all planned motion steps succeeded",
            )
        elif metric == "photo_taken":
            ok = any(
                result.ok
                and (
                    self._result_contains_image(result.data)
                    or str((result.data or {}).get("saved_path") or "").strip()
                )
                for result in state.results
            )
            base.update(satisfied=ok, detail="photo captured" if ok else "no successful photo in results")
        elif metric == "target_fill":
            # 目标占画面高度（视觉模型的粗值）达到阈值 → 够近。这是"靠近类任务"最常用的
            # 到位判据，数值由计划声明（默认 0.25 与技能一致）。
            height, _distance, confirmed = self._approach_measure(state)
            try:
                min_fill = float(criterion.get("min_fill") or 0.25)
            except (TypeError, ValueError):
                min_fill = 0.25
            if not confirmed or height is None:
                base.update(satisfied=False, evaluated=False, detail="no visual size yet")
            else:
                base.update(
                    satisfied=height >= min_fill,
                    detail=f"fill={height:.2f} min_fill={min_fill:.2f}",
                    value=round(float(height), 3),
                )
        elif metric == "target_distance":
            # 目标距离（深度）小于等于阈值 → 够近。
            _height, distance, confirmed = self._approach_measure(state)
            try:
                max_m = abs(float(criterion.get("max_m") or 3.0))
            except (TypeError, ValueError):
                max_m = 3.0
            if not confirmed or distance is None:
                base.update(satisfied=False, evaluated=False, detail="no distance yet")
            else:
                base.update(
                    satisfied=distance <= max_m,
                    detail=f"distance={distance:.1f} max_m={max_m:.1f}",
                    value=round(float(distance), 3),
                )
        elif metric == "target_confirmed":
            found = self._target_found_any(state)
            explicitly_not_found = self._target_not_found_explicit(state)
            looked = self._has_successful_tool(state, "inspect_current_frame")
            satisfied = bool(found) or (explicitly_not_found and looked)
            base.update(satisfied=satisfied, detail=f"found={found} not_found={explicitly_not_found}")
        elif metric == "landed":
            flying = self._observation_flying(observation)
            if flying is None:
                # no telemetry: criterion cannot be evaluated, do not fail the run
                base.update(satisfied=True, evaluated=False, detail="no flight-state telemetry")
            else:
                base.update(satisfied=flying is False, detail=f"flying={flying}")
        elif metric == "flying_at":
            current = self._observation_altitude(observation)
            altitude = criterion.get("altitude")
            tolerance = float(criterion.get("tolerance") or 1.0)
            if current is None:
                base.update(satisfied=True, evaluated=False, detail="no altitude telemetry")
            else:
                satisfied = altitude is not None and abs(current - float(altitude)) <= tolerance
                base.update(satisfied=satisfied, detail=f"current={current} target={altitude}±{tolerance}")
        elif metric == "position_reached":
            pos = self._observation_position(observation)
            tolerance = float(criterion.get("tolerance") or 1.5)
            if pos is None:
                base.update(satisfied=True, evaluated=False, detail="no position telemetry")
            else:
                dx = abs(pos[0] - float(criterion.get("x") or 0.0))
                dy = abs(pos[1] - float(criterion.get("y") or 0.0))
                dz = abs(pos[2] - float(criterion.get("z") or 0.0))
                satisfied = dx <= tolerance and dy <= tolerance and dz <= tolerance
                base.update(satisfied=satisfied, detail=f"current={pos} target=({criterion.get('x')},{criterion.get('y')},{criterion.get('z')})±{tolerance}")
        elif metric == "mission_progress_complete":
            complete = any(self._progress_complete(result.data) for result in state.results if result.ok)
            base.update(satisfied=complete, detail="mission progress complete" if complete else "mission progress not complete")
        elif metric == "formation_stable":
            # only the LATEST formation_command result counts — a stale stable
            # flag from an earlier status must not satisfy the criterion
            last_formation = None
            for result in reversed(state.results):
                if result.tool == "formation_command":
                    last_formation = result
                    break
            stable = bool(
                last_formation
                and last_formation.ok
                and self._nested_bool(last_formation.data, "stable") is True
            )
            base.update(satisfied=stable, detail="formation stable" if stable else "formation not stable / no status result")
        return base

    def _corrective_decision(self, verification: dict[str, Any], state: LoopState, allowed_tools: set[str]) -> LoopDecision | None:
        """One corrective action for the first unsatisfied criterion that has a
        re-verification path. Returns None when nothing can be re-checked."""
        for criterion, item in zip(verification.get("criteria") or [], verification.get("results") or []):
            if item.get("satisfied"):
                continue
            tool = self._CORRECTIVE_TOOLS.get(str(item.get("metric") or ""))
            if tool is None or tool not in allowed_tools:
                continue
            if tool == "inspect_current_frame":
                # inspect 自身可取帧（含直连兜底），不需要"先拍一张"的前置步骤。
                return LoopDecision(
                    "inspect_current_frame",
                    {"question": self._confirmation_question(str(criterion.get("target") or state.command[:160]))},
                    "Re-confirm the target before accepting completion.",
                    is_complete=False,
                )
            if tool == "formation_command":
                return LoopDecision(
                    "formation_command",
                    {"action": "status"},
                    "Re-poll formation status before accepting completion.",
                    is_complete=False,
                )
            return LoopDecision(
                tool,
                {},
                f"Re-read state to verify completion criterion '{item.get('metric')}'.",
                is_complete=False,
            )
        return None

    @staticmethod
    def _is_confirmation_result(data: dict[str, Any]) -> bool:
        question = str(data.get("question") or "")
        return "是否有" in question or "确认" in question

    def _target_found_any(self, state: LoopState) -> bool:
        for item in self._iter_tool_results(state):
            if self._nested_bool(item.get("data"), "target_found") is True:
                return True
            data = item.get("data") or {}
            if (
                item.get("tool") == "inspect_current_frame"
                and self._is_confirmation_result(data)
                and self._visual_target_found(data)
            ):
                return True
        return False

    def _target_not_found_explicit(self, state: LoopState) -> bool:
        for item in self._iter_tool_results(state):
            data = item.get("data") or {}
            if self._nested_bool(data, "target_found") is False:
                return True
            if self._nested_value(data, "status") == "target_not_confirmed":
                return True
            if (
                item.get("tool") == "inspect_current_frame"
                and self._is_confirmation_result(data)
                and not self._visual_target_found(data)
            ):
                return True
        return False

    @staticmethod
    def _nested_bool(value: Any, key: str) -> bool | None:
        found = AgentLoop._nested_value(value, key)
        return bool(found) if isinstance(found, bool) else None

    @staticmethod
    def _nested_value(value: Any, key: str, _depth: int = 0) -> Any:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            if key in value:
                return value[key]
            for item in value.values():
                found = AgentLoop._nested_value(item, key, _depth + 1)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = AgentLoop._nested_value(item, key, _depth + 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _progress_complete(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        progress = data.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool) and progress >= 99.0:
            return True
        percent = data.get("percent")
        if isinstance(percent, (int, float)) and not isinstance(percent, bool) and percent >= 99.0:
            return True
        status = str(data.get("status") or "").lower()
        return any(marker in status for marker in ("complete", "finished", "done"))

    @staticmethod
    def _observation_drone(observation: LoopObservation) -> dict[str, Any]:
        world = observation.world_state if hasattr(observation, "world_state") else {}
        drone = world.get("drone") if isinstance(world, dict) else {}
        return drone if isinstance(drone, dict) else {}

    @staticmethod
    def _observation_flying(observation: LoopObservation) -> bool | None:
        flying = AgentLoop._observation_drone(observation).get("flying")
        return bool(flying) if flying is not None else None

    @staticmethod
    def _observation_altitude(observation: LoopObservation) -> float | None:
        drone = AgentLoop._observation_drone(observation)
        pos = drone.get("position_ned")
        if isinstance(pos, dict):
            try:
                z = float(pos.get("z") or 0.0)
                return abs(z)
            except (TypeError, ValueError):
                pass
        try:
            altitude = float(drone.get("altitude_m") or 0.0)
            return abs(altitude) if altitude else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _observation_position(observation: LoopObservation) -> tuple[float, float, float] | None:
        pos = AgentLoop._observation_drone(observation).get("position_ned")
        if not isinstance(pos, dict):
            return None
        try:
            return (float(pos.get("x") or 0.0), float(pos.get("y") or 0.0), float(pos.get("z") or 0.0))
        except (TypeError, ValueError):
            return None

    def _execute_batch_actions(
        self,
        raw_actions: list[dict[str, Any]],
        command: str,
        state: LoopState,
        observation: LoopObservation,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
        step_index: int,
        execute: bool,
        failure_count: int,
        unresolved_failure: bool,
    ) -> dict[str, Any]:
        """Execute the decision's follow-up actions in order (read-only or flight).

        每个动作都走与主动作相同的 sanitize + guard + 安全校验；任一动作失败就停止
        后续动作，把控制权交回模型。链内看不到中间结果，这是模型自己的选择——它
        应该在"下一步不依赖新观察"时才把多个动作串起来。上限只为防畸形响应。
        """
        chained = raw_actions[: self._BATCH_CHAIN_LIMIT]
        if len(raw_actions) > self._BATCH_CHAIN_LIMIT:
            self._event(
                "warning",
                "agent_loop",
                f"Too many chained actions ({len(raw_actions)}), only the first {self._BATCH_CHAIN_LIMIT} run",
                {"actions": [item.get("action") for item in raw_actions if isinstance(item, dict)]},
            )
        for raw_action in chained:
            if state.status == "failed":
                break
            if not isinstance(raw_action, dict):
                continue
            sub = LoopDecision(
                action=str(raw_action.get("action") or ""),
                params=dict(raw_action.get("params") or {}),
                reason=str(raw_action.get("reason") or ""),
                is_complete=bool(raw_action.get("is_complete")),
                needs_replan=bool(raw_action.get("needs_replan")),
                reflection=str(raw_action.get("reflection") or ""),
            )
            if not sub.action or sub.is_complete:
                continue
            sub = self._sanitize_decision(sub, allowed_tools)
            if not sub.action:
                self._event("warning", "agent_loop", f"Batch action skipped: {raw_action.get('action')}", sub.to_dict(), kind="loop.decision")
                continue
            guarded = self._guard_decision(command, state, observation, sub, allowed_tools, capabilities)
            if guarded is not None:
                sub = guarded
            sub = self._sanitize_decision(sub, allowed_tools)
            if sub.is_complete or not sub.action:
                continue
            batch_result = self._execute_action(sub, dry_run=not execute)
            batch_row = LoopActionResult(
                step_index=step_index,
                tool=batch_result["tool"],
                params=batch_result["params"],
                ok=bool(batch_result["ok"]),
                data=batch_result["data"],
                safety=batch_result.get("safety"),
                duration_ms=batch_result["duration_ms"],
            )
            state.results.append(batch_row)
            self._notify_state(state)
            self.memory.remember_tool_call(batch_row.tool, batch_row.ok)
            self._event(
                "info" if batch_row.ok else "warning",
                "tool",
                f"Batch action {batch_row.tool}",
                batch_result["raw"],
                kind="tool.result",
            )
            if not batch_row.ok:
                failure_count += 1
                unresolved_failure = True
                state.failure_reason = str(batch_row.data.get("message") or f"{batch_row.tool} failed")
                self._event(
                    "warning",
                    "agent_loop",
                    f"Batch action failed ({failure_count}/3)",
                    batch_row.to_dict(),
                    kind="tool.result",
                )
                if failure_count >= 3:
                    state.status = "failed"
                    break
            elif batch_row.tool not in {"drone_get_status"}:
                unresolved_failure = False
        return {"failure_count": failure_count, "unresolved_failure": unresolved_failure}

    def _execute_action(self, decision: LoopDecision, dry_run: bool) -> dict[str, Any]:
        started = time.time()
        if decision.action.startswith("skill:"):
            skill_result = self.skills.execute(
                decision.action,
                decision.params,
                self.tools,
                dry_run=dry_run,
                execute_tool=self._call_tool,
            )
            finished = time.time()
            return {
                "tool": skill_result.skill,
                "params": dict(decision.params),
                "ok": skill_result.ok,
                "data": skill_result.to_dict(),
                "safety": None,
                "duration_ms": round((finished - started) * 1000, 1),
                "raw": skill_result.to_dict(),
            }
        tool_result = self._call_tool(decision.action, decision.params, dry_run)
        # error_code 是 ToolCallResult 的独立字段，而这里以前只把 data 交出去，
        # 于是 AgentLoop 读 result_row.data.get("error_code") 永远是空串，连接
        # 熔断只能退化成按消息子串匹配。这里把它并进 data，审计轨迹与前端也能
        # 直接看到结构化错误码。
        data = dict(tool_result.data or {})
        if tool_result.error_code:
            data.setdefault("error_code", tool_result.error_code)
        return {
            "tool": tool_result.tool,
            "params": tool_result.params,
            "ok": tool_result.ok,
            "data": data,
            "safety": tool_result.safety,
            "duration_ms": round((tool_result.finished_at - tool_result.started_at) * 1000, 1),
            "raw": tool_result.to_dict(),
        }

    def _call_tool(self, name: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
        if self.execute_tool:
            return self.execute_tool(name, params, dry_run)
        return self.tools.execute(
            name,
            params,
            dry_run=dry_run,
            blocked_by_supervisor=self._should_stop(),
        )

    def _wait_if_paused(self) -> None:
        if not self.should_pause:
            return
        while self.should_pause() and not self._should_stop():
            time.sleep(0.2)

    def _paused_now(self) -> bool:
        """此刻是否处于暂停态（不含急停：急停由 _should_stop 判定）。"""
        if not self.should_pause:
            return False
        try:
            return bool(self.should_pause())
        except Exception:
            return False

    def _should_stop(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def _take_steer(self) -> list[str]:
        """取回操作员在执行中追加的补充指令（provider 负责清空）。"""
        if self.steer_provider is None:
            return []
        try:
            items = self.steer_provider() or []
        except Exception:
            return []
        return [text for text in (str(item or "").strip() for item in items) if text]

    def _event(self, level: str, source: str, message: str, data: dict[str, Any], kind: str = "") -> None:
        if kind:
            data = {**data, "kind": kind}
        if not self.on_event:
            return
        try:
            self.on_event(level, source, message, data)
        except Exception as exc:
            self._report_observer_failure("event", exc)

    def _notify_state(self, state: LoopState) -> None:
        if not self.on_state:
            return
        try:
            self.on_state(state)
        except Exception as exc:
            self._report_observer_failure("state", exc)

    def _report_observer_failure(self, observer: str, exc: Exception) -> None:
        """记录观察者异常，并尽量让它在时间线上可见——但绝不因此中断任务。

        两个通道签名不同，能做的交叉上报只有一种：state 回显坏了，就用事件通道
        公告（事件通道是另一条独立回调）。反过来不行——事件通道坏了没有等价的
        备用通道，此时只剩日志与 `observer_failures` 记录，这是可接受的降级：
        任务继续比"错误必须被展示"更重要。

        报告自身不递归：公告走的是"另一个"通道（state 坏了才用事件通道），公告
        本身再抛就只落日志，绝不回头再报告一次。
        """
        detail = f"{type(exc).__name__}: {exc}"
        self._observer_failures.append({"observer": observer, "error": detail})
        # 坏观察者会在每一轮都抛；只留最近若干条，避免无上限增长。
        if len(self._observer_failures) > 20:
            self._observer_failures = self._observer_failures[-20:]
        logger.warning("agent_loop_observer_failed", observer=observer, error=detail, exc_info=True)
        if self._observer_failure_announced or observer != "state" or not self.on_event:
            return
        # 只公告一次：坏观察者会在每一轮都抛，逐次公告会把时间线刷爆。次数由
        # 总结里的 "回显出错 N 次" 给出。
        self._observer_failure_announced = True
        try:
            self.on_event(
                "warning",
                "agent_loop",
                f"界面回显出错（任务继续执行）：{detail}",
                {"observer_error": detail, "observer": observer, "kind": "handler_error"},
            )
        except Exception:
            logger.warning("agent_loop_observer_failure_report_failed", exc_info=True)

    @property
    def observer_failures(self) -> list[dict[str, Any]]:
        """本次运行里观察者抛过的异常，供运行终态与报告如实反映。"""
        return list(self._observer_failures)

    def _observer_failure_note(self) -> str:
        """给最终报告用的一句话：界面回显出错时，操作员看到的过程可能不完整。"""
        if not self._observer_failures:
            return ""
        first = self._observer_failures[0]
        more = len(self._observer_failures) - 1
        note = f"（提示：界面回显出错 {len(self._observer_failures)} 次，任务未受影响；首个错误：{first['error']}"
        return note + (f"；另有 {more} 次" if more > 0 else "") + "）"

    @property
    def observer_failures(self) -> list[dict[str, Any]]:
        """本次运行里观察者抛过的异常（供运行终态与报告如实反映）。"""
        return list(self._observer_failures)

    def _summary(self, state: LoopState) -> str:
        ok_count = sum(1 for result in state.results if result.ok)
        # 面向操作员的中文总结：这句话会直接出现在对话里。
        if state.status == "completed":
            if state.verification_status == "failed":
                # 模型说收尾了，但机器判据说没达成：措辞随证据走。
                return f"任务已收尾，但完成判据未满足：{state.failure_reason or '目标状态未达到'}。本次共成功执行 {ok_count} 个动作。{self._caveat_note(state)}"
            return f"任务完成：本次共成功执行 {ok_count} 个动作。{self._caveat_note(state)}"
        if state.failure_reason.startswith("agent loop reached max_steps=") and state.results:
            return self._step_limit_summary(state, state.max_steps) + self._caveat_note(state)
        return (state.failure_reason or f"任务在 {len(state.results)} 个动作后停止。") + self._caveat_note(state)

    def _outcome_headline(self, state: LoopState) -> str:
        """报告开头必须是运行时的结论，而不是报告本身的语气。

        模型写的正文（`_llm_attempt_summary`）天然偏"我做了什么"，操作员容易读成
        "做成了"。所以终态先行：先给机器的判定，再看模型怎么描述过程。
        """
        if state.status == "completed":
            return "任务完成。"
        if state.status == "failed":
            return "任务失败，未完成。" if state.failure_reason else "任务未完成。"
        if state.status == "blocked":
            return "任务未完成（已停止在当前阶段）。"
        if state.status in {"cancelled", "canceled"}:
            return "任务已被取消，未完成。"
        return ""

    def _caveat_note(self, state: LoopState) -> str:
        """总结必须附带的两类保留：判据未能评估、界面回显出过错。

        两者都不阻止收尾，但都影响"这话能信到什么程度"——宁可多说一句，也不要
        让"没有证据"被读成"已验证通过"。
        """
        parts: list[str] = []
        if state.verification_status == "unverified" and state.verification_unevaluated:
            metrics = ", ".join(str(item.get("metric") or "?") for item in state.verification_unevaluated[:3])
            parts.append(f"有判据无法评估（{metrics}），未做机器校验")
        note = self._observer_failure_note()
        if note:
            parts.append(note.strip("（）"))
        return f"（{'; '.join(parts)}）" if parts else ""

    def _step_limit_summary(self, state: LoopState, max_steps: int) -> str:
        ok_tools = [result.tool for result in state.results if result.ok]
        failed_tools = [result.tool for result in state.results if not result.ok]
        suffix = f"，失败工具：{', '.join(failed_tools[-3:])}" if failed_tools else ""
        return (
            f"Agent Loop 达到 {max_steps} 步上限，已完成 {len(ok_tools)} 个工具调用"
            f"{suffix}。最终报告应基于已收集的工具结果和遥测说明完成项、未完成项与当前状态。"
        )
