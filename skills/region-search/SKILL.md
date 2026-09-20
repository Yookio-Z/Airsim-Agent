---
name: region-search
display_name: Region Search & Track Guidance
status: guidance
type: guidance
description: Use this skill when the operator asks to find, search for, locate, confirm or follow a target from the air — for example "搜索前方的蓝色汽车", "找找附近有没有人", "发现目标后跟踪它", "区域搜索并跟踪目标", or any request naming a target class to look for (car / vehicle / truck / bus / person / 车 / 人 / 车辆 / 目标). Covers the fixed-altitude rule for the forward-looking camera, the detection-first / vision-model-sparingly budget, the rotate-sweep and expanding-grid search when the target is not in view, bounded approach on uncertain targets, and when to give up and report instead of claiming a lock.
required_capabilities: []
subtools: [perception_status, airsim_detect_objects, inspect_current_frame, airsim_take_photo, drone_get_status, drone_takeoff, drone_rotate_to, drone_move_relative, drone_fly_to, drone_approach_target, drone_hover, drone_land]
cost: high
risk: high
---

# Region Search & Track Guidance

## Purpose

Guide a "search → confirm → track" mission. The core discipline is **cheap detection first, the vision model sparingly, approach when uncertain** — so the task does not degenerate into repeatedly calling an expensive image model.

Pixel-level detection runs continuously in the perception axis; `airsim_detect_objects` and `perception_status` only read its results. The Agent decides and flies; it never enters the high-frequency control loop.

**Altitude discipline**: the on-board camera looks forward and slightly down. Keep **2–3 m** for the whole search/confirm/track phase (never below 1.8 m, never above 3.5 m). Flying higher over-angles the view, the target shrinks, and detection rate collapses. So take off to 3 m for these tasks — do not use the generic 5 m / 8 m cruise altitude.

**Do not assume a target position.** Every mission starts from "the target is probably not in frame". Never assume from conversation memory or a previous run that the target is straight ahead. If it is not found, search actively, and if it is still not found, report that honestly.

## Intent → Tool

| Intent | Tool | Notes |
|---|---|---|
| Is the target in the current frame? | `airsim_detect_objects` | **First choice.** Structured, cheap: class / confidence / bbox |
| Read the continuous detection snapshot | `perception_status` | health / targets / primary / events |
| Semantic question (colour, model, same object?) | `inspect_current_frame` | **Expensive (vision model)** |
| Confirm a named target | `inspect_current_frame` | **Expensive.** Ask a direct "是否有<目标>" question; one call per position |
| Keep an evidence frame | `airsim_take_photo` | Do not use it to "understand" the scene — text cannot read an image |
| Turn to sweep a heading | `drone_rotate_to` | `heading` in degrees |
| Move to a search grid point | `drone_fly_to` | local NED; keep `z` in −2.5 … −3.0 |
| Close in on a centred target | `drone_approach_target` | Bounded 1–3 m step. **Requires the target already centred** |
| Reposition a short distance | `drone_move_relative` | Body frame, conservative |

## Decision Logic

```
detection available (airsim_detect_objects / perception_status.primary)
        |
        ├─ confidence is sufficient AND the task needs no semantic attribute
        │        → go straight to tracking
        │
        └─ attribute needed (colour / model) OR confidence is low
                 |
                 v
        ONE vision-model call (inspect_current_frame)
                 |
                 ├─ confirmed → track
                 └─ uncertain (too far / not enough detail / vague)
                          |
                          v
                 approach: move one small step toward the target
                 (drone_approach_target ≤3 m, or drone_fly_to 2–3 m closer)
                 keep altitude 2–3 m
                          |
                          v
                 re-detect → only call the vision model again if still needed
                 (at most 3 approach-and-confirm rounds; then report honestly)
```

**Discipline**:
- At most **one** vision-model call per approach position. Do not call it twice from the same place.
- Two consecutive vague vision-model answers mean distance or angle is wrong — approach or change the angle, do not just ask again.
- Once confirmed, re-check with `airsim_detect_objects` during tracking. Do not call the vision model every step.

## Workflow

