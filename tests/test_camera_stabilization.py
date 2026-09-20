"""Offline fake-RPC tests; never connect to or mutate a running simulator."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import math
import threading

import airsim
import numpy as np
import pytest

from src.modules.airsim_controller import AirSimController
from src.modules.camera_stabilization import CameraStabilization


def matrix(roll, pitch, yaw):
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    return np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ np.array(
        [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]) @ np.array(
        [[1, 0, 0], [0, cr, -sr], [0, sr, cr]])


def quat_matrix(q):
    x, y, z, w = q.x_val, q.y_val, q.z_val, q.w_val
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def xyz(v):
    return np.array([v.x_val, v.y_val, v.z_val])


class FakeClient:
    def __init__(self):
        self.calls = []
        self.body = airsim.Pose(airsim.Vector3r(3, 5, -2), airsim.to_quaternion(.3, -.2, 1.2))
        self.mount = airsim.Pose(airsim.Vector3r(.4, -.1, -1), airsim.to_quaternion(-.26, .12, .4))
        self.current = self.mount
        self.fail = None

    def record(self, method, camera=None, vehicle=""):
        self.calls.append((method, camera, vehicle, threading.get_ident()))
        if self.fail == method:
            raise RuntimeError("unsupported")

    def simIsPause(self):
        self.record("pause")
        return False

    def simGetVehiclePose(self, vehicle_name=""):
        self.record("body", vehicle=vehicle_name)
        return self.body

    def simGetCameraInfo(self, camera_name, vehicle_name=""):
        self.record("info", camera_name, vehicle_name)
        p = xyz(self.body.position) + quat_matrix(self.body.orientation) @ xyz(self.current.position)
        return SimpleNamespace(pose=airsim.Pose(airsim.Vector3r(*p),
                               self.body.orientation * self.current.orientation))

    def simSetCameraPose(self, camera_name, pose, vehicle_name=""):
        self.record("set", camera_name, vehicle_name)
        self.current = pose

    def simGetImages(self, requests, vehicle_name=""):
        self.record("images", requests[0].camera_name, vehicle_name)
        return [SimpleNamespace(width=1, height=1, image_data_uint8=b"\x01\x02\x03")]


@pytest.fixture
def controller():
    c = AirSimController.__new__(AirSimController)
    c._client = FakeClient()
    c._executor = ThreadPoolExecutor(max_workers=1)
    c._rpc_exec_lock = threading.Lock()
    c._rpc_timing = threading.local()
    c._ensure_connected = lambda: True
    yield c
    c._executor.shutdown(wait=True)


def names(client):
    return [call[0] for call in client.calls]


def test_default_disabled_no_camera_rpcs(controller):
    c = controller
    c.configure_camera_stabilization("CameraImage", False)
    assert c.camera_stabilization_status("CameraImage") == dict(enabled=False, applied=False, error="")
    assert c.capture_image("CameraImage") == b"\x01\x02\x03"
    assert names(c._client) == ["pause", "images"]


def test_compound_mount_and_attitudes_preserve_position_and_world_target(controller):
    c = controller
    c.configure_camera_stabilization("CameraImage", vehicle_name="drone")
    assert c.camera_stabilization_status("CameraImage", "drone")["enabled"]
    assert c._client.calls == []
    c.capture_frame("CameraImage", vehicle_name="drone")
    assert names(c._client) == ["pause", "body", "info", "set", "images"]
    assert len({call[3] for call in c._client.calls}) == 1
    assert c._client.calls[0][3] != threading.get_ident()
    # Independent rotation matrices catch Euler-subtraction and order mistakes.
    for roll, pitch, yaw in [(-.6, .5, -2.9), (.7, -.4, 3.1), (.1, .2, -.1)]:
        c._client.body.orientation = airsim.to_quaternion(pitch, roll, yaw)
        c.configure_camera_stabilization("CameraImage", vehicle_name="drone")
        c.capture_frame("CameraImage", vehicle_name="drone")
        actual = matrix(roll, pitch, yaw) @ quat_matrix(c._client.current.orientation)
        np.testing.assert_allclose(actual, matrix(0, -.26, yaw+.4), atol=1e-12)
        np.testing.assert_allclose(xyz(c._client.current.position), [.4, -.1, -1], atol=1e-12)
    assert names(c._client).count("info") == 1
    assert c.camera_stabilization_status("CameraImage", "drone") == dict(enabled=True, applied=True, error="")


def test_only_exact_configured_camera_vehicle_changes(controller):
    c = controller
    c.configure_camera_stabilization("CameraImage", vehicle_name="drone")
    c.capture_image("other", vehicle_name="drone")
    c.capture_image("CameraImage", vehicle_name="other")
    assert names(c._client) == ["pause", "images", "pause", "images"]


def test_disable_restores_original_once_and_reenable_keeps_original(controller):
    c = controller
    original = c._client.mount
    c.configure_camera_stabilization("CameraImage")
    c.capture_image("CameraImage")
    c.configure_camera_stabilization("CameraImage", False)
    c.capture_image("CameraImage")
    np.testing.assert_allclose(quat_matrix(c._client.current.orientation), quat_matrix(original.orientation))
    np.testing.assert_allclose(xyz(c._client.current.position), xyz(original.position))
    sets = names(c._client).count("set")
    c.configure_camera_stabilization("CameraImage", False)
    c.capture_image("CameraImage")
    assert names(c._client).count("set") == sets
    c.configure_camera_stabilization("CameraImage")
    c.capture_image("CameraImage")
    assert names(c._client).count("info") == 1


@pytest.mark.parametrize("failure", ["body", "info", "set"])
def test_failure_latches_without_spam_capture_still_succeeds(controller, failure):
    c = controller
    c.configure_camera_stabilization("CameraImage")
    c._client.fail = failure
    for _ in range(3):
        c.configure_camera_stabilization("CameraImage")
        assert c.capture_image("CameraImage")
    assert names(c._client).count(failure) == 1
    status = c.camera_stabilization_status("CameraImage")
    assert status["enabled"] and not status["applied"]
    assert "stabilization failed" in status["error"]
    c._client.fail = None
    c.configure_camera_stabilization("CameraImage", False)
    c.configure_camera_stabilization("CameraImage", True)
    c.capture_image("CameraImage")
    assert c.camera_stabilization_status("CameraImage")["applied"]


def test_restore_failure_is_bounded_and_visible(controller):
    c = controller
    c.configure_camera_stabilization("CameraImage")
    c.capture_image("CameraImage")
    c.configure_camera_stabilization("CameraImage", False)
    c._client.fail = "set"
    for _ in range(3):
        c.configure_camera_stabilization("CameraImage", False)
        c.capture_image("CameraImage")
    assert names(c._client).count("set") == 2
    status = c.camera_stabilization_status("CameraImage")
    assert not status["enabled"] and not status["applied"]
    assert "mount restoration failed" in status["error"]


def test_runtime_reset_detaches_stale_state_and_preserves_original_mount(controller):
    c = controller
    c.configure_camera_stabilization("CameraImage")
    c.capture_image("CameraImage")
    client, old = c._client, c._camera_stabilization
    c._reset_rpc_runtime()
    assert c._camera_stabilization is not old
    assert not c.camera_stabilization_status("CameraImage")["applied"]
    c._client = client
    c.configure_camera_stabilization("CameraImage", False)
    c.capture_image("CameraImage")
    np.testing.assert_allclose(quat_matrix(client.current.orientation), quat_matrix(client.mount.orientation))
    assert names(client).count("info") == 1
    old._states[("CameraImage", "")].error = "late worker"
    assert c.camera_stabilization_status("CameraImage")["error"] == ""


def test_explicit_simulator_reset_clears_calibration_not_enabled_choice():
    s = CameraStabilization()
    s.configure("CameraImage")
    client = FakeClient()
    s.before_capture(client, "CameraImage")
    fresh = s.reset_runtime(simulator_reset=True)
    assert fresh.status("CameraImage") == dict(enabled=True, applied=False, error="")
    assert fresh._states[("CameraImage", "")].mount_orientation is None
