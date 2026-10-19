"""Skill catalog：用户可编辑的 SKILL.md 指导文档（见 .airsim_agent/skills/）。

技能 = 操作知识，不是动作：激活某个技能只是把它的 Markdown 正文作为一次观察
取回，模型照着它去挑原生工具。注册表默认没有任何可执行执行器——`register()`
保留为将来的确定性算子的扩展点，当前无生产调用方。
"""

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
    """Public card for one registered (executable) skill.

    扩展点：当前生产环境没有任何已注册执行器，技能面只有 SKILL.md 文档；
    实现确定性算子时再把执行器注册进来。
    """

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


class SkillRegistry:
    """Registry of markdown skill documents (plus an empty executor hook)."""

    def __init__(
        self,
        overrides_path: Path | str | None = None,
        docs_dir: Path | str | None = None,
    ) -> None:
        self._skills: dict[str, AgentSkill] = {}
        self._overrides_path = Path(overrides_path) if overrides_path else None
        self._overrides: dict[str, dict[str, Any]] = {}
        self._docs_dir = Path(docs_dir) if docs_dir else Path(__file__).resolve().parents[2] / ".airsim_agent" / "skills"
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
