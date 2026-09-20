# AirSim camera experiment — 2026-09-17

## Scope and outcome

Same running UE4.27 editor, CameraImage Scene 800x600, local RPC 41451, capture target 5 FPS, detection target 4/s and imgsz 1280. No flight commands or PX4 parameter changes were issued. Existing dirty worktree changes were preserved.

The principal observed constraint is editor minimization, not a fixed Python sleep. Restoring the existing editor window restored capture throughput. The editor preference “Use Less CPU when in Background” was already unchecked and was not changed. This experiment does not establish the internal engine/driver mechanism or prove that every background/occlusion state behaves like minimization.

## Measurements

- PNG application baseline: steady batch 2.815 FPS; median get_frame 329.928 ms, state lock 0.006 ms, publish/annotation/JPEG 5.198 ms.
- Raw application while minimized: steady batch 2.806 FPS, median get_frame 334.732 ms. Removing PNG alone did not overcome the primary constraint.
- Restored editor: representative batches 4.835–4.903 FPS with median get_frame 84–85 ms; later heavier-load batches 4.408–4.808 FPS.
- Deliberate minimization reproduced roughly 327–335 ms capture and 2.939 FPS after transition. Transition also coincided with image timeouts, depth breaker activation, and transient MAVLink heartbeat loss. No claim is made that all these events share a proven internal cause.
- Restoring the editor again recovered the link and approximately 4.4–4.8 FPS.
- Final application cached-preview sample: 12.036 seconds, 77 responses, 53 distinct frame timestamps, 4.457 FPS over source timestamp span, all responses online. This is HTTP cache/new-frame verification, not browser decoded-image load FPS.
- Final vehicle state: disarmed, not flying, landed_state=1, LOITER, heartbeat age 0.5 s, link_stale=false.

Bounded alternating RPC tests used a separate read-only client while application capture continued; these are relative comparisons under added load, not isolated simulator throughput:

| Editor state | PNG median pipeline | Raw median pipeline |
|---|---:|---:|
| Minimized, 4 pairs | 385.226 ms | 325.982 ms |
| Restored, 4 pairs | 282.219 ms | 159.437 ms |

Pause probes were approximately 0.5–1.1 ms in a separate short sample. Queue/lock times in that separate client were negligible; this does not measure depth contention inside the application's controller.

## Retained implementation

- AirSimController.capture_frame returns an owned, writable BGR array from raw Scene bytes, with dimensions/payload validation. Existing capture_image continues to return PNG bytes.
- LazyControllerFrameSource uses raw Scene only for recognized loopback addresses. Other controllers, remote addresses and non-Scene types retain encoded transport.
- Set process environment DRONE_PERCEPTION_RAW_SCENE=0 before starting the server to restore PNG transport. Default is 1. No .env or simulator settings were edited.
- Controller records last RPC stage durations in caller thread-local storage. The capture loop logs median capture, lock and publication costs and monotonic sustained throughput per 50 completed frames. Capture includes provider/connection/pause/image/decode work; publication combines annotation and JPEG encoding. These are not individual server rendering/compression timings.
- Existing short-window FPS estimator was not changed; use sustained_fps or timestamp deltas for these results.

PNG/raw previews were inspected: matching scene orientation and color, no vertical flip or red/blue swap, detection overlay present.

## Verification

- Full suite at implementation stage: 497 passed, 7 pre-existing failures, 2 warnings. Failure names match the previously established baseline.
- Five new raw capture contract tests: passed (owned BGR memory, malformed length rejection, PNG photo contract, feature switch, remote transport preservation).
- Final focused Python run: 53 passed, one existing AirSim preemption timeout failure (~6.52 seconds vs <2 seconds).
- Frontend FPS regression: 3 passed.
- git diff --check: no whitespace errors; line-ending warnings only.

## Flight stability boundary

No flight-stability code or PX4 gains were modified. The observed heartbeat interruption during the minimization experiment makes consistent simulation timing a prerequisite for meaningful takeoff comparisons. This is not proof that minimization caused the historical takeoff pitch excursion, and no reduction in flight oscillation has been verified. Native takeoff setpoint/response ULog correlation and controlled flight trials remain outstanding.

## Evidence

- logs/camera_baseline_runtime.log
- logs/camera_raw_runtime.log
- logs/camera_rpc_comparison.json
- logs/camera_rpc_visible_comparison.json
- logs/camera_final_preview_samples.json
- logs/camera_baseline_preview.jpg
- logs/camera_raw_preview.jpg
- logs/camera_optimization_pytest.log

Re-run bounded RPC comparison from repository root:

```sh
.venv/Scripts/python.exe -m scripts.benchmark_camera_rpc --pairs 4 --output logs/camera_rpc_new_comparison.json
```

Keep the editor restored rather than minimized during simulation. Window restoration, not a newly changed editor preference, is the tested workaround. Higher CPU/GPU activity while rendering normally is expected.
