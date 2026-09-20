"""Probe the running AirSim scene for a movable/detectable person target.

Read-only: enumerates scene objects, finds person-like actors, samples their
pose over a few seconds to answer "does it move", and reports what the depth
projection would need.

Usage:  python scripts/probe_scene_person.py [--seconds 6] [--interval 1.0]
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import airsim

PERSON_RE = re.compile(r"person|human|mannequin|character|pedestrian|npc|actor", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--port", type=int, default=41451)
    args = ap.parse_args()

    client = airsim.MultirotorClient(port=args.port)
    client.confirmConnection()
    print("RPC connected")

    print("=== vehicles ===")
    try:
        for v in client.listVehicles():
            print(" ", v)
    except Exception as exc:
        print("  listVehicles failed:", exc)

    print("=== scene objects (all) ===")
    try:
        objs = client.simListSceneObjects(".*")
    except Exception as exc:
        print("  simListSceneObjects failed:", exc)
        return 1
    print(f"  {len(objs)} objects")
    for name in sorted(objs):
        print("   -", name)

    hits = [o for o in sorted(objs) if PERSON_RE.search(o)]
    print(f"=== person-like objects: {len(hits)} ===")
    for name in hits:
        try:
            pose = client.simGetObjectPose(name)
            p = pose.position
            print(f"  {name}: pos=({p.x_val:.2f},{p.y_val:.2f},{p.z_val:.2f})")
        except Exception as exc:
            print(f"  {name}: pose failed: {exc}")

    if not hits:
        return 2

    # Sample poses over time to see whether the target moves on its own.
    print(f"=== movement sampling ({args.seconds}s @ {args.interval}s) ===")
    samples: dict[str, list[tuple[float, float, float]]] = {n: [] for n in hits}
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        for name in hits:
            try:
                p = client.simGetObjectPose(name).position
                samples[name].append((p.x_val, p.y_val, p.z_val))
            except Exception:
                pass
        time.sleep(args.interval)

    for name, pts in samples.items():
        if len(pts) < 2:
            print(f"  {name}: insufficient samples")
            continue
        dist = 0.0
        for a, b in zip(pts, pts[1:]):
            dist += sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
        dt = max(args.interval * (len(pts) - 1), 1e-6)
        print(f"  {name}: path_len={dist:.2f} m over {dt:.1f}s -> speed≈{dist / dt:.2f} m/s")
        print(f"     first={pts[0]}")
        print(f"     last ={pts[-1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
