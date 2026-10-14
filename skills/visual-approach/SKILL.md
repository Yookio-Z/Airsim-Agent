---
name: visual-approach
display_name: Visual Approach (Vision Model + Depth)
status: guidance
type: guidance
description: Use this skill when the operator points at something visible in the camera and asks the aircraft to go up to it — "飞过去靠近它", "靠近那辆白车", "穿过门框落在那块黄方块上", "靠近看清楚它是什么". This is the one approach path. Covers the two-step "vision model gives a rough normalised point and size → depth at that point → one small bounded body-frame step" loop, the pixel-to-metre arithmetic for the 90° camera, the mandatory re-check before every further step, passing through an opening (gate / door frame / window), landing on a small object or pad, and when to stop instead of chasing.
required_capabilities: []
subtools: [perception_status, inspect_current_frame, airsim_get_depth_map, airsim_locate_point, drone_get_status, drone_takeoff, drone_move_relative, drone_rotate_to, drone_fly_to, drone_hover, drone_land]
cost: high
risk: high
---

# Visual Approach (Vision Model + Depth)

## Purpose

This is the one and only approach path: the operator points at something visible and the
aircraft goes to it. **The vision model supplies the semantics (what it is, roughly where
it is, roughly how big it looks) and the depth camera supplies the metric distance**, and
every move is a bounded, re-checked body-frame step **sized from that measured distance**.

**The rule that makes this safe**: the step is **computed from the depth reading**, not
fixed — far away you take long steps, close in the step shrinks by itself, and it never
goes past the 2 m standoff. The vision model's point and size are explicitly *rough*
(±30 % estimates), so while the target is still small in frame (`h < 0.10`) each step is
also capped at **one third of the remaining distance**, and the depth is re-read before
the next step. Never derive a world position by hand — `airsim_locate_point` is the only
sanctioned way, and this bounded body-frame loop does not need it.

## Camera geometry — the 90° constant (other skills reference this block)

Our forward camera: **horizontal FOV = 90°**, 4:3 image (800×600 colour, 256×144 depth).

- `tan(fov_h/2) = tan(45°) = 1`, so the horizontal pixel-to-metre factor is exactly 2:
  - metric lateral offset ≈ `Z × (x − 0.5) × 2`,
  - metric width ≈ `Z × w × 2`,
  - metric height ≈ `Z × h × 2` (the vertical half-angle is ~37°, tan ≈ 0.75, so height
    comes out ~30% high — acceptable while `h` is itself a rough estimate).
- Yaw needed to centre a point at normalised `x`: `Δheading ≈ (x − 0.5) × 90°`
  (x = 0.65, i.e. 15% of the frame right of centre → turn about +13.5°).
- `Z` is always `distance_median_m` from `airsim_get_depth_map` at the target's point.

## The two-step loop

