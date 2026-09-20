---
name: flight-sequence
display_name: Flight Sequence Guidance
status: guidance
type: guidance
description: Use this skill when the operator gives ONE compact command that chains several single-vehicle UAV steps together — for example "起飞到3米，向前2米拍照看看有什么，然后返航降落" or "check status, take off, move forward and photograph the area, return and land". Covers the ordered workflow status -> arm -> takeoff -> move -> photo -> vision analysis -> return -> land, including the safe-altitude rule before any horizontal movement, conservative distances for vague wording, and simulated-preflight recovery. Also use it whenever a single-vehicle task involves more than one flight action in sequence (起飞/移动/拍照/返航/降落).
required_capabilities: [flight_control, telemetry]
subtools: [drone_get_status, drone_set_mode, drone_arm, drone_takeoff, drone_move_relative, drone_fly_to, drone_rotate_to, airsim_take_photo, inspect_current_frame, drone_hover, drone_land]
cost: medium
risk: medium
---

# Flight Sequence Guidance

## Purpose

Run a short ordered single-vehicle task without turning this document into an executable tool.
This is operating knowledge: read it, then choose native tools from `available_tool_cards`.
Activating this skill loads guidance only — it performs no flight action.

## Intent → Tool

| Operator intent | Tool | Key parameters |
|---|---|---|
| Read state / connection / position | `drone_get_status` | leave `vehicle_name` empty for a fleet-wide read |
| Recover a failed preflight check | `drone_set_mode` | `mode=LOITER` |
| Arm motors | `drone_arm` | — |
| Take off | `drone_takeoff` | `altitude` (default 3.0 when unspecified) |
| Move a short body-frame distance | `drone_move_relative` | `forward_m` / `right_m` / `up_m`, `velocity` 1.0–1.5 |
| Fly to a known local NED point | `drone_fly_to` | `x`, `y`, `z` (NED; `z=-3.0` is 3 m up) |
| Point the camera at a heading | `drone_rotate_to` | `heading` in degrees |
| Capture a still frame | `airsim_take_photo` | — |
| Open-ended image question ("画面里有什么") | `inspect_current_frame` | `question` |
| Named-target check ("有没有红色车") | `inspect_current_frame` | `question`（直接问"是否有<目标>"） |
| Hold position | `drone_hover` | — |
| Land | `drone_land` | — |

## Workflow

```
1. drone_get_status                    # record the start x/y for the return leg
2. drone_set_mode(mode=LOITER)         # ONLY for simulated preflight recovery
3. drone_arm
4. drone_takeoff(altitude=3.0)
5. drone_move_relative(...)            # body-frame, conservative
6. airsim_take_photo
7. inspect_current_frame                # 开放问题或"是否有<目标>"确认
8. drone_fly_to(x=start_x, y=start_y, z=-3.0)
9. drone_land
10. drone_get_status                   # report the final state
```

## Operating Rules

- Read `drone_get_status` before any flight action.
- **Never command horizontal movement below 1.5 m.** If the vehicle is not flying or is lower than that, take off first (3 m default). If takeoff is impossible, say the movement is blocked by safety instead of trying it.
- For vague movement wording ("一点距离", "稍微", "a bit", "扫一下"), keep horizontal movement to 1–2 m at 1.0–1.5 m/s, then hover before photographing.
- Use `drone_move_relative` for body-frame moves and `drone_fly_to` for a known NED point. Do not convert between them by hand.
- Only attempt preflight recovery when the backend is simulated/SITL: `drone_set_mode(mode=LOITER)`, re-read status, then arm if the checks look ready. On a real vehicle, stop and report.
- For "返航", fly to the start `x`/`y` captured in step 1 at a safe airborne `z`. If no start position was ever read, read status and explain the limitation rather than guessing coordinates.
- For "扫描周围环境", do not fly a large low-altitude sweep. At a safe altitude, take one or two still frames, or make a small yaw adjustment, only if the operator clearly asked for a sweep.
- After landing, read status once and report `armed`, `flying`, mode, NED position, and collision state when available.

## Reporting

