---
name: target-inspection
display_name: Close Inspection & Re-acquisition
status: guidance
type: guidance
description: Use this skill when the operator wants to get close to a target that is already visible in the camera, identify its features, move to a specific side of it (for example "飞到他前面去看一下车头"), or re-find it after turning away — such as "靠近确认一下它的特征", "再靠近一点看清楚", "飞到他前面去", "转过头还能找得到它". Covers the one-look + one-depth-reading rule before any movement, the fill-ratio stopping rule expressed through the vision model's rough normalised height, keeping the target inside the frame while manoeuvring, asking the vision model which way the target faces before repositioning in front of it, and re-acquiring the target after a turn instead of reporting it lost.
required_capabilities: []
subtools: [perception_status, inspect_current_frame, airsim_get_depth_map, airsim_locate_point, drone_move_relative, drone_rotate_to, drone_fly_to, drone_get_status, drone_hover, drone_land]
cost: high
risk: high
---

# Close Inspection & Re-acquisition

## Purpose

The operator can already see the target in the camera feed and wants the aircraft to **go
and look at it**: near enough to identify its features, positioned relative to the target
(in front of it, beside it), and findable again after looking away. Semantics come from
the vision model, the metre from the depth camera, motion is bounded and re-checked — the
same discipline as `visual-approach`, applied to a target you can already see (that skill
holds the loop, the geometry block and the 90° constants).

## Rule 0 — one look and one depth reading are the price of movement

Nothing moves on a hunch — the look is the eyes and the depth reading is the ruler:

1. `inspect_current_frame("画面里的是不是<目标>？它在什么位置、大致多大、朝哪个方向？")`
   → `answer.target_candidates[0].bbox_hint = {x, y, w, h}`, the rough normalised centre
   and rough normalised width/height (±30% estimates, never measurements).
2. `airsim_get_depth_map(query_points="0.36,0.52;0.355,0.525;0.365,0.515")` (normalised,
   median of 3–5 nearby points) → `distance_median_m`.
3. Depth missing, zero, implausible, or disagreeing with the target's apparent size
   (`visual-approach` sanity check)? **Do not move** — re-point inside the target and
   read again, or report the distance as unknown.

## Rule 1 — "close enough" is measured in the image, not in metres

Do **not** approach a fixed number of metres. Approach until the target's **rough
normalised height `h` reaches the target fill** — default 0.25, a quarter of the frame
height — then stop and switch to looking:

- `h` is rough, so `h ≥ 0.20` counts as "within ~20% of the threshold": take **one more
  step only if the depth also decreased**, then stop. Never step twice on one look.
- Looking means a specific feature question, not a re-description:
  `inspect_current_frame("这辆车是什么颜色、什么品牌、有什么明显特征？")`.
- Only raise the fill (0.35–0.45) when the operator explicitly asks for a very close
  look, and never at the cost of losing the target out of frame.
- Compute each step with the `visual-approach` arithmetic (remaining = `Z × 0.97 − 2.0`,
  lateral = `Z × (x − 0.5) × 2`); the step size **comes from the measured distance**
  (capped at 12 m, and at one third of the remaining distance while the target is still
  small) — never a fixed number of metres. Keep the 2 m stand-off.

## Rule 2 — keep the target in frame at all times

Every repositioning must keep the target visible, because losing it means starting over:

- Move in short, checked steps; after each step re-read the depth at the target's point —
  cheap, and it shows what sits between you and it — before deciding the next one.
- A missing depth reading is not "the target is gone": re-point where it was, or take one
  fresh look to re-centre. Do not turn away, and never command a long blind move while the
  target is off-centre: re-centre with `drone_rotate_to(current + (x − 0.5) × 90°)`.

## Rule 3 — going "in front of" a target needs its facing direction first

Depth and the rough point give position, not orientation. To fly to the front of a vehicle:

1. Confirm identity and features:
   `inspect_current_frame("画面里的车头朝向哪一边？向左、向右、正对镜头还是背对镜头？")`.
   The vision model is the authority on orientation — never guess it from the point.
2. **Orbit with the basic primitives, in small checked steps**: strafe with
   `drone_move_relative(right_m=±1.5~2)`, take **one look per new position** (a look costs
   10–20 s — never ask twice from the same spot), and re-centre when the target drifts to
   the frame edge using the yaw arithmetic above (15% right of centre → about +13°).
   Two to four small steps are usually enough.
3. Then move forward past it in bounded steps (`drone_move_relative(forward_m=…)`,
   re-reading depth each step); while it is out of frame, **stop and turn back** rather
   than flying blind.
4. When it is in view again, inspect it: `inspect_current_frame("这是车头吗？描述看到的细节")`.

If the vision model cannot tell which way the target faces, say so and ask the operator
rather than guessing a direction.

## Rule 4 — re-acquisition after turning away

After any turn or transit where the target left the frame, re-acquisition is a normal
step, not a failure:

- Rotate back toward the last seen bearing (`drone_rotate_to`) and sweep in small
  increments, **one look per heading**, before considering a wider search.
- If you took a world position earlier with `airsim_locate_point` (optional), use its
  `world_pos` to fly back near the last known spot (`drone_fly_to`) and look once — the
  cheapest way to re-find a target you already located.
- Report honestly: "转过去之后又找到了" or "转回来没有找到，最后一次看到它是在…".
  Never claim a target you cannot see.

## Rule 5 — obstacles stop the approach, they do not get ignored

There is no automatic obstacle check in the motion tools; **you** sample the depth:

- Before a forward step, read depth between you and the target. Anything ahead reading
  more than ~40% nearer than the target's own distance **blocks the step** (the clearance
  check from the opening section of `visual-approach`) — do not take it.
- Do not retry the same forward step; that is how the aircraft ends up pressed against a
  wall. Look once ("挡住去路的是什么？在画面左/中/右？"), then change the route: go around it
  with small `drone_move_relative` steps, climb a little (`up_m=1`) and re-approach, or
  pick a different approach axis.
- No clear path → say so and stop: report what is blocking, where the target was last
  seen, and what you would need (different altitude / side) to continue. A blocked
  inspection reported honestly is a good outcome; a collision is not.

## Budget & failure discipline

- A look (`inspect_current_frame`) costs 10–20 s and a model call — one per position, and
  only for *specific* questions (is this it, facing direction, features). Depth
  (`airsim_get_depth_map`) is cheap: read it every step.
- A stale camera frame (`perception_status` → growing `capture_age_s`) stops the mission.
- End with the target approached, identified and reported — or with an explicit statement
  of where it was last seen and what stopped the inspection.
