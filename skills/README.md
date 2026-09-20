# Agent Skills

Skills are **operating knowledge**, not actions. They teach the Agent how to run a
class of task and which native tools to reach for; every flight action still goes
through the ordinary tool surface with the ordinary safety gates.

## Layout

```
skills/
  <skill-name>/
    SKILL.md          # required
    references/*.md   # optional, loaded on demand
```

`<skill-name>` and the frontmatter `name:` must match exactly, and must be
lowercase letters, digits and hyphens (max 64 chars, no leading/trailing or
double hyphens).

## How a skill reaches the model

Two tiers, so an unused skill costs almost nothing:

1. **Catalog (always in the prompt).** For each enabled skill the model sees
   only `name`, `display_name` and `description` — roughly 50–100 tokens. The
   description is the trigger: it must say **what the skill covers and when to
   use it**, in the imperative ("Use this skill when ...").
2. **Body (loaded on activation).** When a description matches the task, the
   model emits that skill's action name (`skill:<name>`) as its action. The
   runtime then returns the full `SKILL.md` body as an observation, and the model
   follows it while choosing native tools.

A skill with no description can never be triggered, so the description is not
optional decoration.

## Frontmatter

| Key | Effect |
|---|---|
| `name` | Required. Skill identity (`skill:<name>`); must equal the folder name. |
| `description` | Required. The trigger signal — state what it covers **and** when to use it, and include the words an operator would actually say. |
| `status` | `disabled` or `archived` hides the skill entirely. The UI writes this when you toggle a skill. |
| `required_capabilities` | Skill is hidden unless the active backend provides all of them. |
| `display_name`, `cost`, `risk`, `type`, `subtools` | Display and catalogue metadata only. |
| `when_to_use` | Only a fallback: prefer putting "when to use" text in `description`. |

## Writing a good SKILL.md

- **Keep the body under ~500 lines.** Move long reference material into
  `references/` and point at it.
- **Anchor every rule to a real tool.** Prefer an "intent → tool" table naming
  exact tool names and key parameters over prose descriptions of intent.
- **Put load-bearing instructions near the top** — the body may be truncated.
- **Explain why a rule matters** instead of stacking capitalised MUSTs; the
  model follows reasoned constraints more reliably.
- **Say what the skill is not for.** Scope boundaries stop the wrong skill from
  being activated — though note that the description, not the body, is what the
  model uses to decide.
- **Do not invent tools or fields.** If a skill names a tool that is not
  registered, or a snapshot field that is always null on this backend, the Agent
  will burn turns on it. Check the tool surface first.

## Current skills

| Skill | Covers |
|---|---|
| `flight-sequence` | One compact command chaining several single-vehicle steps: status → arm → takeoff → move → photo → vision analysis → return → land. |
| `region-search` | Find / confirm / track a target from the air, including the detection-first budget and the in-place sweep plus expanding-grid search. |
| `formation` | Multi-vehicle formation flight and area coverage via `formation_command`. |

## Legacy executable skills

`src/agent/skill_registry.py` still contains Python executors
(`navigation`, `search`, `visual_observe`, `return_home`) from the earlier design
where a skill was a callable action. They are **not registered**: the runtime
builds the registry without `register_builtins`, and they were retired because
they were too rule-driven and brittle. Only migration tests opt into them.

Extension points for skills that need code belong under the skill folder as
helper scripts, not as the primary policy path.
