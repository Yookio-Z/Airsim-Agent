"""Test whether the scene's person actor can be driven by the API.

Answers the feasibility question behind "track a moving person": the stock
Blocks person is a static mannequin, so a moving target only exists if the API
can move the actor. Tries simSetObjectPose and reports whether the pose sticks.
"""

from __future__ import annotations

import re
import sys
import time

import airsim

KEY = re.compile(r"thirdperson|person|twinblast|character|human|mannequin", re.I)


def main() -> int:
    client = airsim.MultirotorClient()
    client.confirmConnection()

    objs = sorted(client.simListSceneObjects(".*"))
    hits = [o for o in objs if KEY.search(o)]
    print("candidates:", hits)
    if not hits:
        return 1

    target = "ThirdPersonCharacter_2" if "ThirdPersonCharacter_2" in hits else hits[0]
    print(f"target = {target}")

    before = client.simGetObjectPose(target).position
    print(f"before: ({before.x_val:.3f}, {before.y_val:.3f}, {before.z_val:.3f})")

    pose = client.simGetObjectPose(target)
    pose.position.x_val = before.x_val + 2.0
    pose.position.y_val = before.y_val + 3.0
    print("calling simSetObjectPose(dx=+2.0, dy=+3.0, teleport=True) ...")
    try:
        ok = client.simSetObjectPose(target, pose, True)
        print("  returned:", ok)
    except Exception as exc:
        print("  raised:", type(exc).__name__, exc)
        ok = None

    time.sleep(0.5)
    after = client.simGetObjectPose(target).position
    print(f"after : ({after.x_val:.3f}, {after.y_val:.3f}, {after.z_val:.3f})")
    moved = abs(after.x_val - before.x_val) + abs(after.y_val - before.y_val)
    print(f"delta = {moved:.3f} m  -> {'MOVABLE' if moved > 0.5 else 'NOT movable'}")

    # Continuous small steps: does the actor follow a scripted path at ~20 Hz?
    if moved > 0.5:
        print("=== scripted 3 s path @ 20 Hz ===")
        t0 = time.time()
        n = 0
        base = client.simGetObjectPose(target).position
        while time.time() - t0 < 3.0:
            t = time.time() - t0
            p = client.simGetObjectPose(target)
            p.position.x_val = base.x_val + 3.0 * t
            p.position.y_val = base.y_val
            client.simSetObjectPose(target, p, True)
            n += 1
        end = client.simGetObjectPose(target).position
        print(f"  {n} set calls in 3.0 s -> {n / 3.0:.1f} Hz")
        print(f"  end pose: ({end.x_val:.2f}, {end.y_val:.2f}, {end.z_val:.2f})")
        print(f"  expected x ≈ {base.x_val + 3.0:.2f}")

        # Did it actually keep moving between calls, or only on our writes?
        p1 = client.simGetObjectPose(target).position
        time.sleep(1.0)
        p2 = client.simGetObjectPose(target).position
        print(f"  drift with no writes over 1 s: {((p2.x_val - p1.x_val) ** 2 + (p2.y_val - p1.y_val) ** 2) ** 0.5:.3f} m")

    return 0


if __name__ == "__main__":
    sys.exit(main())
