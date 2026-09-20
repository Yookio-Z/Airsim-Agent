"""High-level skills exposed to the lightweight Agent Loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .skill_docs import load_skill_docs
from .tool_executor import ToolCallResult, ToolRuntime


ToolExecuteCallback = Callable[[str, dict[str, Any], bool], ToolCallResult]


@dataclass(frozen=True)
class SkillSpec:
    """Public card for one Agent-level skill."""

    name: str
    description: str
    when_to_use: str
    required_capabilities: list[str] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    cost: str = "medium"
    risk: str = "medium"
    subtools: list[str] = field(default_factory=list)
    failure_policy: str = "Stop the skill and return a structured failure."
    verification: str = "Return sub tool results and final status for runtime verification."

    @property
    def action_name(self) -> str:
        return f"skill:{self.name}"

    def to_card(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        overrides = overrides or {}
        return {
            "name": self.action_name,
            "display_name": overrides.get("display_name") or self.action_name,
            "purpose": overrides.get("description") or self.description,
            "when_to_use": overrides.get("when_to_use") or self.when_to_use,
            "inputs": dict(overrides.get("parameters") or self.parameters),
            "outputs": "Structured skill result with sub tool results.",
            "cost": overrides.get("cost") or self.cost,
            "risk": overrides.get("risk") or self.risk,
            "required_capabilities": list(overrides.get("required_capabilities") or self.required_capabilities),
            "preconditions": ["backend connected when possible", "safety checks passed"],
            "subtools": list(overrides.get("subtools") or self.subtools),
            "failure_policy": overrides.get("failure_policy") or self.failure_policy,
            "verification": overrides.get("verification") or self.verification,
            "not_for": (
                "Do not use for pure status, link-management, or single explicit manual commands. "
                "Use skills for multi-step goals where the Agent should choose a safe sequence."
            ),
            "kind": "skill",
            "doc_path": overrides.get("doc_path", ""),
            "doc_status": overrides.get("doc_status", ""),
            "doc_type": overrides.get("doc_type", ""),
        }


@dataclass
class AgentSkillResult:
    """Result returned by a high-level Agent skill."""

    skill: str
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "failed",
            "skill": self.skill,
            "message": self.message,
            "data": self.data,
            "tool_results": list(self.tool_results),
        }


class AgentSkill(Protocol):
    spec: SkillSpec

    def execute(
        self,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        """Run this skill against the current tool runtime."""


class _SequentialSkill:
    """Base helper for skills that run a small deterministic tool sequence."""

    spec: SkillSpec

    def _call(
        self,
        tools: ToolRuntime,
        results: list[dict[str, Any]],
        tool: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> ToolCallResult:
        payload = params or {}
        result = execute_tool(tool, payload, dry_run) if execute_tool else tools.execute(tool, payload, dry_run=dry_run)
        results.append(result.to_dict())
        return result

    def _finish(self, ok: bool, message: str, results: list[dict[str, Any]], data: dict[str, Any] | None = None) -> AgentSkillResult:
        return AgentSkillResult(
            skill=self.spec.action_name,
            ok=ok,
            message=message,
            data=data or {},
            tool_results=results,
        )


class NavigationAgentSkill(_SequentialSkill):
    spec = SkillSpec(
        name="navigation",
        description="Arm, take off, optionally fly to a local NED target, then optionally hover.",
        when_to_use="For one-shot navigation tasks where the target local NED coordinate is known.",
        required_capabilities=["flight_control", "telemetry"],
        parameters={
            "altitude": "Takeoff altitude in meters.",
            "x": "Optional target north meters.",
            "y": "Optional target east meters.",
            "z": "Optional target down meters; negative is above origin.",
            "forward_m": "Optional body-frame forward movement in meters.",
            "right_m": "Optional body-frame right movement in meters.",
            "up_m": "Optional upward movement in meters.",
            "velocity": "Target speed in m/s.",
            "hover_after": "Whether to hold position after reaching the target.",
        },
        cost="medium",
        risk="medium",
        subtools=["drone_connect", "drone_get_status", "drone_arm", "drone_takeoff", "drone_fly_to", "drone_move_relative", "drone_hover"],
        failure_policy="Refresh status after uncertain takeoff; continue only if telemetry shows the vehicle is airborne near the target altitude.",
        verification="Reports target_position_ned or relative_move plus every subtool result.",
    )

    def execute(
        self,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        results: list[dict[str, Any]] = []
        altitude = _positive_float(params.get("altitude") or params.get("altitude_m"), 3.0)
        velocity = _positive_float(params.get("velocity") or params.get("speed_mps"), 2.0)

        for tool, payload in (("drone_connect", {}), ("drone_get_status", {})):
            result = self._call(tools, results, tool, payload, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, f"{tool} failed", results, result.data)
        status = _status_from_result(result)

        if status.get("armed") is not True:
            result = self._call(tools, results, "drone_arm", {}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, "drone_arm failed", results, result.data)
            status["armed"] = True
        if status.get("flying") is not True:
            result = self._call(tools, results, "drone_takeoff", {"altitude": altitude}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                status_result = self._call(
                    tools,
                    results,
                    "drone_get_status",
                    {},
                    dry_run=dry_run,
                    execute_tool=execute_tool,
                )
                status = _status_from_result(status_result)
                if not status_result.ok or not _is_airborne_near(status, altitude):
                    return self._finish(False, "drone_takeoff failed", results, result.data)

        target = _target_from_params(params, default_z=-altitude)
        relative = _relative_from_params(params)
        if target:
            result = self._call(
                tools,
                results,
                "drone_fly_to",
                {**target, "velocity": velocity},
                dry_run=dry_run,
                execute_tool=execute_tool,
            )
            if not result.ok:
                return self._finish(False, "navigation target failed", results, result.data)
        elif relative:
            result = self._call(
                tools,
                results,
                "drone_move_relative",
                {**relative, "velocity": velocity},
                dry_run=dry_run,
                execute_tool=execute_tool,
            )
            if not result.ok:
                return self._finish(False, "relative navigation failed", results, result.data)

        if bool(params.get("hover_after", True)):
            result = self._call(tools, results, "drone_hover", {}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, "hover after navigation failed", results, result.data)

        return self._finish(
            True,
            "navigation skill complete",
            results,
            {"target_position_ned": target, "relative_move": relative},
        )


class SearchAgentSkill(_SequentialSkill):
    spec = SkillSpec(
        name="search",
        description="Prepare the vehicle and run a bounded visual sweep using atomic perception tools.",
        when_to_use="For visual target search tasks such as finding a car or person.",
        required_capabilities=["flight_control", "telemetry", "image_capture"],
        parameters={
            "target_class": "Target class such as car, person, truck, or target.",
            "search_altitude": "Search altitude in meters.",
            "search_radius": "Operator-visible search bound in meters; no backend workflow is started.",
            "max_steps": "Maximum sweep headings to inspect.",
            "sweep_headings": "Optional explicit heading list in degrees.",
        },
        cost="high",
        risk="medium",
        subtools=[
            "drone_connect",
            "drone_get_status",
            "drone_arm",
            "drone_takeoff",
            "drone_rotate_to",
            "airsim_take_photo",
            "airsim_detect_objects",
            "inspect_current_frame",
        ],
        failure_policy="Stop if the vehicle cannot safely become airborne or the camera cannot capture a frame.",
        verification="Reports target_class, search_radius, sweep headings, image captures, and any provider/VLM evidence.",
    )

    def execute(
        self,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        results: list[dict[str, Any]] = []
        altitude = _positive_float(params.get("search_altitude") or params.get("altitude"), 3.0)
        radius = _positive_float(params.get("search_radius") or params.get("radius"), 25.0)
        target_class = str(params.get("target_class") or params.get("target_description") or "target").strip() or "target"
        image_type = str(params.get("image_type") or "scene").strip() or "scene"
        max_steps = _clamped_int(params.get("max_steps") or params.get("sweep_steps"), 4, 1, 12)
        headings = _search_headings(params.get("sweep_headings") or params.get("headings"), max_steps)
        confidence = _bounded_float(params.get("confidence"), 0.3, 0.01, 1.0)

        for tool, payload in (("drone_connect", {}), ("drone_get_status", {})):
            result = self._call(tools, results, tool, payload, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, f"{tool} failed", results, result.data)
        status = _status_from_result(result)

        if status.get("armed") is not True:
            result = self._call(tools, results, "drone_arm", {}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, "drone_arm failed", results, result.data)
        if status.get("flying") is not True:
            result = self._call(tools, results, "drone_takeoff", {"altitude": altitude}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, "drone_takeoff failed", results, result.data)

        if not _tool_available(tools, "airsim_take_photo"):
            return self._finish(
                False,
                "airsim_take_photo unavailable",
                results,
                {"target_class": target_class, "search_radius": radius, "reason": "missing_image_capture_tool"},
            )

        rotate_available = _tool_available(tools, "drone_rotate_to")
        detection_available = _tool_available(tools, "airsim_detect_objects")
        vlm_available = _tool_available(tools, "inspect_current_frame")

        for index, heading in enumerate(headings, 1):
            if rotate_available:
                rotation = self._call(
                    tools,
                    results,
                    "drone_rotate_to",
                    {"heading_deg": heading},
                    dry_run=dry_run,
                    execute_tool=execute_tool,
                )
                if not rotation.ok:
                    return self._finish(False, "drone_rotate_to failed", results, rotation.data)

            capture = self._call(
                tools,
                results,
                "airsim_take_photo",
                {"image_type": image_type, "auto_save": bool(params.get("auto_save", True))},
                dry_run=dry_run,
                execute_tool=execute_tool,
            )
            if not capture.ok:
                return self._finish(False, "airsim_take_photo failed", results, capture.data)

            capture_evidence = _target_evidence(capture.data, target_class)
            if capture_evidence:
                return self._finish(
                    True,
                    "search skill complete: target found in capture evidence",
                    results,
                    {
                        "target_class": target_class,
                        "search_altitude": altitude,
                        "search_radius": radius,
                        "target_found": True,
                        "provider": "airsim_take_photo",
                        "heading_deg": heading,
                        "evidence": capture_evidence,
                    },
                )

            if detection_available:
                detection = self._call(
                    tools,
                    results,
                    "airsim_detect_objects",
                    {"target_class": target_class, "confidence": confidence},
                    dry_run=dry_run,
                    execute_tool=execute_tool,
                )
                evidence = _target_evidence(detection.data, target_class) if detection.ok else None
                if evidence:
                    return self._finish(
                        True,
                        "search skill complete: target found by detection provider",
                        results,
                        {
                            "target_class": target_class,
                            "search_altitude": altitude,
                            "search_radius": radius,
                            "target_found": True,
                            "provider": "airsim_detect_objects",
                            "heading_deg": heading,
                            "evidence": evidence,
                        },
                    )

            if vlm_available:
                confirmation = self._call(
                    tools,
                    results,
                    "inspect_current_frame",
                    {"question": f"画面中是否有{target_class}？请确认目标是否存在，并简述其位置和外观。"},
                    dry_run=dry_run,
                    execute_tool=execute_tool,
                )
                evidence = _target_evidence(confirmation.data, target_class) if confirmation.ok else None
                if evidence:
                    return self._finish(
                        True,
                        "search skill complete: target confirmed by VLM",
                        results,
                        {
                            "target_class": target_class,
                            "search_altitude": altitude,
                            "search_radius": radius,
                            "target_found": True,
                            "provider": "inspect_current_frame",
                            "heading_deg": heading,
                            "evidence": evidence,
                        },
                    )

        return self._finish(
            True,
            "search skill complete: target not confirmed",
            results,
            {
                "target_class": target_class,
                "search_altitude": altitude,
                "search_radius": radius,
                "target_found": False,
                "sweep_headings": headings,
                "providers": {
                    "rotation": rotate_available,
                    "detection": detection_available,
                    "vlm": vlm_available,
                },
            },
        )


class VisualObserveAgentSkill(_SequentialSkill):
    spec = SkillSpec(
        name="visual_observe",
        description="Capture the current camera frame and run open-ended VLM analysis or target confirmation.",
        when_to_use=(
            "For questions about what the drone currently sees, or for confirming whether a described target "
            "is visible before moving."
        ),
        required_capabilities=["image_capture"],
        parameters={
            "question": "Open-ended question about the current camera frame.",
            "target_description": "Optional target to confirm, such as red car or person.",
            "image_type": "Camera image type, usually scene.",
        },
        cost="medium",
        risk="low",
        subtools=["airsim_take_photo", "inspect_current_frame"],
        failure_policy="Never move the vehicle. If capture or VLM analysis fails, return the failure with camera context.",
        verification="Reports capture result and VLM analysis/confirmation result.",
    )

    def execute(
        self,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        results: list[dict[str, Any]] = []
        image_type = str(params.get("image_type") or "scene")
        question = str(params.get("question") or "").strip()
        target = str(params.get("target_description") or params.get("target_class") or "").strip()

        capture = self._call(
            tools,
            results,
            "airsim_take_photo",
            {"image_type": image_type, "auto_save": bool(params.get("auto_save", False))},
            dry_run=dry_run,
            execute_tool=execute_tool,
        )
        if not capture.ok:
            return self._finish(False, "airsim_take_photo failed", results, capture.data)

        if target:
            analysis = self._call(
                tools,
                results,
                "inspect_current_frame",
                {"question": f"画面中是否有{target}？请确认目标是否存在，并简述其位置和外观。"},
                dry_run=dry_run,
                execute_tool=execute_tool,
            )
            if not analysis.ok:
                return self._finish(False, "inspect_current_frame failed", results, analysis.data)
            return self._finish(True, "visual target confirmation complete", results, {"target_description": target})

        analysis = self._call(
            tools,
            results,
            "inspect_current_frame",
            {"question": question or "请描述当前无人机摄像头画面中可见的信息。"},
            dry_run=dry_run,
            execute_tool=execute_tool,
        )
        if not analysis.ok:
            return self._finish(False, "inspect_current_frame failed", results, analysis.data)
        return self._finish(True, "visual observation complete", results, {"question": question})


class ReturnHomeAgentSkill(_SequentialSkill):
    spec = SkillSpec(
        name="return_home",
        description="Fly back near local origin and optionally land.",
        when_to_use="When the operator asks to return, recover, or end a mission safely.",
        required_capabilities=["flight_control", "telemetry"],
        parameters={
            "altitude": "Return altitude in meters.",
            "velocity": "Return speed in m/s.",
            "land": "Whether to land after returning.",
        },
        cost="medium",
        risk="medium",
        subtools=["drone_connect", "drone_get_status", "drone_fly_to", "drone_land"],
        failure_policy="Stop if return flight fails; land only after return command succeeds.",
        verification="Reports return_position_ned or already_on_ground.",
    )

    def execute(
        self,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        results: list[dict[str, Any]] = []
        altitude = _positive_float(params.get("altitude") or params.get("altitude_m"), 3.0)
        velocity = _positive_float(params.get("velocity") or params.get("speed_mps"), 2.0)

        for tool, payload in (("drone_connect", {}), ("drone_get_status", {})):
            result = self._call(tools, results, tool, payload, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, f"{tool} failed", results, result.data)
        status = _status_from_result(result)
        if status.get("flying") is False and not dry_run:
            return self._finish(True, "return-home skill complete: vehicle already on ground", results, {"already_on_ground": True})

        result = self._call(
            tools,
            results,
            "drone_fly_to",
            {"x": 0.0, "y": 0.0, "z": -altitude, "velocity": velocity},
            dry_run=dry_run,
            execute_tool=execute_tool,
        )
        if not result.ok:
            return self._finish(False, "drone_fly_to failed", results, result.data)

        if bool(params.get("land", True)):
            result = self._call(tools, results, "drone_land", {}, dry_run=dry_run, execute_tool=execute_tool)
            if not result.ok:
                return self._finish(False, "landing after return failed", results, result.data)

        return self._finish(True, "return-home skill complete", results, {"return_position_ned": {"x": 0.0, "y": 0.0, "z": -altitude}})


class SkillRegistry:
    """Registry of high-level Agent skills."""

    def __init__(
        self,
        overrides_path: Path | str | None = None,
        docs_dir: Path | str | None = None,
        register_builtins: bool = False,
    ) -> None:
        self._skills: dict[str, AgentSkill] = {}
        if register_builtins:
            for skill in (NavigationAgentSkill(), SearchAgentSkill(), VisualObserveAgentSkill(), ReturnHomeAgentSkill()):
                self.register(skill)
        self._overrides_path = Path(overrides_path) if overrides_path else None
        self._overrides: dict[str, dict[str, Any]] = {}
        self._docs_dir = Path(docs_dir) if docs_dir else Path(__file__).resolve().parents[2] / "skills"
        self._doc_overrides: dict[str, dict[str, Any]] = {}
        self._doc_markdown: dict[str, str] = {}
        self._load_doc_overrides()
        self._load_overrides()

    def _load_overrides(self) -> None:
        if not self._overrides_path or not self._overrides_path.exists():
            return
        try:
            data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._overrides = {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            self._overrides = {}

    def _load_doc_overrides(self) -> None:
        docs = load_skill_docs(self._docs_dir)
        self._doc_overrides = {
            action_name: doc.to_card_overrides()
            for action_name, doc in docs.items()
        }
        self._doc_markdown = {
            action_name: doc.raw_text
            for action_name, doc in docs.items()
        }

    def reload_docs(self) -> None:
        """Reload user-editable SKILL.md documents."""
        self._load_doc_overrides()

    def _save_overrides(self) -> None:
        if not self._overrides_path:
            return
        self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self._overrides_path.write_text(
            json.dumps(self._overrides, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register(self, skill: AgentSkill) -> None:
        self._skills[skill.spec.action_name] = skill

    def get(self, action_name: str) -> AgentSkill | None:
        return self._skills.get(action_name)

    def get_available(self, capabilities: dict[str, Any]) -> list[SkillSpec]:
        return [skill.spec for skill in self._skills.values() if _supports(capabilities, skill.spec.required_capabilities)]

    def available_cards(self, capabilities: dict[str, Any], memory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cards = [spec.to_card(self._merged_overrides(spec.action_name)) for spec in self.get_available(capabilities)]
        return self._apply_memory_guidance(cards, memory)

    def all_cards(self) -> list[dict[str, Any]]:
        """Return cards for every registered skill (ignoring capability filter)."""
        return [skill.spec.to_card(self._merged_overrides(skill.spec.action_name)) for skill in self._skills.values()]

    def doc_cards(self) -> list[dict[str, Any]]:
        """Return markdown skill documents, including docs without executors."""
        cards: list[dict[str, Any]] = []
        for action_name, overrides in sorted(self._doc_overrides.items()):
            description = str(overrides.get("description") or "")
            cards.append({
                "name": action_name,
                "display_name": overrides.get("display_name") or action_name,
                # Both keys are emitted on purpose. `description` is the trigger
                # signal the catalog and the model read; `purpose` is the key
                # the UI tool-card code reads. Emitting only one of them is what
                # previously left the trigger empty for every skill document.
                "description": description,
                "purpose": description,
                "when_to_use": overrides.get("when_to_use", ""),
                "inputs": dict(overrides.get("parameters") or {}),
                "outputs": "See the markdown skill document.",
                "cost": overrides.get("cost", "medium"),
                "risk": overrides.get("risk", "medium"),
                "required_capabilities": list(overrides.get("required_capabilities") or []),
                "subtools": list(overrides.get("subtools") or []),
                "failure_policy": overrides.get("failure_policy", ""),
                "verification": overrides.get("verification", ""),
                "kind": "skill_doc",
                "doc_path": overrides.get("doc_path", ""),
                "doc_status": overrides.get("doc_status", ""),
                "doc_type": overrides.get("doc_type", ""),
                "executable": action_name in self._skills,
                "markdown": self._doc_markdown.get(action_name, ""),
            })
        return cards

    def usable_doc_cards(self, capabilities: dict[str, Any]) -> list[dict[str, Any]]:
        """Skill documents that are enabled and supported by this backend.

        Disabled/archived docs and docs whose ``required_capabilities`` the
        backend cannot satisfy are dropped entirely rather than listed and then
        blocked, so the model never spends turns on an unusable skill.
        """
        return [
            card
            for card in self.doc_cards()
            if str(card.get("doc_status") or "").lower() not in {"disabled", "archived"}
            if _supports(capabilities, list(card.get("required_capabilities") or []))
        ]

    def activatable(self, capabilities: dict[str, Any]) -> set[str]:
        """Skill action names the model is allowed to activate on this backend."""
        return {str(card.get("name") or "") for card in self.usable_doc_cards(capabilities) if card.get("name")}

    def skill_body(self, action_name: str) -> str:
        """Full SKILL.md text for one skill ('' when unknown).

        This is the second tier of progressive disclosure: only loaded when the
        model actually activates the skill.
        """
        return self._doc_markdown.get(action_name, "")

    def guidance_cards(
        self,
        command: str,
        capabilities: dict[str, Any],
        memory: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the skill CATALOG for the prompt: metadata only, no bodies.

        Two-tier progressive disclosure. This catalog is always present (roughly
        50-100 tokens per skill) so the model knows what guidance exists; the
        full SKILL.md body is loaded only when the model activates the skill via
        the ``skill:<name>`` action (see ``skill_body`` / ``execute``).

        ``command`` is accepted for call-site compatibility and deliberately NOT
        used for matching. Harness-side keyword scoring was removed: the
        ``description`` field is the trigger signal and the model decides
        relevance. Scoring here previously ranked skills by keywords found in
        their own prose, which made two docs match every command and left the
        search/tracking doc permanently unselected.
        """
        cards = self.usable_doc_cards(capabilities)
        return [self._catalog_entry(card) for card in cards[: max(0, int(limit))]]

    @staticmethod
    def _catalog_entry(card: dict[str, Any]) -> dict[str, Any]:
        """One catalog line: enough to decide whether to activate, nothing more."""
        return {
            "name": card.get("name", ""),
            "display_name": card.get("display_name", ""),
            "description": card.get("description", ""),
            "required_capabilities": list(card.get("required_capabilities") or []),
            "subtools": list(card.get("subtools") or []),
            "executable": False,
        }

    def _merged_overrides(self, action_name: str) -> dict[str, Any]:
        return {
            **(self._doc_overrides.get(action_name) or {}),
            **(self._overrides.get(action_name) or {}),
        }

    def _apply_memory_guidance(self, cards: list[dict[str, Any]], memory: dict[str, Any] | None) -> list[dict[str, Any]]:
        guidance = (memory or {}).get("guidance") or {}
        preferred = {
            str(item.get("name")): item
            for item in guidance.get("preferred_skills", []) or []
            if isinstance(item, dict) and item.get("name")
        }
        ranked: list[dict[str, Any]] = []
        for index, card in enumerate(cards):
            item = preferred.get(str(card.get("name")))
            if not item:
                ranked.append({**card, "memory_rank": 1000 + index})
                continue
            note = (
                f"Memory guidance: historically successful for intent '{item.get('intent', '')}' "
                f"({item.get('success_rate', 0)} success rate over {item.get('runs', 0)} runs)."
            )
            ranked.append({
                **card,
                "when_to_use": f"{card.get('when_to_use', '')} {note}".strip(),
                "memory_guidance": item,
                "memory_rank": index,
            })
        ranked.sort(key=lambda card: (int(card.get("memory_rank", 1000)), str(card.get("name", ""))))
        for card in ranked:
            card.pop("memory_rank", None)
        return ranked

    def update_spec(self, action_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update skill card metadata and persist overrides."""
        if action_name not in self._skills:
            raise KeyError(f"unknown skill: {action_name}")
        allowed = {
            "display_name",
            "description",
            "when_to_use",
            "cost",
            "risk",
            "required_capabilities",
            "parameters",
            "subtools",
            "failure_policy",
            "verification",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        self._overrides[action_name] = {**(self._overrides.get(action_name) or {}), **filtered}
        self._save_overrides()
        return self._skills[action_name].spec.to_card(self._merged_overrides(action_name))

    def execute(
        self,
        action_name: str,
        params: dict[str, Any],
        tools: ToolRuntime,
        dry_run: bool = False,
        execute_tool: ToolExecuteCallback | None = None,
    ) -> AgentSkillResult:
        skill = self.get(action_name)
        if skill:
            return skill.execute(params, tools, dry_run=dry_run, execute_tool=execute_tool)

        # Documentation-only skill: activating it loads the Markdown body as
        # operating knowledge. It returns guidance, not an action -- the model
        # then picks native tools from the catalog it already has.
        markdown = self.skill_body(action_name)
        if markdown:
            return AgentSkillResult(
                action_name,
                True,
                f"activated skill guidance: {action_name}",
                data={
                    "status": "activated",
                    "skill": action_name,
                    "markdown": markdown,
                    "message": (
                        "Skill guidance loaded. Follow it while choosing native tools from "
                        "available_tool_cards; this skill itself is not an action."
                    ),
                },
            )
        return AgentSkillResult(action_name, False, f"unknown skill: {action_name}")


def _supports(capabilities: dict[str, Any], required: list[str]) -> bool:
    return all(bool(capabilities.get(name, False)) for name in required)


def _positive_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.1, abs(number))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))




def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _search_headings(value: Any, max_steps: int) -> list[float]:
    raw_items: list[Any]
    if isinstance(value, str) and value.strip():
        raw_items = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        step = 360.0 / max_steps
        return [round(step * index, 3) for index in range(max_steps)]

    headings: list[float] = []
    for item in raw_items:
        try:
            headings.append(float(item) % 360.0)
        except (TypeError, ValueError):
            continue
        if len(headings) >= max_steps:
            break
    if headings:
        return headings
    step = 360.0 / max_steps
    return [round(step * index, 3) for index in range(max_steps)]


def _tool_available(tools: ToolRuntime, tool_name: str) -> bool:
    list_tools = getattr(tools, "list_tools", None)
    if not callable(list_tools):
        return True
    try:
        names = {
            str(item.get("name") or "")
            for item in list_tools()
            if isinstance(item, dict)
        }
    except Exception:
        return True
    return tool_name in names


def _target_evidence(data: Any, target_class: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None

    for key in ("target_found", "found", "verified", "confirmed", "target_confirmed"):
        if data.get(key) is True:
            return {"source_field": key, "value": True}

    status = str(data.get("status") or "").strip().lower()
    if status in {"candidate_found", "target_found", "found", "locked", "target_confirmed", "confirmed"}:
        return {"source_field": "status", "value": status}

    target = str(target_class or "").strip().lower()
    for key in ("verify_detections", "detections", "objects", "candidates", "target_candidates", "matches"):
        items = data.get(key)
        if not isinstance(items, list) or not items:
            continue
        matches = [item for item in items if _candidate_matches_target(item, target)]
        if matches:
            return {"source_field": key, "matches": matches[:3], "count": len(matches)}
        if target in {"", "target", "object"}:
            return {"source_field": key, "matches": items[:3], "count": len(items)}

    for value in data.values():
        if isinstance(value, dict):
            nested = _target_evidence(value, target_class)
            if nested:
                return nested
    return None


def _candidate_matches_target(candidate: Any, target: str) -> bool:
    if not isinstance(candidate, dict):
        return target in {"", "target", "object"}
    if not target or target in {"target", "object"}:
        return True
    for key in ("class", "class_name", "label", "name", "category", "type"):
        value = str(candidate.get(key) or "").strip().lower()
        if value and (target in value or value in target):
            return True
    return False


def _status_from_result(result: ToolCallResult) -> dict[str, Any]:
    data = result.data if isinstance(result.data, dict) else {}
    if "drone" in data and isinstance(data["drone"], dict):
        return data["drone"]
    return data


def _is_airborne_near(status: dict[str, Any], altitude: float) -> bool:
    if not isinstance(status, dict):
        return False
    flying = bool(status.get("flying"))
    current = _altitude_from_status(status)
    if current is None:
        return flying
    threshold = max(0.5, min(abs(altitude) * 0.75, abs(altitude) - 0.5 if abs(altitude) > 1.0 else abs(altitude) * 0.75))
    return flying and current >= threshold


def _altitude_from_status(status: dict[str, Any]) -> float | None:
    for key in ("altitude_m", "altitude"):
        value = status.get(key)
        if value is None:
            continue
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            pass
    pos = status.get("position_ned")
    if isinstance(pos, dict) and pos.get("z") is not None:
        try:
            return abs(float(pos.get("z")))
        except (TypeError, ValueError):
            return None
    return None


def _target_from_params(params: dict[str, Any], default_z: float) -> dict[str, float] | None:
    nested = params.get("ned_target") or params.get("target_position_ned")
    if isinstance(nested, dict):
        params = {**params, **nested}
    if "x" not in params and "y" not in params and "z" not in params:
        return None
    return {
        "x": float(params.get("x", 0.0) or 0.0),
        "y": float(params.get("y", 0.0) or 0.0),
        "z": float(params.get("z", default_z) or default_z),
    }


def _relative_from_params(params: dict[str, Any]) -> dict[str, float] | None:
    nested = params.get("relative_move")
    if isinstance(nested, dict):
        params = {**params, **nested}
    if "forward_m" not in params and "right_m" not in params and "up_m" not in params:
        return None
    move = {
        "forward_m": float(params.get("forward_m", 0.0) or 0.0),
        "right_m": float(params.get("right_m", 0.0) or 0.0),
        "up_m": float(params.get("up_m", 0.0) or 0.0),
    }
    if abs(move["forward_m"]) < 1e-9 and abs(move["right_m"]) < 1e-9 and abs(move["up_m"]) < 1e-9:
        return None
    return move
