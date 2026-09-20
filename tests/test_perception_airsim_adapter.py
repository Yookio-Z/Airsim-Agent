"""AirSim perception adapter tests.

These cover the depth-projection chain that silently returned nothing before:
the frame source must hand its controller to the adapter, the depth request
must ask for float pixels, and the engine must read the projected key names.

No AirSim simulator or network is needed -- the RPC surface is faked.
"""

from __future__ import annotations

import numpy as np

from src.modules.perception_airsim_adapter import build_depth_fn, build_frame_source, get_depth_image


# ----------------------------------------------------------------------
# 桩：AirSim RPC 表面
# ----------------------------------------------------------------------

class _FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x_val = x
        self.y_val = y
        self.z_val = z


class _FakeQuat:
    """Identity attitude: yaw = 0."""

    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0) -> None:
        self.w_val = w
        self.x_val = x
        self.y_val = y
        self.z_val = z


class _FakeKinematics:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.position = _FakeVector(x, y, z)
        self.orientation = _FakeQuat()


class _FakeState:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.kinematics_estimated = _FakeKinematics(x, y, z)


class _FakeImageResponse:
    def __init__(self, data_float, width, height) -> None:
        self.image_data_float = data_float
        self.width = width
        self.height = height


class _FakeClient:
    """Records the ImageRequest so tests can assert the pixel format asked for."""

    def __init__(self, response=None, state=None, fail_float=False) -> None:
        self.response = response
        self.state = state or _FakeState()
        self.fail_float = fail_float
        self.requests_seen: list = []

    def simGetImages(self, requests, vehicle_name="", external=False):
        self.requests_seen.extend(requests)
        if self.fail_float:
            # What AirSim returns when pixels_as_float was requested as False.
            return [_FakeImageResponse(0.0, 0, 0)]
        return [self.response] if self.response is not None else []

    def getMultirotorState(self, vehicle_name=""):
        return self.state


class _FakeController:
    """Minimal AirSimController surface: _client plus a timeout-honouring _rpc."""

    def __init__(self, client=None) -> None:
        self._client = client
        self.rpc_calls: list[str] = []

    def _rpc(self, fn, *args, timeout=10.0, **kwargs):
        self.rpc_calls.append(getattr(fn, "__name__", "?"))
        return fn(*args, **kwargs)


def _depth_response(value=5.0, width=64, height=48):
    """Uniform depth field, so the median sample equals ``value``."""
    return _FakeImageResponse([value] * (width * height), width, height)


# ----------------------------------------------------------------------
# build_frame_source
# ----------------------------------------------------------------------

def test_frame_source_prefers_the_flight_controller_capture_path():
    """A provider must win: the controller's capture has a hard timeout."""
    from src.modules.frame_source import LazyControllerFrameSource

    source = build_frame_source(frame_provider=lambda: object(), ip="127.0.0.1", port=41451)
    assert isinstance(source, LazyControllerFrameSource)


def test_frame_source_without_provider_requires_a_reachable_simulator():
    """No provider means an own client, which must fail fast when nothing listens."""
    try:
        source = build_frame_source(frame_provider=None, ip="127.0.0.1", port=9, timeout_sec=0.2)
    except OSError:
        return  # expected: fast TCP probe rejected the missing simulator
    assert source is None


# ----------------------------------------------------------------------
# get_depth_image：请求格式与解析
# ----------------------------------------------------------------------

def test_depth_request_asks_for_float_pixels():
    """Regression: asking for uint8 while reading image_data_float yields nothing."""
    client = _FakeClient(response=_depth_response())
    image = get_depth_image(_FakeController(client))

    assert image is not None
    assert len(client.requests_seen) == 1
    assert client.requests_seen[0].pixels_as_float is True
    assert client.requests_seen[0].compress is False


def test_depth_image_is_reshaped_to_height_by_width():
    client = _FakeClient(response=_depth_response(value=7.5, width=64, height=48))
    image = get_depth_image(_FakeController(client))

    assert image.shape == (48, 64)
    assert float(np.median(image)) == 7.5


def test_depth_image_returns_none_when_float_payload_is_absent():
    """The old code path returned this on every call; it must stay a clean None."""
    client = _FakeClient(fail_float=True)
    assert get_depth_image(_FakeController(client)) is None


def test_depth_image_returns_none_without_a_client():
    assert get_depth_image(_FakeController(client=None)) is None
    assert get_depth_image(None) is None


# ----------------------------------------------------------------------
# build_depth_fn：接线与投影
# ----------------------------------------------------------------------

