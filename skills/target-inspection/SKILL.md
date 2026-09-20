---
name: target-inspection
display_name: Close Inspection & Re-acquisition
status: guidance
type: guidance
description: Use this skill when the operator wants to get close to a target that is already visible in the camera, identify its features, move to a specific side of it (for example "飞到他前面去看一下车头"), or re-find it after turning away — such as "靠近确认一下它的特征", "再靠近一点看清楚", "飞到他前面去", "转过头还能找得到它". Covers the fill-ratio stopping rule (close in until the target is large enough to identify — never a fixed distance), keeping the target inside the frame while manoeuvring, asking the vision model which way the target faces before repositioning in front of it, and re-acquiring the target after a turn instead of reporting it lost.
required_capabilities: [object_detection]
subtools: [perception_status, airsim_detect_objects, inspect_current_frame, drone_approach_target, drone_move_relative, drone_rotate_to, drone_fly_to, drone_get_status, drone_hover]
cost: high
risk: high
---

# Close Inspection & Re-acquisition

## Purpose

The operator can already see the target in the camera feed and wants the aircraft to **go and look at it**: get near enough to identify its features, position relative to the target (in front of it, beside it), and still find it again after looking away. This skill is the discipline for that, using the perception axis for detection, the vision model only for the questions detection cannot answer, and bounded motion for every repositioning.

## Rule 1 — "close enough" is measured in the image, not in metres

Do **not** approach a fixed number of metres. Approach until the target is large enough to identify:

- `drone_approach_target(min_fill=…)` returns `reached: true` as soon as the target's height fills `min_fill` of the frame (default 0.25 = a quarter of the picture height). That is the "close enough to identify" condition — the tool computes the step from the depth image, so one call usually finishes the approach.
- If it returns `reached: true`, **stop moving closer** and switch to looking: `inspect_current_frame("这辆车是什么颜色、什么品牌、有什么明显特征？")`.
- Only lower `min_fill` (e.g. 0.35–0.45) when the operator explicitly asks for a very close look, and never at the cost of losing the target out of frame.
- The tool refuses when the target is not roughly centred (|ex| > 0.35): turn first with `drone_rotate_to` / `drone_move_relative(right_m=…)` until it is centred, then approach.

## Rule 2 — keep the target in frame at all times

Every repositioning must keep the target visible, because losing it means starting over:

- Move in short, checked steps. After each step read `airsim_detect_objects` (cheap) before deciding the next one.
- If detection drops for one or two frames, do **not** conclude the target is gone: the target is still usable for a few seconds (the axis keeps a short re-acquisition window). Re-centre and re-detect instead of turning away.
- Never command a long blind move while the target is off-centre or unconfirmed.

## Rule 3 — going "in front of" a target needs its facing direction first

Detection gives position, not orientation. To fly to the front of a vehicle:

1. Confirm the target is the intended one and get its features: `inspect_current_frame("画面里的车头朝向哪一边？向左、向右、正对镜头还是背对镜头？")`. The vision model is the authority on orientation — never guess it from the detection box.
2. **Orbit with the basic primitives, in small checked steps**: strafe sideways with `drone_move_relative(right_m=±1.5~2)` and re-detect (`airsim_detect_objects`) after each step; when the target drifts toward the frame edge, re-centre with `drone_rotate_to(heading_deg=…)` using the current heading plus roughly the pixel offset times half the camera's horizontal FOV (≈45° for our 90° camera) — e.g. the target sits 30% right of centre → turn about +13°. Repeat until the aircraft is on the intended side; two to four small steps are usually enough.
3. Re-detect to confirm the target is still locked, then move forward past it in bounded steps (`drone_move_relative(forward_m=…)`), re-detecting between steps — while it is out of frame, **stop and turn back** rather than continuing to fly blind.
4. When it is in view again, inspect it: `inspect_current_frame("这是车头吗？描述看到的细节")`.

If the vision model cannot tell which way the target faces, say so and ask the operator rather than guessing a direction.

## Rule 4 — re-acquisition after turning away

After any turn or transit where the target left the frame, re-acquisition is a normal step, not a failure:

- `airsim_detect_objects` again; if the target reappears, continue.
- If it does not, rotate back toward the last seen bearing and sweep in small increments (`drone_rotate_to`), checking detection after each stop, before considering a wider search.
- Report honestly: "转过去之后又找到了" or "转回来没有找到，最后一次看到它是在…". Never claim a lock you do not have.

## Rule 5 — obstacles stop the approach, they do not get ignored

`drone_approach_target` reads the depth image and **stops itself** when something other than the target is closer than ~1.5 m ahead of the aircraft (`blocked: true`, with `obstacle_m`). When that happens:

- Do **not** retry the same forward step — that is how the aircraft ends up pressed against a wall.
- Read where the blockage is (`inspect_current_frame("挡住去路的是什么？它在画面左/中/右，离得近还是远？")`), then change the route: turn to go around it with small `drone_move_relative` steps, or climb a little (`drone_move_relative(up_m=1)`) and re-approach, or pick a different approach axis entirely.
- If there is no clear path, say so and stop: report what is blocking, where the target was last seen, and what you would need (different altitude / different side) to continue. A blocked inspection reported honestly is a good outcome; a collision is not.

## Budget & failure discipline

- Detection (`airsim_detect_objects`, `perception_status`) is cheap and continuous; the vision model (`inspect_current_frame`) costs a model call — use it to answer *specific* questions (facing direction, features, "is this the front?"), not to re-describe the scene every step.
- If the perception axis has no frame (`capture_age_s` growing), stop and report the camera link — do not keep commanding motion.
- End the mission with the target either locked in the required position with its features reported, or with an explicit statement of where it was last seen and what stopped the inspection.
