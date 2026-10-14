# Agent Skills

Skills are **operating knowledge**, not actions. They teach the Agent how to run a
kind of task and which native tools to reach for; every flight action still goes
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
| `visual-approach` | **The approach path.** Go to any visible target: vision-model point + depth distance loop, the 90° pixel-to-metre arithmetic, passing through an opening, landing on a small object. |
| `target-inspection` | Close inspection and re-acquisition: one look + one depth reading before any movement, the fill-ratio stopping rule via the rough normalised height, orientation before repositioning, obstacles stop the approach. |
| `region-search` | Find / confirm / follow a target from the air with a strict vision-model look budget (one look per heading, ≤8 headings × ≤3 positions) and an honest "not found". |
| `formation` | Multi-vehicle formation flight and area coverage via `formation_command`. |

## Perception decision (project level)

Object detection has been **removed from this project**: there is no detector tool
anywhere in the tool surface. The perception path is **"vision model for semantics + depth
for metric distance"** — `inspect_current_frame` answers "is it there / what is it /
roughly where and how big" (its `bbox_hint` is explicitly rough), and
`airsim_get_depth_map` at that normalised point supplies the metre (`distance_median_m`).
`perception_status` now reports **camera health only** (online, fps, `capture_age_s`,
view) — it carries no targets. Absolute coordinates come from `airsim_locate_point(x, y)`,
which turns a normalised image point plus the drone pose into a NED `world_pos` (optional;
the bounded body-frame loop does not need it). Approach missions are handled by
`visual-approach`.

## Legacy executable skills

`src/agent/skill_registry.py` still contains Python executors
(`navigation`, `search`, `visual_observe`, `return_home`) from the earlier design
where a skill was a callable action. They are **not registered**: the runtime
builds the registry without `register_builtins`, and they were retired because
they were too rule-driven and brittle. Only migration tests opt into them.

Extension points for skills that need code belong under the skill folder as
helper scripts, not as the main policy path.
