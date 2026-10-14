---
name: region-search
display_name: Region Search & Follow Guidance
status: guidance
type: guidance
description: Use this skill when the operator asks to find, search for, locate, confirm or follow a target from the air — for example "搜索前方的蓝色汽车", "找找附近有没有人", "发现目标后跟着它", "区域搜索并跟踪目标", or any request naming a target to look for (car / vehicle / truck / bus / person / 车 / 人 / 车辆 / 目标). Covers the fixed-altitude rule for the forward-looking camera, the strict vision-model look budget (one look per heading, at most 8 headings per position and 3 positions), the rotate-sweep and expanding-ring search when the target is not in view, the bounded depth-based approach after a sighting, and when to give up and report instead of inventing a target.
required_capabilities: []
subtools: [perception_status, inspect_current_frame, airsim_get_depth_map, airsim_locate_point, airsim_take_photo, drone_get_status, drone_takeoff, drone_rotate_to, drone_move_relative, drone_fly_to, drone_hover, drone_land]
cost: high
risk: high
---

# Region Search & Follow Guidance

## Purpose

Guide a "search → confirm → follow" mission: **the vision model is the only sensor that
can say whether the target is there**, and every look is a model call that costs
**10–20 s**. The core discipline is a strict look budget, flight between looks, and an
honest "not found" — so the task does not degenerate into re-asking the same question
from the same place.

The Agent decides and flies; the camera and depth tools are read-only.

**Altitude discipline**: the on-board camera looks forward and slightly down. Keep **2–3 m**
for the whole search/confirm/follow phase (never below 1.8 m, never above 3.5 m). Flying
higher over-angles the view, the target shrinks to a handful of pixels, and the vision
model stops resolving it. Take off to 3 m for these tasks — not the generic 5 m / 8 m
cruise altitude.

**Do not assume a target position.** Every mission starts from "the target is probably not
in frame". Never assume from conversation memory or a previous run that the target is
straight ahead. If it is not found, search actively; if it is still not found, report that
honestly.

**The look budget (hard):** one look = one `inspect_current_frame` call with **one** direct
question ("画面里有没有<目标>？在哪个方位（左/中/右）？离得近还是远？"), 10–20 s each;
**at most 8 headings per position** and **at most 3 positions** per mission — 24 looks,
i.e. several minutes already. **Never re-ask from the same position**: a vague or negative
answer means the angle or distance is wrong, so turn to a new heading or fly to a new
position first.

## Intent → Tool

| Intent | Tool | Notes |
|---|---|---|
| Camera/frame health | `perception_status` | `online`, `fps`, `capture_age_s`, `view` — camera health only |
| Is the target in this view? | `inspect_current_frame` | **The only way to see a target.** One direct question per position |
| Distance to a seen target | `airsim_get_depth_map` | Normalised points, median of 3–5; cheap, read it before any step |
| Absolute position of a seen target | `airsim_locate_point` | Normalised point → `world_pos` (NED), `distance_m`, `valid`; optional |
| Keep an evidence frame | `airsim_take_photo` | Do not use it to "understand" the scene — text cannot read an image |
| Turn to sweep a heading | `drone_rotate_to` | absolute `heading` in degrees |
| Move to a search ring point | `drone_fly_to` | local NED; keep `z` in −2.5 … −3.0 |
| Reposition a short distance | `drone_move_relative` | body frame, conservative (1–2 m) |
| Hold while looking / re-looking | `drone_hover` | hover before each look |

## Decision Logic

```
look (inspect_current_frame) from the current heading
        |
        ├─ target seen → confirm identity in the SAME answer; read depth at its
        │   point; then approach one bounded step **sized from that depth** per
        │   look/position (never a fixed number of metres), or
        │   hand over to the visual-approach skill for a full approach
        │
        ├─ "maybe" / too far / unclear → do not ask again from here. Either take
        │   one depth-sized approach step and look again, or turn
        │   to the next heading. At most 3 approach-and-confirm rounds
        │
        └─ nothing → next heading (≤8 per position) → next ring position (≤3)
                 → honest "not found"
```

## Workflow

