"""End-to-end check: does the stock detector see the Blocks person, and does the
depth projection put it in the right place?

No flight. Places the person actor at a known NED position in front of the
parked aircraft, grabs Scene + DepthPlanar from the configured cameras, runs the
project's own AxisDetector on the person class, and compares the depth-projected
NED position against the actor's true pose.

Usage: python scripts/probe_person_detection.py [--distance 8]
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import airsim
import numpy as np

sys.path.insert(0, ".")

PERSON = "ThirdPersonCharacter_2"


def grab(client: airsim.MultirotorClient, camera: str, image_type: airsim.ImageType,
         pixels_as_float: bool) -> np.ndarray | None:
    req = airsim.ImageRequest(camera, image_type, pixels_as_float=pixels_as_float, compress=False)
    resp = client.simGetImages([req], vehicle_name="PX4")[0]
    if resp.width == 0 or resp.height == 0:
        return None
    if pixels_as_float:
        img = np.array(resp.image_data_float, dtype=np.float32)
    else:
        img = np.frombuffer(resp.image_data_uint8, dtype=np.uint8)
    return img.reshape(resp.height, resp.width, -1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--distance", type=float, default=8.0)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.35)
    args = ap.parse_args()

    client = airsim.MultirotorClient()
    client.confirmConnection()

    state = client.getMultirotorState(vehicle_name="PX4")
    pos = state.kinematics_estimated.position
    _, _, yaw_rad = airsim.to_eularian_angles(state.kinematics_estimated.orientation)
    yaw_deg = math.degrees(yaw_rad)
    print(f"aircraft NED: ({pos.x_val:.2f}, {pos.y_val:.2f}, {pos.z_val:.2f}) yaw={yaw_deg:.1f} deg")

    # Park the person straight ahead on the aircraft's heading, on the ground.
    yaw = math.radians(yaw_deg)
    tx = pos.x_val + args.distance * math.cos(yaw)
    ty = pos.y_val + args.distance * math.sin(yaw)
    pose = client.simGetObjectPose(PERSON)
    ground_z = pose.position.z_val
    pose.position.x_val, pose.position.y_val = tx, ty
    client.simSetObjectPose(PERSON, pose, True)
    time.sleep(0.6)
    truth = client.simGetObjectPose(PERSON).position
    print(f"person placed at NED: ({truth.x_val:.2f}, {truth.y_val:.2f}, {truth.z_val:.2f})")

    print("grabbing frames ...")
    scene = grab(client, "CameraImage", airsim.ImageType.Scene, False)
    depth = grab(client, "CameraDepth", airsim.ImageType.DepthPlanar, True)
    if scene is None or depth is None:
        print("frame grab failed", scene is None, depth is None)
        return 1
    print(f"scene {scene.shape}  depth {depth.shape}")
    depth = np.squeeze(depth)
    print(f"depth valid pixels: {int(np.count_nonzero((depth > 0.1) & (depth < 100)))} / {depth.size}")

    import cv2

    bgr = cv2.cvtColor(scene[:, :, :3], cv2.COLOR_RGB2BGR)
    cv2.imwrite(".runtime/probe_person_scene.png", bgr)

    from src.modules.yolo_detection import AxisDetector, canonical_target_class

    canon = canonical_target_class("person")
    det = AxisDetector(target_class="person", confidence=args.conf, imgsz=args.imgsz)
    print(f"detector: canonical={canon} kind={det.kind} imgsz={det.imgsz}")

    t0 = time.time()
    dets = None
    for name in ("detect", "run", "update"):
        fn = getattr(det, name, None)
        if fn is None:
            continue
        try:
            dets = fn(bgr)
            print(f"  used detector.{name}() in {time.time() - t0:.2f}s")
            break
        except TypeError as exc:
            print(f"  {name}() signature mismatch: {exc}")
    if dets is None:
        print("no usable detect method; public API:", [m for m in dir(det) if not m.startswith("_")])
        return 1

    print(f"raw detections ({len(dets)}):")
    for d in dets:
        print("  ", d)

    people = [d for d in dets if str(d.get("class", "")).lower() == "person"]
    if not people:
        print("=> NO person detected")
        return 2

    best = max(people, key=lambda d: float(d.get("confidence") or 0.0))
    print(f"best person: conf={best.get('confidence')} bbox={best.get('bbox')}")

    # Depth-project the bbox foot point, the way the perception axis does.
    x1, y1, x2, y2 = [float(v) for v in best["bbox"]]
    dh, dw = depth.shape
    sh, sw = bgr.shape[:2]
    u = int(round((x1 + x2) / 2.0 * dw / sw))
    v = int(round(y2 * dh / sh))
    u = max(0, min(dw - 1, u))
    v = max(0, min(dh - 1, v))
    patch = depth[max(0, v - 2):v + 3, max(0, u - 2):u + 3]
    valid = patch[(patch > 0.1) & (patch < 100)]
    d_m = float(np.median(valid)) if valid.size else float("nan")
    print(f"depth at foot ({u},{v}) = {d_m:.2f} m")

    fov_h = 90.0
    fov_v = math.degrees(2 * math.atan(math.tan(math.radians(fov_h / 2)) * dh / dw))
    ang_h = (u - dw / 2.0) / dw * fov_h
    ang_v = (v - dh / 2.0) / dh * fov_v
    xb = d_m * math.cos(math.radians(ang_v)) * math.cos(math.radians(ang_h))
    yb = d_m * math.cos(math.radians(ang_v)) * math.sin(math.radians(ang_h))
    zb = d_m * math.sin(math.radians(ang_v))
    yaw_r = math.radians(yaw_deg)
    xe = pos.x_val + xb * math.cos(yaw_r) - yb * math.sin(yaw_r)
    ye = pos.y_val + xb * math.sin(yaw_r) + yb * math.cos(yaw_r)
    ca_z = -3.0  # camera 1 m above the aircraft origin (settings.json: Z=-1)
    ze = ca_z + zb

    print(f"projected NED: ({xe:.2f}, {ye:.2f}, {ze:.2f})   [vertical FOV used: {fov_v:.1f} deg]")
    print(f"true NED     : ({truth.x_val:.2f}, {truth.y_val:.2f}, {truth.z_val:.2f})")
    err_h = math.hypot(xe - truth.x_val, ye - truth.y_val)
    print(f"horizontal error = {err_h:.2f} m (target foot vs actor origin; part is the actor's own offset)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