State whether the sequence completed; list the executed chain in one sentence; report the final state and approximate NED position; summarise the image or vision-model result; mention any skipped step, failed tool, safety block, or uncertainty.

## Failure Policy

- Stop after a flight-control failure unless a simulated-mode recovery is clearly appropriate.
- Never invent telemetry, GPS coordinates, image content, or detection results.
- If return-to-start cannot be resolved from telemetry, land if already near the start; otherwise hover and explain.

## Not For

- Multi-vehicle or formation work — use the `formation` skill.
- Area search, target confirmation, or tracking — use the `region-search` skill.
- Knowledge questions that need no vehicle action.

## Precise motion, turning, and return-to-home

The operator notices every inaccuracy: a move that lands 0.8 m short, a "turn left 90°" that ends at 60°, a "return home" that flies somewhere unexplained. These rules remove the guesswork.

**Frames — know what "forward" means.** `drone_move_relative(forward_m/right_m/up_m)` moves in the **body frame of the current heading**. After a turn, "forward" points somewhere else: a plan like turn-left-then-forward goes left-then-along-the-new-heading, which is usually what the operator wants — but if they said "go forward 5 m, then turn left and go forward 3 m", the second leg is NOT parallel to the first. When a fixed world direction is required, use `drone_fly_to` (absolute NED) instead of relative moves.

**Turning — always absolute.** `drone_rotate_to(heading_deg)` takes an absolute heading (0 = north, clockwise positive). Never send `90` for "turn left 90°"; read the current heading first (`drone_get_status` → `heading_deg`) and send `(heading - 90) % 360` (right/clockwise: `+`). State the arithmetic in your plan reasoning so it can be checked.

**Distance accuracy.** Relative moves are velocity commands with a tolerance, so expect roughly 0.3–1 m of error per leg. Split long legs (>10 m) into 2–3 segments, read `drone_get_status` after each, and correct the remainder with a second short move. For a precise waypoint, prefer `drone_fly_to` and verify the final position with a status read.

**Return home is an absolute-position task.** Do not try to compose it from relative legs — that is how "return home" ends up nowhere near home. Read the position before takeoff (that is the home point), remember it, and at the end fly back with `drone_fly_to(home_x, home_y, home_z)` at the flight altitude, then `drone_land`. `drone_fly_to` steers the nose toward the target by itself, so do not add a separate rotate; only turn deliberately when the mission needs a specific viewing direction on arrival.

**Heading changes change what the camera sees.** The gimbal holds pitch and roll in the world frame, but **yaw follows the airframe** — after any turn the picture faces a different direction. If the operator cares about what is being looked at, re-confirm the view (`airsim_detect_objects` / `inspect_current_frame`) after turning rather than assuming the earlier framing still holds.

**Never report a motion as done without reading it back.** A tool returning "command sent" is not the same as the vehicle reaching the target: after each motion step in an ordered task, read `drone_get_status` (position, altitude, heading) and compare against the intended value before moving on.

## Preconditions — every flight action has one

Never plan a flight action without first establishing that the vehicle is in a state where it can execute it. Read `drone_get_status` at the start of the task and after every landing.

| Action | Requires | If not satisfied |
|---|---|---|
| `drone_rotate_to`, `drone_move_relative`, `drone_fly_to`, `drone_fly_path` | **airborne** (armed + flying) | arm and take off first — a grounded vehicle cannot turn or translate, the command just fails |
| `drone_takeoff` | **armed** | `drone_arm` first (and only when the operator's task needs flight) |
| `drone_arm` | link up, no active failsafe | report the reason instead of retrying blindly |
| `drone_land` | armed / airborne | already-landed vehicles need no landing |

A previous task's state is **not** current state: a mission that ended in `drone_land` leaves the vehicle disarmed on the ground, so the next turn request must arm and take off again. Do not retry a failed action unchanged — read the status, find out *why* it failed (usually "not airborne" or "not armed"), fix the precondition, then retry once.

Report honestly when a precondition cannot be met ("当前未解锁/已落地，无法转向；需要先解锁起飞"), instead of repeating the same failing command until the loop runs out of steps.
