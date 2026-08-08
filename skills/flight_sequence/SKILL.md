---
name: flight_sequence
display_name: Flight Sequence Guidance
status: guidance
type: guidance
description: Use this Markdown skill to plan short UAV command sequences with native tools.
required_capabilities: [flight_control, telemetry]
subtools: [drone_get_status, drone_set_mode, drone_arm, drone_takeoff, drone_move_relative, drone_fly_to, airsim_take_photo, airsim_vlm_analyze_image, airsim_vlm_confirm_target, drone_land, drone_hover]
cost: medium
risk: medium
---

# Flight Sequence Guidance

## Purpose

Guide the LLM through a short ordered UAV task without turning the skill itself into an executable tool.
This skill is read as guidance. Do not call `skill:flight_sequence`.
Choose only tools that appear in `available_tool_cards`.

## When to Use

Use this guidance when the operator gives one compact command that combines several of these goals:

- read current state or connection status
- arm / take off
- move by a small relative distance
- capture a photo
- analyze or confirm image content
- return near the task start position
- land and report the result

Examples:

- "检查无人机状态，正常的话起飞向前飞行三米拍摄照片看看有什么内容，随后返航降落，告诉我结果"
- "起飞到 3 米，向右 2 米，拍照识别目标，然后回到起点降落"

## Operating Rules

- Keep the main action path short. Do not repeat long memory explanations on every turn.
- Before flight, read `drone_get_status`.
- If the task includes horizontal movement, scan, patrol, photo, or visual analysis and the vehicle is not flying or altitude is below 1.5 m, first reach a safe airborne altitude. Use 3 m by default when the operator did not specify altitude.
- For vague movement words such as "a bit", "short distance", "一点距离", "简单扫描", or "扫一下", keep horizontal movement conservative: 1-2 m, velocity about 1.0-1.5 m/s, then hover before taking a photo.
- Do not command horizontal relative movement while near the ground. Prefer `drone_takeoff` first, or explain that the movement is blocked by safety if takeoff is not possible.
- If status shows `armed=false` and `pre_flight_checks_pass=false`, try a simple recovery only when the backend is simulated/SITL:
  1. call `drone_set_mode` with `mode=LOITER`
  2. read status again
  3. if checks look ready, call `drone_arm`
- For takeoff, call `drone_takeoff` with the requested altitude; default to 3 m only if the operator did not specify altitude.
- For body-frame movement, use `drone_move_relative`.
- For open-ended image questions such as "看看有什么内容", use `airsim_take_photo`, then `airsim_vlm_analyze_image`.
- For named target checks such as "是否有红色车辆", use `airsim_take_photo`, then `airsim_vlm_confirm_target`.
- For "扫描周围环境", do not do a large low-altitude sweep by default. At safe altitude, take one or more still frames or a small yaw/position adjustment only if the operator clearly asks for a sweep.
- For "返航" after a task, return near the task start point remembered from the first status readback. If no start position is available, read status and explain the limitation instead of guessing.
- Use `drone_fly_to` for return-to-start with the original `x/y` and a safe airborne `z` such as `-3.0`, then call `drone_land`.
- After landing, call `drone_get_status` once and report final `armed`, `flying`, mode, NED position, and collision state when available.

## Recommended Native Tool Order

For the common "status -> takeoff -> move -> photo -> analyze -> return -> land -> report" task:

1. `drone_get_status`
2. `drone_set_mode` only if needed for simulated preflight recovery
3. `drone_arm`
4. `drone_takeoff`
5. `drone_move_relative`
6. `airsim_take_photo`
7. `airsim_vlm_analyze_image` or `airsim_vlm_confirm_target`
8. `drone_fly_to` back to the initial `x/y`
9. `drone_land`
10. `drone_get_status`

## Reporting

The final answer should be concise but complete:

- state whether the sequence completed
- list the main executed chain in one sentence
- report final state and approximate NED position
- summarize the image/VLM result
- mention any skipped step, failed tool, safety block, or uncertainty

## Failure Policy

- Stop after a flight-control failure unless a simple simulated-mode recovery is clearly appropriate.
- Never invent telemetry, GPS, image content, or target detection results.
- If VLM analysis is slow, state that image analysis was the slow step only after it finishes or fails.
- If return-to-start cannot be resolved from start telemetry, land safely if already near the start; otherwise hover and explain the limitation.
