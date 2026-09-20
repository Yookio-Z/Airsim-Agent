"""One local simulation takeoff, telemetry recording, and landing via existing controller."""
import concurrent.futures
import json
import math
from pathlib import Path
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
FIELDS = ("position_ned", "velocity_ned", "attitude_rad", "armed", "flying", "mode", "landed_state", "heartbeat_age_s", "link_stale", "offboard_hold_active", "local_position_age_s", "global_position_age_s", "status_text")


def request(path, payload=None, timeout=5):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def main():
    state = request("/api/state")
    idle_deadline = time.monotonic() + 15
    while state["agent_state"]["busy"] and time.monotonic() < idle_deadline:
        time.sleep(0.5)
        state = request("/api/state")
    drone = state["tool_runtime"]["drone"]
    caps = state["agent_state"]["capabilities"]
    assert caps.get("simulated_vehicle") is True and drone.get("real_vehicle") is False
    assert state["tool_runtime"]["backend"] == "px4_mavlink"
    assert not state["agent_state"]["busy"] and not (state.get("current_run") or {}).get("status") == "running"
    assert drone.get("armed") is False and drone.get("landed_state") == 1
    assert drone.get("link_stale") is False and drone.get("heartbeat_age_s", 99) < 2
    assert not state["supervisor"]["emergency_stop"] and not state["supervisor"]["paused"]
    output = Path("logs") / ("takeoff_probe_" + time.strftime("%Y%m%d_%H%M%S") + ".jsonl")
    start = time.monotonic()
    with output.open("x", encoding="utf-8", buffering=1) as stream:
        def record(kind, **data):
            stream.write(json.dumps({"t": round(time.monotonic()-start, 3), "wall_time": time.time(), "kind": kind, **data}, ensure_ascii=False) + "\n")

        def sample():
            d = request("/api/state")["tool_runtime"]["drone"]
            record("telemetry", **{k: d.get(k) for k in FIELDS})
            return d

        sample()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            record("takeoff_start", altitude=3.0)
            future = pool.submit(request, "/api/tool", {"tool": "drone_takeoff", "params": {"altitude": 3.0}, "dry_run": False, "expected_backend": "px4_mavlink"}, 110)
            completed = None
            while time.monotonic() - start < 100:
                d = sample()
                if future.done() and completed is None:
                    result = future.result()
                    completed = time.monotonic()
                    record("takeoff_result", result=result)
                    print("takeoff_result", json.dumps(result, ensure_ascii=False), flush=True)
                tilt = max(abs(float((d.get("attitude_rad") or {}).get(k, 0))) for k in ("roll", "pitch"))
                if d.get("link_stale") or tilt > math.radians(25) or abs(d.get("position_ned", {}).get("z", 0)) > 5:
                    record("abort", reason="link, attitude or altitude envelope exceeded")
                    break
                if completed is not None and time.monotonic() - completed >= 10:
                    break
                time.sleep(0.5)
            if not future.done():
                record("cancel", result=request("/api/control", {"action": "cancel", "expected_backend": "px4_mavlink"}, 15))
                try:
                    record("takeoff_result", result=future.result(timeout=15))
                except Exception as exc:
                    record("takeoff_error", error=str(exc))
        finally:
            record("land_start")
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as landing_pool:
                    landing = landing_pool.submit(request, "/api/control", {"action": "land", "expected_backend": "px4_mavlink", "vehicles": ["px4_sys1"]}, 65)
                    deadline = time.monotonic() + 75
                    reported = False
                    while time.monotonic() < deadline:
                        d = sample()
                        if landing.done() and not reported:
                            result = landing.result()
                            record("land_result", result=result)
                            print("land_result", json.dumps(result, ensure_ascii=False), flush=True)
                            reported = True
                        if reported and d.get("landed_state") == 1 and d.get("armed") is False:
                            record("safe_grounded")
                            break
                        time.sleep(0.5)
                    else:
                        raise RuntimeError("Landing was not confirmed within 75 seconds")
            finally:
                pool.shutdown(wait=False)
                print("evidence", output, flush=True)


if __name__ == "__main__":
    main()