def test_depth_fn_needs_a_provider_but_not_an_eagerly_ready_controller():
    """Only a missing provider disables depth.

    The axis is built before the camera controller finishes connecting, so a
    readiness check at build time would permanently disable depth even after the
    controller came up. A provider that currently yields no controller must
    still produce a callable, which simply returns None until the controller
    appears.
    """
    assert build_depth_fn(frame_provider=None) is None

    for provider in (lambda: None, _FakeController(client=None)):
        depth_fn = build_depth_fn(frame_provider=provider)
        assert depth_fn is not None, "depth must stay wired until the factory is absent"
        assert depth_fn(np.zeros((48, 64, 3), dtype=np.uint8), {"bbox": [28, 20, 36, 28]}) is None


def test_depth_fn_recovers_once_the_controller_connects():
    """The reported bug: depth stayed dead because it was judged at build time."""
    state = {"controller": None, "client": None}

    def provider():
        return state["controller"]

    depth_fn = build_depth_fn(frame_provider=provider)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    assert depth_fn(frame, {"bbox": [28, 20, 36, 28]}) is None

    # Controller arrives later (lazy connect on the first capture).
    state["controller"] = _FakeController(_FakeClient(response=_depth_response(value=6.0)))
    world = depth_fn(frame, {"bbox": [28, 20, 36, 28]})

    assert world is not None
    assert world["valid"] is True
    assert world["depth_meters"] == 6.0


def test_depth_fn_projects_a_detection_to_a_world_position():
    controller = _FakeController(_FakeClient(response=_depth_response(value=6.0), state=_FakeState(10.0, 20.0, -3.0)))
    depth_fn = build_depth_fn(frame_provider=lambda: controller)

    assert depth_fn is not None
    world = depth_fn(np.zeros((48, 64, 3), dtype=np.uint8), {"bbox": [28, 20, 36, 28]})

    assert world is not None
    assert world["valid"] is True
    # The engine reads "depth_meters"; projection must keep emitting that key.
    assert world["depth_meters"] == 6.0
    assert world["distance_to_drone"] == 6.0
    assert set(world["world_pos"]) == {"x", "y", "z"}


def test_depth_fn_uses_the_controller_rpc_path():
    """Both RPC calls must go through _rpc so they inherit the hard timeout."""
    controller = _FakeController(_FakeClient(response=_depth_response()))
    depth_fn = build_depth_fn(frame_provider=lambda: controller)
    depth_fn(np.zeros((48, 64, 3), dtype=np.uint8), {"bbox": [28, 20, 36, 28]})

    assert "simGetImages" in controller.rpc_calls
    assert "getMultirotorState" in controller.rpc_calls


def test_depth_fn_degrades_to_none_on_missing_inputs():
    controller = _FakeController(_FakeClient(response=_depth_response()))
    depth_fn = build_depth_fn(frame_provider=lambda: controller)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    assert depth_fn(frame, {}) is None
    assert depth_fn(frame, {"bbox": None}) is None
    assert depth_fn(frame, {"bbox": [0, 0, 0, 0]})["valid"] is False


def test_depth_fn_resolves_the_provider_per_call():
    """The runtime rebuilds the camera client after an AirSim restart."""
    holder = {"controller": _FakeController(_FakeClient(response=_depth_response(value=4.0)))}
    depth_fn = build_depth_fn(frame_provider=lambda: holder["controller"])

    first = depth_fn(np.zeros((48, 64, 3), dtype=np.uint8), {"bbox": [28, 20, 36, 28]})
    assert first["depth_meters"] == 4.0

    holder["controller"] = _FakeController(_FakeClient(response=_depth_response(value=9.0)))
    second = depth_fn(np.zeros((48, 64, 3), dtype=np.uint8), {"bbox": [28, 20, 36, 28]})
    assert second["depth_meters"] == 9.0


# ----------------------------------------------------------------------
# 可剥离性：感知核心不得牵连 AirSim
# ----------------------------------------------------------------------

