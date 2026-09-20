"""Opt-in, capture-sampled attitude compensation, not a render-rate gimbal.

AirSim camera info is a world pose; simSetCameraPose takes a body-relative
pose. No settings, flight-control or MAVLink calls are made here. A failed
compensation does not invalidate image capture, but must not be reported as
stabilized. Failures latch (no per-frame retry spam) until an enable transition
or RPC runtime reset. Disable restores the original mount on the next capture.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import airsim


# Quaternion tuples use AirSim's x, y, z, w order; Euler convention is ZYX.
def multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X + x*W + y*Z - z*Y,
            w*Y - x*Z + y*W + z*X,
            w*Z + x*Y - y*X + z*W,
            w*W - x*X - y*Y - z*Z)


def quaternion(q):
    values = (q.x_val, q.y_val, q.z_val, q.w_val)
    norm = math.sqrt(sum(v*v for v in values))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Invalid camera/body quaternion")
    return tuple(v / norm for v in values)


def inverse(q):
    return (-q[0], -q[1], -q[2], q[3])


def rotate(q, xyz):
    return multiply(multiply(q, (*xyz, 0.0)), inverse(q))[:3]


def from_euler(roll, pitch, yaw):
    cr, sr = math.cos(roll/2), math.sin(roll/2)
    cp, sp = math.cos(pitch/2), math.sin(pitch/2)
    cy, sy = math.cos(yaw/2), math.sin(yaw/2)
    return (sr*cp*cy - cr*sp*sy, cr*sp*cy + sr*cp*sy,
            cr*cp*sy - sr*sp*cy, cr*cp*cy + sr*sp*sy)


def pitch_yaw(q):
    x, y, z, w = q
    return (math.asin(max(-1.0, min(1.0, 2*(w*y - z*x)))),
            math.atan2(2*(w*z + x*y), 1 - 2*(y*y + z*z)))


def position(p):
    xyz = (p.x_val, p.y_val, p.z_val)
    if not all(math.isfinite(v) for v in xyz):
        raise ValueError("Invalid camera/body position")
    return xyz


def pose(xyz, q):
    return airsim.Pose(airsim.Vector3r(*xyz), airsim.Quaternionr(*q))


@dataclass
class _CameraState:
    enabled: bool = True
    applied: bool = False
    error: str = ""
    mount_position: tuple | None = None
    mount_orientation: tuple | None = None
    dirty: bool = False  # A set RPC may have changed the simulator even on failure.


class CameraStabilization:
    """Controller-local state, keyed by exact (camera, vehicle), never broadcast.

    configure/status are local only. before_capture runs on the controller's
    single RPC worker, using its real client (never its queued RPC proxy).
    """

    def __init__(self):
        self._states = {}

    def configure(self, camera_name, enabled=True, vehicle_name=""):
        key = (str(camera_name), str(vehicle_name))
        state = self._states.get(key)
        enabled = bool(enabled)
        if state is None:
            if enabled:
                self._states[key] = _CameraState()
        elif state.enabled != enabled:
            state.enabled = enabled
            state.error = ""
            state.applied = False

    def status(self, camera_name, vehicle_name=""):
        state = self._states.get((str(camera_name), str(vehicle_name)))
        return {"enabled": bool(state and state.enabled),
                "applied": bool(state and state.applied),
                "error": state.error if state else ""}

    def reset_runtime(self, *, simulator_reset=False):
        # Detach stale blocked workers. A transport reconnect does NOT reset the
        # camera: retain its ORIGINAL mount, not the last compensated pose.
        # Only a successful explicit simulator reset permits mount rediscovery.
        fresh = CameraStabilization()
        fresh._states = {
            key: (_CameraState(enabled=state.enabled) if simulator_reset else
                  replace(state, applied=False, error=""))
            for key, state in self._states.copy().items()
        }
        return fresh

    def before_capture(self, client, camera_name, vehicle_name=""):
        key = (str(camera_name), str(vehicle_name))
        state = self._states.get(key)
        if state is None or state.error:
            return
        camera_name, vehicle_name = key
        stage = "stabilization"
        try:
            if not state.enabled:
                if state.dirty and state.mount_orientation is not None:
                    stage = "mount restoration"
                    client.simSetCameraPose(camera_name,
                        pose(state.mount_position, state.mount_orientation), vehicle_name=vehicle_name)
                    state.dirty = False
                state.applied = False
                return
            body = client.simGetVehiclePose(vehicle_name=vehicle_name)
            body_q = quaternion(body.orientation)
            inv_body = inverse(body_q)
            if state.mount_orientation is None:
                camera = client.simGetCameraInfo(camera_name, vehicle_name=vehicle_name).pose
                body_xyz, camera_xyz = position(body.position), position(camera.position)
                state.mount_position = rotate(inv_body, tuple(c-b for c, b in zip(camera_xyz, body_xyz)))
                state.mount_orientation = multiply(inv_body, quaternion(camera.orientation))
            mount_pitch, mount_yaw = pitch_yaw(state.mount_orientation)
            _, body_yaw = pitch_yaw(body_q)
            desired_world = from_euler(0.0, mount_pitch, body_yaw + mount_yaw)
            relative_q = multiply(inv_body, desired_world)
            # A concurrent disable is handled by restoration next capture.
            state.dirty = True
            client.simSetCameraPose(camera_name, pose(state.mount_position, relative_q),
                                    vehicle_name=vehicle_name)
            state.applied = state.enabled
        except Exception as exc:
            state.applied = False
            state.error = f"Camera {stage} failed: {type(exc).__name__}: {exc}"