```
1. ASK THE VISION MODEL FOR A POINT AND A ROUGH SIZE (one call, 10–20 s)
   inspect_current_frame(
     "画面里那个<目标>在什么位置？给出它的中心归一化坐标和大致宽高
      bbox_hint {x, y, w, h}：x=0 最左、1 最右；y=0 最上、1 最下；
      并说明它是什么、朝哪个方向。")
   → answer.target_candidates[0].bbox_hint = {x: 0.36, y: 0.52, w: 0.08, h: 0.12}
   w、h 是**粗估**（schema 里就叫 rough）：当作 ±30% 的估计，永远不要当测量值用。

2. DEPTH AT THAT POINT (cheap,毫秒级)
   airsim_get_depth_map(query_points="0.36,0.52;0.355,0.525;0.365,0.515")
   → query_results[].distance_m  +  distance_median_m
   **Use normalized (0..1) points** (colour 800x600 vs depth 256x144 — colour pixels
   silently read a different place and return a plausible wrong distance) and **query
   3-5 points around the target**, using distance_median_m: a single depth pixel often
   lands on the object's edge.

2b. SANITY-CHECK THE DEPTH AGAINST THE TARGET'S APPARENT SIZE (before moving)
   The depth image is 1/9 the colour resolution: a distant target is 20-40 px tall
   in colour but only 1-2 px in depth, so a point that looks "on the target" often
   lands on the **background**. Compare two independent estimates:

     z_size ≈ target_height_m / (h × 2)      # 小孔成像反推，用上面的 90° 常数
                                              # person≈1.7 m, car≈1.5 m

   |distance_median_m − z_size| > 50 % of z_size → probably background: **do not
   move** — re-point inside the target and re-read. Measured case (2026-10-12): the
   model guessed "30–50 m" and the depth read 71 m (the building behind) while the
   apparent size implied ≈21 m — two of three numbers wrong, and this check catches it.

3. ONE BOUNDED STEP (body frame, metres)
   tan(fov_h/2) = tan(45°) = 1, so with Z = distance_median_m and x = 归一化横向中心：

     right_m   = Z × (x − 0.5) × 2      # 横向偏移，fov=90° 时是精确值
     forward_m = Z × 0.97               # ≈ Z·cos(15°) 俯角修正；目标靠近画面上下边缘时
                                        #   垂直分量会混进来，这个估计会变差（所以每步要复核）
     remaining = max(0, forward_m − 2.0)   # 留 2 m 站离，绝不贴上去
     step      = clamp(remaining, 0, 12.0) # **一步走多远由实测距离决定**，不是固定步长：
                                           #   远则长（上限 12 m），近则自动缩短，到站离为止
     目标还小（h < 0.10）时：step = min(step, remaining / 3)   # 点/大小是粗值，近处别赌
     right     = clamp(right_m, −3.0, 3.0)
   → drone_move_relative(forward_m=step, right_m=right, velocity=1.0)

   已知距离不需要每步都问视觉模型：**深度每步读**（毫秒级，也是"墙/障碍"的探测），
   视觉模型**每 3 步复核一次**，或在中位距离不再下降、横向偏移突然变大时立刻复核。
   距离 0.5 m 左右就该停：**够近是"到站离"，不是"走完固定米数"**。

4. RE-CHECK DISCIPLINE (mandatory at the boundaries)
   - 每步：airsim_get_depth_map 在目标点上重读（距离是下一步的前提，也是障碍探测）
   - 每 3 步：视觉模型再问一次"画面中央偏<方向>的那个<目标>还在吗？还是同一个吗？"
   - 距离不再下降 / 目标不见了 / 复核说不清 → STOP 并报告，不要继续走
```

Verified on 2026-10-12 against the project's own geometry. At 30 m a ±20-pixel error in
the vision-model point is ≈ ±1.2 m laterally — small relative to a step sized from the
distance, but it *accumulates*, which is why: the target must be centred before stepping,
a long step is only allowed while the target is far (and then never more than one third
of the remaining distance), and every step is followed by a depth re-read.

## Workflow

```
1. Airborne first: drone_get_status → if not flying, drone_takeoff(altitude=3) and wait
   for flying=true; keep 2–3 m throughout (forward-looking camera). Check the camera
   link (perception_status: online, fps, capture_age_s) — a stale frame stops the mission.
2. One look (step 1): record point AND rough size. Never ask twice from the same
   position — a vague answer means the angle or distance is wrong, so move or turn.
3. Depth at that point (step 2), median of 3–5 points, plus the sanity-check (step 2b).
   Missing or absurd distance_m → STOP: without a distance there is no bounded step.
4. Turn to face the target first when it is far off centre (drone_rotate_to, using
   (x − 0.5) × 90°): turning is faster and safer than strafing.
5. Step (step 3). Read the depth again before each further step and re-ask the
   vision model every 3 steps (step 4). Stop when the median distance stops
   decreasing.
6. Finish when the target is **large enough to identify**: its rough normalised height
   h reaches the target fill (default 0.25 — a quarter of the frame height). Because h
   is rough, h ≥ 0.20 counts as "within ~20% of the threshold": take one more step only
   if the depth also decreased, then stop. Never chase a fixed metre count. Then
   drone_hover and report {target, steps, first/last distance, final state, limits hit}.
   For "until satisfied" stopping conditions follow "Looping until satisfied" below.
```