def test_perception_core_imports_without_airsim():
    """The detection/tracking core must stay free of the simulator.

    Checked in a subprocess because another test in the same session may
    already have imported airsim, which would mask the leak.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import src.modules.yolo_detection\n"
        "import src.modules.target_tracker\n"
        "import src.modules.perception_profile\n"
        "import src.modules.perception_axis\n"
        "leaked = sorted(m for m in sys.modules if m == 'airsim' or m.startswith('airsim.')"
        " or 'airsim_controller' in m)\n"
        "print('LEAKED:' + ','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr
    assert "LEAKED:" in result.stdout, result.stdout
    leaked = result.stdout.split("LEAKED:", 1)[1].strip()
    assert leaked == "", f"perception core dragged in AirSim modules: {leaked}"


# ----------------------------------------------------------------------
# bbox 缩放：彩色帧与深度图分辨率不同
# ----------------------------------------------------------------------

def test_bbox_maps_through_angle_not_height_ratio():
    """The two images differ in size AND in vertical coverage.

    FOV_Degrees is the horizontal FOV, so a 4:3 colour frame sees ~73.7 deg
    vertically while a 16:9 depth frame sees ~58.7 deg. Linear y scaling by the
    height ratio would sample the wrong depth row off-centre, so y is mapped
    through the actual angle. x stays linear because fov_h is shared.
    """
    from src.modules.perception_airsim_adapter import _scale_bbox_to_depth

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    depth = np.zeros((144, 256), dtype=np.float32)

    scaled = _scale_bbox_to_depth([337, 140, 364, 158], frame, depth, fov_h=90.0)

    assert scaled == [135, 32, 146, 39], scaled
    assert scaled[2] > scaled[0] and scaled[3] > scaled[1], "box must not collapse"
    # The naive linear mapping would have produced y=42/47; angle mapping puts the
    # box higher because the depth frame covers less vertical field.
    linear_y = round(140 * 144 / 480)
    assert scaled[1] != linear_y, "linear height scaling must not be used"


def test_bbox_mapping_is_linear_when_aspects_match():
    """Same aspect ratio -> same vertical FOV -> plain linear scaling is correct."""
    from src.modules.perception_airsim_adapter import _scale_bbox_to_depth

    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    depth = np.zeros((192, 256), dtype=np.float32)  # both 4:3

    scaled = _scale_bbox_to_depth([400, 300, 480, 360], frame, depth, fov_h=90.0)

    assert scaled == [128, 96, 154, 115], scaled


def test_bbox_scaling_is_a_noop_at_equal_resolution():
    from src.modules.perception_airsim_adapter import _scale_bbox_to_depth

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    depth = np.zeros((480, 640), dtype=np.float32)

    assert _scale_bbox_to_depth([10, 20, 30, 40], frame, depth) == [10, 20, 30, 40]


def test_bbox_scaling_tolerates_bad_input():
    from src.modules.perception_airsim_adapter import _scale_bbox_to_depth

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    depth = np.zeros((144, 256), dtype=np.float32)

    assert _scale_bbox_to_depth([], frame, depth) == []
    assert _scale_bbox_to_depth([1, 2, 3], frame, depth) == []
    assert _scale_bbox_to_depth(["a", "b", "c", "d"], frame, depth) == []


def test_depth_fn_projects_across_mismatched_resolutions():
    """End-to-end: colour 480x640 detection, depth 144x256."""
    client = _FakeClient(response=_depth_response(value=6.0, width=256, height=144),
                         state=_FakeState(10.0, 20.0, -3.0))
    controller = _FakeController(client)
    depth_fn = build_depth_fn(frame_provider=lambda: controller)

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    world = depth_fn(frame, {"bbox": [337, 140, 364, 158]})

    assert world is not None
    assert world["valid"] is True, "mismatched depth resolution must not collapse the box"
    assert world["depth_meters"] == 6.0


# ----------------------------------------------------------------------
# FOV：垂直视场必须由水平视场与宽高比推出，而不是写死
# ----------------------------------------------------------------------

def test_vertical_fov_follows_aspect_ratio_not_a_constant():
    """The projection used a hardcoded fov_v=60, which is wrong for every aspect.

    For 16:9 at fov_h=90 the true vertical FOV is ~58.7 deg; for 4:3 it is
    ~73.7 deg. A constant cannot be right for both, and the error grows with the
    off-axis angle -- exactly where a target sits while being approached.
    """
    from src.modules.perception_airsim_adapter import _derive_fov_v

    four_three = _derive_fov_v(90.0, 640, 480)
    sixteen_nine = _derive_fov_v(90.0, 1280, 720)

    assert abs(four_three - 73.7) < 0.5, four_three
    assert abs(sixteen_nine - 58.7) < 0.5, sixteen_nine
    assert four_three != sixteen_nine, "a single constant cannot cover both aspects"
    # The old hardcoded 60 was wrong for 4:3 by more than 13 degrees.
    assert abs(four_three - 60.0) > 10.0


def test_vertical_fov_is_robust_to_bad_input():
    from src.modules.perception_airsim_adapter import _derive_fov_v

    assert _derive_fov_v(90.0, 0, 0) == 90.0
    assert _derive_fov_v(90.0, -5, 480) == 90.0
    # Clamped rather than producing a nonsensical angle.
    assert 0.0 < _derive_fov_v(179.0, 640, 480) <= 179.0
