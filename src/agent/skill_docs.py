"""Markdown-backed skill documentation loader.

Skills have two faces:
- executable hard skills implemented in Python
- user-editable markdown documents that describe when and how to use them

The loader keeps markdown optional. If a document is missing or malformed, the
runtime falls back to the Python SkillSpec already registered in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillDoc:
    name: str
    path: Path
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""

    @property
    def action_name(self) -> str:
        return f"skill:{self.name}"

    def to_card_overrides(self) -> dict[str, Any]:
        description = str(
            self.metadata.get("description")
            or self.sections.get("purpose")
            or self.sections.get("objective")
            or self.title
        ).strip()
        when_to_use = str(self.sections.get("when to use") or self.metadata.get("when_to_use") or "").strip()
        failure_policy = str(self.sections.get("failure policy") or self.metadata.get("failure_policy") or "").strip()
        verification = str(self.sections.get("verification") or self.metadata.get("verification") or "").strip()
        parameters = _section_parameters(self.sections.get("inputs", ""))
        overrides: dict[str, Any] = {
            "display_name": self.metadata.get("display_name") or self.title or self.action_name,
            "description": description,
            "doc_path": str(self.path),
            "doc_status": self.metadata.get("status", ""),
            "doc_type": self.metadata.get("type", ""),
        }
        if when_to_use:
            overrides["when_to_use"] = when_to_use
        if failure_policy:
            overrides["failure_policy"] = failure_policy
        if verification:
            overrides["verification"] = verification
        for key in ("cost", "risk"):
            if self.metadata.get(key):
                overrides[key] = str(self.metadata[key])
        for key in ("required_capabilities", "subtools"):
            values = _as_list(self.metadata.get(key))
            if values:
                overrides[key] = values
        if parameters:
            overrides["parameters"] = parameters
        return overrides


def load_skill_docs(docs_dir: Path | str) -> dict[str, SkillDoc]:
    root = Path(docs_dir)
    if not root.exists():
        return {}

    docs: dict[str, SkillDoc] = {}
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            doc = parse_skill_doc(path)
        except Exception:
            continue
        docs[doc.action_name] = doc
    return docs


def parse_skill_doc(path: Path | str) -> SkillDoc:
    doc_path = Path(path)
    raw = doc_path.read_text(encoding="utf-8").strip()
    metadata, body = _split_frontmatter(raw)
    sections = _sections(body)
    title = _title(body) or str(metadata.get("display_name") or doc_path.parent.name)
    name = str(metadata.get("name") or doc_path.parent.name).strip()
    return SkillDoc(
        name=name,
        path=doc_path,
        title=title,
        metadata=metadata,
        sections=sections,
        raw_text=raw,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    return _parse_frontmatter(lines[1:end]), "\n".join(lines[end + 1 :]).strip()


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        data[key] = _parse_value(value)
    return data


def _parse_value(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return value.strip().strip("\"'")


def _title(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _sections(body: str) -> dict[str, str]:
    result: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            result.setdefault(current, [])
            continue
        if current:
            result[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in result.items()}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _section_parameters(section: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:]
        if ":" not in item:
            continue
        name, description = item.split(":", 1)
        name = name.strip().strip("`")
        description = description.strip()
        if name and description:
            params[name] = description
    return params