## Looping until satisfied ("直到满意为止")

When the operator's stopping condition is a *quality* rather than a step count
("靠近到满意", "够近为止", "看清楚那个人的样子"), do not converge after the first cycle:

```
repeat:
  1. 观察：inspect_current_frame（点 + 粗尺寸 + 描述）
  2. 判断：目标的归一化高度 h 够了吗？—— h ≥ 0.25 或操作员给的条件，不是固定米数
     够 → drone_hover + 汇报（几步、最终距离、最终状态、有没有触到上限）
     不够 → 继续
  3. 动作：深度读取（每轮）→ 一次 ≤3m 的有界步（或先 drone_rotate_to 对准）
  4. 放弃条件：连续 2 轮中位距离没下降（没在靠近 / 被挡住），或连续 2 次复核
     说不清目标 → 停下并如实汇报
```

循环本身由运行时提供（计划的 `keep_reacting=true`）：每轮都要**基于新观察**重新判断，
而不是重发同一个动作。硬上限约 28 步，所以每轮都要有进展，触到上限就如实收尾。

## Passing through an opening (gate / door frame / window)

Centre the opening, prove it is wide enough, then cross in short checked steps.

1. **Centre it.** One look: "画面里那个门框/开口的中心在什么位置？大致宽高是多少？"
   → `bbox_hint {x, y, w, h}`. Turn with
   `drone_rotate_to(heading_deg = current + (x − 0.5) × 90°)` — the pixel-offset ×
   half-FOV arithmetic above. Re-look only after turning, never from the same heading.
2. **Clearance check BEFORE moving.** One depth reading at the centre only proves
   there is *a* surface far away, not that the path is clear. Sample the centre and
   four points around it at ±0.1 normalised offsets:

   ```
   airsim_get_depth_map(query_points="0.50,0.50;0.40,0.50;0.60,0.50;0.50,0.40;0.50,0.60")
   ```

   With Z_open = centre reading: if **any** surrounding point reads more than ~40%
   nearer (`< 0.6 × Z_open`), something is in the way — **STOP**, do not move forward.
   Re-centre, try another altitude (±0.5 m), or report the opening is not clear.
3. **Is the hole big enough?** The drone is ~0.5 m across; metric opening width ≈
   `Z_open × w × 2`. Below ~1.5 m estimated width the margin is too thin — **report
   instead of attempting it** (`w` is rough, so "1.6 m" can be a 1.1 m hole).
4. **Cross in ≤2 m steps.** `drone_move_relative(forward_m=2, velocity=1.0)`, then
   re-read the depth at the centre point (and the 4 offsets) before the next step. The
   opening must stay roughly centred: `|x − 0.5| ≤ 0.15`. If it drifts past that, stop
   and re-centre with `drone_rotate_to` before moving again.
5. When the opening's depth jumps to "behind you" (the far side dominates the frame),
   stop, take one confirmation look ("开口还在身后吗？前方有什么？") and continue the
   mission or hover and report.

## Landing on a small object / pad

Landing is the one case where the 2 m stand-off is deliberately given up — but only
with a fresh depth reading at the object's point at every step.

1. Approach until the object is centred and `distance_median_m` is 2–3 m, then
   `drone_hover`. Do not keep stepping forward while descending.
2. Optionally take its absolute position: `airsim_locate_point(x, y)` (`x`,`y` = the
   object's normalised point) → `world_pos` (NED), `distance_m`, `valid`. Use it to
   `drone_fly_to` directly above the spot before descending, or to re-find the object
   later. If `valid` is false, skip it — the descent below stands on its own.
3. Descend in 0.5 m steps: `drone_move_relative(up_m=-0.5)`, then re-read the depth at
   the object's point (re-look with the vision model if the point is stale and the
   object has moved in frame). **Never descend without a fresh depth reading there.**
