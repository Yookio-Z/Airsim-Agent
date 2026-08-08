"""Smoke-test the ROS Provider Gateway from Windows or WSL."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any


def request(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def read_sse(base_url: str, seconds: float, hz: float) -> None:
    url = f"{base_url.rstrip('/')}/providers/px4/telemetry/stream?hz={hz:g}"
    deadline = time.time() + max(0.1, seconds)
    events = 0
    with urllib.request.urlopen(url, timeout=max(5.0, seconds + 3.0)) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                events += 1
                payload = json.loads(line[5:].strip())
                data = payload.get("data", {})
                offboard = data.get("offboard", {})
                print(
                    f"sse[{events}] status={payload.get('status')} "
                    f"px4_seen={data.get('px4_seen')} "
                    f"mode={data.get('mode')} "
                    f"setpoint_hz={offboard.get('publish_rate_hz')}"
                )
            if time.time() >= deadline:
                break
    print(f"SSE events received: {events} in {seconds:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--move-forward", type=float, default=0.0)
    parser.add_argument("--stream-seconds", type=float, default=0.0)
    parser.add_argument("--stream-hz", type=float, default=10.0)
    parser.add_argument("--configure-test-geofence", action="store_true")
    parser.add_argument("--blocked-target", nargs=3, type=float, metavar=("X", "Y", "Z"))
    args = parser.parse_args()

    for label, method, path, payload in [
        ("health", "GET", "/health", None),
        ("providers", "GET", "/providers", None),
        ("px4_status", "GET", "/providers/px4/status", None),
        ("safety_status", "GET", "/providers/safety/status", None),
        ("obstacle_summary", "POST", "/providers/obstacle/summary", {"max_age_sec": 1.0}),
    ]:
        print(f"\n--- {label} ---")
        print(json.dumps(request(args.url, method, path, payload), ensure_ascii=False, indent=2))

    if args.configure_test_geofence:
        print("\n--- configure test geofence ---")
        payload = {
            "zones": [
                {
                    "id": "origin_box",
                    "min_x": -1.0,
                    "max_x": 1.0,
                    "min_y": -1.0,
                    "max_y": 1.0,
                    "min_z": -5.0,
                    "max_z": 0.0,
                }
            ]
        }
        print(json.dumps(request(args.url, "POST", "/providers/safety/geofence", payload), ensure_ascii=False, indent=2))

    if args.blocked_target:
        x, y, z = args.blocked_target
        print("\n--- blocked target check ---")
        payload = {"x": x, "y": y, "z": z, "wait": False}
        print(json.dumps(request(args.url, "POST", "/providers/px4/setpoint/local_ned", payload), ensure_ascii=False, indent=2))

    if args.move_forward:
        print("\n--- validate forward motion ---")
        payload = {"motion": {"forward_m": args.move_forward, "right_m": 0.0, "up_m": 0.0, "velocity": 1.0}}
        print(json.dumps(request(args.url, "POST", "/providers/obstacle/validate_motion", payload), ensure_ascii=False, indent=2))

    if args.stream_seconds > 0.0:
        print("\n--- telemetry stream ---")
        read_sse(args.url, args.stream_seconds, args.stream_hz)


if __name__ == "__main__":
    main()