```
1. Prepare: drone_get_status — require connected and a fresh heartbeat.
   If not flying: drone_takeoff(altitude=3) and poll get_status until flying=true.
   "Airborne" means flying=true, not just a non-zero altitude reading (it may be stale).
   Reconnect at most once (drone_connect); if it still fails, report immediately.
2. Perception self-check: perception_status — require health.online=true.
   If offline, stop the mission and report; do not spin.
3. Search (actively — never assume the target is straight ahead):
   a. In-place sweep (round 0): drone_rotate_to every 30° (0→30→…→330),
      calling airsim_detect_objects(target_class=...) at each heading.
      Target appears → go to Decision Logic. Full circle with nothing → grid search.
   b. Expanding grid (heading 0° north, clockwise; altitude 2–3 m; velocity ≤3):
        ring 1 ±8 m:  (8,0) (0,8) (-8,0) (0,-8)
        ring 2 ±20 m: (20,0) (0,20) (-20,0) (0,-20)
        ring 3 ±40 m: (40,0) (0,40) (-40,0) (0,-40)
      At each point: drone_fly_to → wait 2–3 s to settle → airsim_detect_objects.
   c. Detection range is short: beyond ~25 m a small target is a few pixels.
      Fly within 15 m before concluding "not found".
   d. Three rings with nothing → report "not found". Never fabricate a lock.
4. Confirm by approaching (only when uncertain):
   Target must be CENTRED first — drone_approach_target refuses when horizontal
   error exceeds 0.35, so use drone_rotate_to to centre it.
   Then one bounded step (≤3 m) forward, re-detect, and at most 1 vision-model
   call per position. At most 3 rounds. Never fly blind toward unknown coordinates.
5. Track (after confirmation): hold 2–3 m
   a. Target has world_pos → drone_fly_to near the target, z in −2.5 … −3.0.
   b. Target visible but world_pos is null → hover and re-check the snapshot.
      world_pos requires a depth reading; it is null when depth is unavailable.
      Do NOT invent coordinates or fly blind in this state.
   c. Target lost: seen within the last 30 s → re-check near its last known
      position once; beyond 30 s → report "target lost, tracking stopped".
6. Wrap up: drone_land, then summarise
   {search_rounds, target_class, colour/confirmation, found/failed, confidence, approach_rounds}.
```

## Core Rules

- **Every perception check must be followed by a flight action or a state readback.** No consecutive idle queries.
- **Never assume the target position.** No assuming "straight ahead" from memory; if it is not found, run step 3; if it is still not found, report it.
- **Vision-model budget**: keep total calls per mission at or below 3, and only when the call brings new information (new distance or angle). A single call takes roughly 10–20 s.
- **Centring is the Agent's job.** There is no automatic visual servoing unless the operator has explicitly enabled it; do not assume the system centres the target for you. To centre, use `drone_rotate_to` (yaw). Only yaw is available — vertical framing follows from the fixed 2–3 m altitude.
- **Flight failures**: on any flight-tool error (arm / takeoff / fly_to / land), retry **at most once**, then stop the mission and report `{stage, tool, error, suggestion}`. A typical cause is an unavailable link.
- **Link self-check**: before takeoff confirm `connected` and a fresh heartbeat via `drone_get_status`. Reconnect at most once. If `health.online=false` twice in a row, stop and report.
- **Coordinate semantics**: `world_pos` is NED. To fly toward the target use its `x`/`y` with `z` between −2.5 and −3.0. Do not crowd closer than 2 m.
- **Search bounds**: without an explicit area, work within ±15 m of the start; no single leg over 8 m; total path within 120 m.
- **Time**: aim to finish within 180 s. Prefer landing over running long.
- **Altitude and safety**: 2–3 m throughout (never below 1.8 m, never above 3.5 m). On any anomaly, hover first and report.

## Failure Policy

- Stop and report when the perception service goes offline.
- Stop and report rather than flying blind when a target has no `world_pos`.
- Report "not found" honestly after the three search rings instead of claiming a lock.

## Not For

- Search while the perception service is offline (report the fault first; use `flight-sequence` to get airborne and check the link).
- Dynamic obstacle avoidance (AirSim scenes are mostly static; altitude discipline is the guard).
- Precision landing on a real vehicle (needs recalibration after simulation acceptance) — use `flight-sequence`.
- Multi-vehicle area coverage — use the `formation` skill.