4. Stop descending when either holds: the object is no longer resolvable in the frame
   (the camera looks forward, so a pad right below leaves the view), or the depth at
   its centre is under ~0.6 m.
5. `drone_land`, then report the final position and whether the landing looked centred.
   If the depth jumps far closer than the previous step (something else is
   underneath), hover, stop descending and report — do not land on an unknown surface.

## Core rules

- **3 m per step, hard cap.** A rough vision-model point is a weak reference; a large
  step turns a small pointing error into metres of lateral error.
- **Depth every step, vision model every 3 steps.** Never chain two moves on one
  observation without a fresh depth reading at the target point.
- **Depth is code's job, arithmetic is yours.** You may read `distance_median_m` and
  compute `right/forward/Δheading` from the 90° geometry block; you may **not** derive
  a world position — that is `airsim_locate_point`'s job, and it is optional.
- **The rough size stays rough.** `w`,`h` are ±30% estimates; size-derived numbers
  (`Z × h × 2` etc.) decide things like "will the drone fit", never report measurements.
- **No distance, no move.** A missing/zero/implausible depth reading stops the step.
- **Cross-check the box, don't trust one probe.** When you need coordinates, call
  `airsim_locate_point(x, y, w, h)` with the vision model's rough box: it samples depth
  *inside* the box and *just outside* it and says whether the box is an independent object
  (`object_in_front`) or one continuous surface (`same_surface` — you drew the road/wall, not
  the car), and it reports the metric size (`metric_size`) for a sanity check ("a car is
  ~1.8 m wide, not 0.4 m"). `same_surface` or an absurd size means **re-point, do not move**.
- **A located point is a PROBE, not the target.** `airsim_locate_point` returns where the
  depth ray hits — with a downward-tilted camera that is often the road/building *in front
  of* the target (measured: 9° down → the road 30 m away, not the car). **Never
  `drone_fly_to` such a point**: you will overshoot and lose the target out of frame. Close
  in with bounded `drone_move_relative` steps instead; if you must fly to a coordinate, stop
  **at least 2 m short** of it and keep the target inside the frame.
- **Never trust one localization.** A located point (or a distance) is an estimate from one
  look; after moving part of the way, call `inspect_current_frame` + `airsim_get_depth_map`
  again and update it. Two or three "move partway → re-estimate" rounds converge on a target
  that one "fly straight to the estimate" round loses.
- **Lost the target? Go back, don't keep looking from here.** Turn toward its last known
  position (or step back / climb a little) and look again from a point where it should be in
  frame. Repeated looks from the same spot will keep failing.
- **Stop and report** after 3 steps without convergence, after two ambiguous vision
  answers, or as soon as the target's direction becomes unclear. Report what was
  reached, not what was intended.
- **Centring is the agent's job.** Nothing auto-centres the target: you yaw with
  `drone_rotate_to` using the pixel-offset arithmetic. Only yaw is available — vertical
  framing follows from the fixed 2–3 m altitude.

## Failure policy

- Vision model says "there is something" but the depth at its point is invalid → the
  point is probably on the sky/edge; ask for a tighter point on the object's body.
- Two consecutive steps that do not reduce the median distance → stop: you are not
  converging, likely pointing at the wrong thing or blocked.
- The vision model contradicts itself ("it is a different car now") → stop and report;
  do not keep stepping on a target you can no longer identify.
- Never pass closer than 2 m to a target you are only inspecting; the landing section
  above is the deliberate exception, and it descends with depth.
- A stale camera frame (`perception_status.capture_age_s` growing) stops the mission.

## Not For

- Search ("find a car somewhere") → use `region-search`.
- Multi-vehicle work → use the `formation` skill.
- A semantic question with no motion ("这是什么颜色？") → just call
  `inspect_current_frame`; no approach loop is needed.
- Hand-computing a target's NED position from pixels → not derivable by hand; use
  `airsim_locate_point` when you actually need absolute coordinates.
- Real-vehicle flights: needs an operator-approved bound per step.