```
1. Prepare: drone_get_status — require connected and a fresh heartbeat.
   If not flying: drone_takeoff(altitude=3) and poll get_status until flying=true.
   "Airborne" means flying=true, not just a non-zero altitude reading (it may be stale).
   Retry the connection at most once; if it still fails, report immediately.
2. Perception self-check: perception_status — require the camera link online with a
   fresh frame. If offline or `capture_age_s` is growing, stop the mission and report;
   do not spin.
3. Search (actively — never assume the target is straight ahead). One look per heading,
   never two:
   a. In-place sweep (round 0): drone_rotate_to at 45° steps (0→45→…→315) — at most
      8 headings. At each heading: hover, wait 2–3 s to settle, then ONE look.
      Target seen → go to step 4. Full circle with nothing → fly to the ring.
   b. Expanding ring: at most 3 positions in total — the start position plus at most
      2 ring points, further out each time and at the same 2–3 m altitude, e.g.
      drone_fly_to(8, 0) then drone_fly_to(16, 0) (local NED, z kept in −2.5 … −3.0),
      along the heading where the last sweep looked most promising. At each point:
      settle 2–3 s, then repeat the 8-heading sweep.
      (8 headings × 3 positions = the whole budget: 24 looks, minutes of model time.)
   c. Resolution limit: beyond ~25 m a small target is a handful of pixels and the
      vision model will say "看不清". Fly within 15 m of a spot before concluding
      "not found" there.
   d. Budget exhausted with nothing → report "not found". Never invent a target.
4. Confirm (one look): a direct attribute question
   ("那辆车的颜色/特征是不是<目标>？"), reading bbox_hint {x, y, w, h} from the same
   answer. If it is close enough to identify, hover and report. If it is still far,
   approach one bounded step: depth at its point (median of 3–5) → forward step sized
   from that distance (visual-approach arithmetic; never a fixed 3 m)
   per the visual-approach arithmetic — one look per new position, at most 3 rounds.
   Never fly toward unknown coordinates.
5. Follow / hold (after confirmation): keep the target 2–3 m away and in frame.
   a. Read depth at its point before every step; step with drone_move_relative or
      drone_fly_to (z in −2.5 … −3.0) only while the target is visible and centred.
   b. Want an absolute anchor? airsim_locate_point(x, y) gives its `world_pos` (NED)
      — optional, use it to fly to the spot or to re-find the target.
   c. Target leaves the frame: rotate back toward its last bearing and take ONE look.
      Seen within the last 30 s → continue. Beyond 30 s or two failed looks → report
      "target lost" and stop.
6. Wrap up: drone_land, then summarise
   {search_rounds, looks_used, target_description, found/failed, approach_rounds}.
```

## Core Rules

- **Every perception check must be followed by a flight action or a state readback.** No
  consecutive idle queries.
- **Look budget**: one look per heading, ≤8 headings per position, ≤3 positions, one
  question per look. Never re-ask from the same position. Two vague answers in a row mean
  the angle or distance is wrong — move or turn.
- **Centring is the Agent's job.** Nothing auto-centres a target for you. Yaw with
  `drone_rotate_to(heading_deg = current + (x − 0.5) × 90°)` using the 90° camera
  geometry block in `visual-approach` (the target 15% right of centre → about +13°).
  Only yaw is available; vertical framing follows from the fixed 2–3 m altitude.
- **Flight failures**: on any flight-tool error (arm / takeoff / fly_to / land), retry
  **at most once**, then stop the mission and report `{stage, tool, error, suggestion}`.
  A typical cause is an unavailable link.
- **Link self-check**: before takeoff confirm `connected` and a fresh heartbeat via
  `drone_get_status`. Reconnect at most once. If the camera is offline twice in a row,
  stop and report.
- **Absolute coordinates only from `airsim_locate_point`** (`world_pos` is NED). To fly
  toward a located target use its `x`/`y` with `z` between −2.5 and −3.0; never crowd
  closer than 2 m.
- **Search bounds**: without an explicit area, work within ±15 m of the start; no single
  leg over 8 m; total path within 120 m.
- **Time**: aim to finish within 180 s — but a full look budget is already minutes, so
  prefer landing over running long.
- **Altitude and safety**: 2–3 m throughout (never below 1.8 m, never above 3.5 m). On any
  anomaly, hover first and report.

## Failure Policy

- Stop and report when the camera link goes offline or the frame goes stale.
- Stop and report rather than flying blind when a seen target has no valid depth and no
  valid `airsim_locate_point` result.
- Report "not found" honestly after the look budget is spent instead of claiming a target.

## Not For

- Search while the perception/camera service is offline (report the fault first; use
  `flight-sequence` to get airborne and check the link).
- Dynamic obstacle avoidance (AirSim scenes are mostly static; altitude discipline and
  depth checks are the guard).
- Precision landing on a real vehicle (needs recalibration after simulation acceptance) —
  use `flight-sequence`; landing on a small object is covered by `visual-approach`.
- Multi-vehicle area coverage — use the `formation` skill.
