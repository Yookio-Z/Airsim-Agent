"""Read-only sampling of body vs camera attitude to verify AirSim gimbal stabilisation.

Run it, then move the drone forward/backward from the UI (or any other client) while
it samples. The verdict compares the airframe pitch range with the camera world pitch
range: with ``"Gimbal": {"Stabilization": 1, "Pitch": -15, "Roll": 0}`` in
settings.json the camera should hold its world attitude (pitch/roll constant) while
the airframe pitches to accelerate and brake.

Only sends read RPCs (simGetVehiclePose / simGetCameraInfo); no flight commands.
"""
import argparse
import json
import math
from pathlib import Path
import time

import airsim


def quat_to_euler(q) -> tuple[float, float, float]:
    """Quaternion (AirSim wxyz) to (roll, pitch, yaw) in degrees.

    ``airsim.to_euler_angles`` is not present in every client build (the bundled
    one here has no such attribute), so the conversion is done locally.
    """
    w, x, y, z = float(q.w_val), float(q.x_val), float(q.y_val), float(q.z_val)
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="CameraImage")
    parser.add_argument("--vehicle", default="")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41451)
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--hz", type=float, default=10.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    client = airsim.MultirotorClient(ip=args.ip, port=args.port)
    client.confirmConnection()

    out = Path(args.out) if args.out else Path("logs") / ("camera_gimbal_" + time.strftime("%Y%m%d_%H%M%S") + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)

    period = 1.0 / max(0.5, args.hz)
    start = time.monotonic()
    samples = []
    with out.open("x", encoding="utf-8", buffering=1) as stream:
        while time.monotonic() - start < args.seconds:
            tick = time.monotonic()
            try:
                body_q = client.simGetVehiclePose(vehicle_name=args.vehicle).orientation
                cam_q = client.simGetCameraInfo(args.camera, vehicle_name=args.vehicle).pose.orientation
            except Exception as exc:  # noqa: BLE001 — a probe must stop cleanly, not crash mid-flight
                print("sample failed:", exc)
                break
            body_roll, body_pitch, body_yaw = quat_to_euler(body_q)
            cam_roll, cam_pitch, cam_yaw = quat_to_euler(cam_q)
            row = {
                "t": round(time.monotonic() - start, 3),
                "body_pitch_deg": round(body_pitch, 3),
                "body_roll_deg": round(body_roll, 3),
                "body_yaw_deg": round(body_yaw, 3),
                "cam_pitch_deg": round(cam_pitch, 3),
                "cam_roll_deg": round(cam_roll, 3),
                "cam_yaw_deg": round(cam_yaw, 3),
            }
            samples.append(row)
            stream.write(json.dumps(row) + "\n")
            time.sleep(max(0.0, period - (time.monotonic() - tick)))

    def span(key):
        values = [s[key] for s in samples]
        return (min(values), max(values), max(values) - min(values)) if values else (0.0, 0.0, 0.0)

    body_pitch, body_roll = span("body_pitch_deg"), span("body_roll_deg")
    cam_pitch, cam_roll = span("cam_pitch_deg"), span("cam_roll_deg")
    print(f"samples={len(samples)} -> {out}")
    print(f"body pitch {body_pitch[0]:.2f}..{body_pitch[1]:.2f} (span {body_pitch[2]:.2f} deg), roll span {body_roll[2]:.2f}")
    print(f"cam  pitch {cam_pitch[0]:.2f}..{cam_pitch[1]:.2f} (span {cam_pitch[2]:.2f} deg), roll span {cam_roll[2]:.2f}")
    if body_pitch[2] > 2.0 and cam_pitch[2] < body_pitch[2] * 0.35:
        print("VERDICT: camera attitude is decoupled from airframe pitch -> gimbal stabilisation active")
    elif body_pitch[2] > 2.0:
        print("VERDICT: camera pitch tracks airframe pitch -> no stabilisation (Gimbal block not active)")
    else:
        print("VERDICT: airframe barely pitched; move the drone forward/back to make the test meaningful")


if __name__ == "__main__":
    main()
