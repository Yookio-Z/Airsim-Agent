"""Tests for PersonActor: the teleport-drive controller behind person_* tools.

The fake client implements the three RPC calls the controller uses, so the tests
cover the motion model, the shortest-path turn, the stall detector and actor
discovery without a live simulator.
"""

from __future__ import annotations

import math
import threading
import time

import airsim
import pytest

from scripts.person_actor import PersonActor, PersonActorError


class FakePersonClient:
    """In-memory stand-in for AirSim's object-pose API."""

    def __init__(
        self,
        *,
        x: float = 0.0,
        y: float = 0.0,
        z: float = -0.09,
        yaw_deg: float = 0.0,
        objects: tuple[str, ...] = ("ThirdPersonCharacter_2", "TemplateCube_Rounded_1", "CameraActor_9"),
        accepts_writes: bool = True,
        follows_writes: bool = True,
    ) -> None:
        self.pose = airsim.Pose(
            airsim.Vector3r(x, y, z),
            airsim.to_quaternion(0.0, 0.0, math.radians(yaw_deg)),
        )
        self.objects = list(objects)
        self.accepts_writes = accepts_writes
        self.follows_writes = follows_writes
        self.writes = 0
        self._lock = threading.Lock()

    def simListSceneObjects(self, _regex: str) -> list[str]:
        return list(self.objects)

    def simGetObjectPose(self, name: str) -> airsim.Pose:
        if name not in self.objects:
            return airsim.Pose(airsim.Vector3r(float("nan"), float("nan"), float("nan")))
        with self._lock:
            return airsim.Pose(self.pose.position, self.pose.orientation)

    def simSetObjectPose(self, name: str, pose: airsim.Pose, teleport: bool = True) -> bool:
        if name not in self.objects or not self.accepts_writes:
            return False
        with self._lock:
            self.writes += 1
            if self.follows_writes:
                self.pose = airsim.Pose(pose.position, pose.orientation)
        return True

    @property
    def yaw_deg(self) -> float:
        with self._lock:
            return math.degrees(airsim.to_eularian_angles(self.pose.orientation)[2])


def _actor(client: FakePersonClient, **kwargs) -> PersonActor:
    actor = PersonActor(client=client, tick_hz=50.0, arrive_tolerance=0.25, blocked_ticks=3, **kwargs)
    actor.connect()
    return actor


def test_goto_walks_to_target_and_reports_arrival() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        assert actor.goto(2.0, 0.0, speed=2.0, timeout=5.0) is True
        state = actor.state(fresh=True)
    assert state.mode == "idle"
    assert state.x == pytest.approx(2.0, abs=0.3)
    assert state.y == pytest.approx(0.0, abs=0.3)
    assert state.blocked is False
    assert client.writes > 5  # a driven path, not one teleport


def test_goto_paces_the_actor_instead_of_snapping() -> None:
    """A 4 m walk at 2 m/s must take about 2 s, not one tick."""
    client = FakePersonClient()
    with _actor(client) as actor:
        started = time.monotonic()
        assert actor.goto(4.0, 0.0, speed=2.0, arrive=0.25, timeout=10.0) is True
        elapsed = time.monotonic() - started
    assert elapsed > 1.2, f"walk finished in {elapsed:.2f}s: the actor was teleported, not walked"


def test_move_body_follows_current_heading() -> None:
    """Facing east (yaw 90), "forward" must increase y, not x."""
    client = FakePersonClient(yaw_deg=90.0)
    with _actor(client) as actor:
        actor.move_body(1.6, 0.0)
        assert actor.wait(0.6) is True
        actor.stop()
        time.sleep(0.3)
        state = actor.state(fresh=True)
    assert state.y > 0.6, f"expected travel along +y, got y={state.y:.2f}"
    assert abs(state.x) < 0.25


def test_move_body_strafe_is_perpendicular() -> None:
    """Facing north, "right" must move east (+y)."""
    client = FakePersonClient(yaw_deg=0.0)
    with _actor(client) as actor:
        actor.move_body(0.0, 1.5, speed=1.5)
        assert actor.wait(0.6) is True
        actor.stop()
        time.sleep(0.3)
        state = actor.state(fresh=True)
    assert state.y > 0.5
    assert abs(state.x) < 0.25


def test_turn_to_takes_the_short_way_round() -> None:
    client = FakePersonClient(yaw_deg=170.0)
    with _actor(client) as actor:
        assert actor.turn_to(-170.0, rate=90.0) is True
        state = actor.state(fresh=True)
    assert abs(((state.yaw_deg + 180.0) % 360.0) - 10.0) < 6.0  # ≈ -170 or 190, not the long way


def test_velocity_duration_expires_and_stops() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        actor.move_velocity(1.0, 0.0, duration=0.4)
        time.sleep(0.9)
        state = actor.state(fresh=True)
        frozen = (state.x, state.y)
        time.sleep(0.4)
        later = actor.state(fresh=True)
    assert state.mode == "idle"
    assert math.hypot(later.x - frozen[0], later.y - frozen[1]) < 0.05


def test_stop_halts_a_running_walk() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        actor.move_velocity(2.0, 0.0)
        time.sleep(0.3)
        actor.stop()
        time.sleep(0.2)
        first = actor.state(fresh=True)
        time.sleep(0.4)
        second = actor.state(fresh=True)
    assert second.mode == "idle"
    assert math.hypot(second.x - first.x, second.y - first.y) < 0.05


def test_stalled_actor_is_reported_as_blocked() -> None:
    """Writes are accepted but the pose never moves (immovable / walled-in actor)."""
    client = FakePersonClient(follows_writes=False)
    with _actor(client) as actor:
        actor.blocked_ticks = 3
        assert actor.goto(3.0, 0.0, speed=1.5, timeout=4.0) is False
        state = actor.state(fresh=True)
    assert state.blocked is True


def test_write_rejection_is_reported() -> None:
    client = FakePersonClient(accepts_writes=False)
    with _actor(client) as actor:
        assert actor.goto(3.0, 0.0, timeout=3.0) is False
        state = actor.state(fresh=True)
    assert state.blocked is True
    assert "simSetObjectPose" in state.error


def test_unknown_name_falls_back_to_a_person_like_actor() -> None:
    client = FakePersonClient(objects=("TwinblastPlayerCharacter_3", "LightSource_1", "CameraActor_2"))
    with _actor(client) as actor:
        assert actor.name == "TwinblastPlayerCharacter_3"


def test_missing_person_raises_on_connect() -> None:
    client = FakePersonClient(objects=("Ground", "LightSource_1", "CameraActor_2"))
    actor = PersonActor(client=client)
    with pytest.raises(PersonActorError):
        actor.connect()


def test_set_pose_places_and_reorients() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        state = actor.set_pose(5.0, -2.0, yaw_deg=135.0)
    assert (state.x, state.y) == pytest.approx((5.0, -2.0), abs=0.01)
    assert (state.yaw_deg + 180.0) % 360.0 == pytest.approx(315.0, abs=0.5)


def test_patrol_walks_every_leg() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        legs = actor.patrol([(1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], speed=2.0, arrive=0.25)
        state = actor.state(fresh=True)
    assert legs == 3
    assert state.x == pytest.approx(0.0, abs=0.4)
    assert state.y == pytest.approx(1.0, abs=0.4)


def test_drive_walks_and_turns_at_the_same_time() -> None:
    """Teleop needs both axes at once: press "walk" and "turn right" together."""
    client = FakePersonClient()
    with _actor(client) as actor:
        actor.drive(forward=1.0, turn=90.0)
        assert actor.wait(0.8) is True
        actor.stop()
        time.sleep(0.2)
        state = actor.state(fresh=True)
    assert 50.0 < state.yaw_deg < 110.0, f"yaw did not track the turn rate: {state.yaw_deg:.1f}"
    assert state.x > 0.4, "walking stopped while turning"
    assert state.y > 0.2, "the path did not curve: turning is not steering the walk"


def test_drive_speed_rescales_diagonal() -> None:
    client = FakePersonClient()
    with _actor(client) as actor:
        actor.drive(forward=1.0, right=1.0, speed=2.0)
        assert actor.wait(0.7) is True
        actor.stop()
        time.sleep(0.2)
        state = actor.state(fresh=True)
    travelled = math.hypot(state.x, state.y)
    assert travelled == pytest.approx(2.0 * 0.7, abs=0.35), f"diagonal speed off: {travelled:.2f} m"


def test_acceleration_ramp_prevents_instant_start() -> None:
    """A 3 m/s command must spin up, not jump to speed: distance in 0.2 s stays small."""
    client = FakePersonClient()
    with _actor(client) as actor:
        assert actor.max_accel == pytest.approx(2.5)
        actor.move_velocity(3.0, 0.0)
        time.sleep(0.2)
        actor.stop()
        time.sleep(0.5)
        state = actor.state(fresh=True)
    # Instant velocity would travel 0.6 m; a 2.5 m/s^2 ramp manages ~0.05 m plus braking.
    assert state.x < 0.25, f"started too abruptly: {state.x:.2f} m in 0.2 s"


def test_stop_brakes_over_a_short_distance() -> None:
    """Stopping is a short deceleration, not a freeze: a frame of glide, then halt."""
    client = FakePersonClient()
    with _actor(client) as actor:
        actor.move_velocity(2.0, 0.0)
        time.sleep(1.0)  # reach speed
        moving = actor.state(fresh=True)
        actor.stop()
        time.sleep(0.25)
        braking = actor.state(fresh=True)
        time.sleep(0.5)
        halted = actor.state(fresh=True)
    glide = braking.x - moving.x
    assert 0.02 < glide < 0.9, f"unexpected braking distance {glide:.2f} m"
    assert halted.x - braking.x < 0.25, "the actor kept sliding after braking"
    assert halted.speed_mps < 0.05


def test_gait_does_not_steer_the_walk() -> None:
    """The cosmetic gait must not push the walk off target or drift it sideways."""
    plain = FakePersonClient()
    with _actor(plain) as actor:
        assert actor.goto(3.0, 0.0, speed=1.4, timeout=8.0) is True
        reference = actor.state(fresh=True)

    client = FakePersonClient()
    with _actor(client, cosmetic_walk=True) as actor:
        assert actor.goto(3.0, 0.0, speed=1.4, timeout=8.0) is True
        state = actor.state(fresh=True)

    assert abs(state.x - reference.x) < 0.25, "the gait pushed the walk off target"
    assert abs(state.y - reference.y) < 0.20, "the gait drifted sideways"


def test_gait_lift_stays_within_one_bob() -> None:
    """The written z must stay anchored to the ground: the bob may not ratchet upward."""
    client = FakePersonClient()
    written: list[float] = []
    with _actor(client, cosmetic_walk=True) as actor:
        original = actor._write_pose  # type: ignore[attr-defined]

        def spy(x, y, z, yaw_deg, **kwargs):
            written.append(z)
            return original(x, y, z, yaw_deg, **kwargs)

        actor._write_pose = spy  # type: ignore[method-assign]
        assert actor.goto(3.0, 0.0, speed=1.4, timeout=8.0) is True
        bob = actor.gait_bob

    assert written, "the walk wrote nothing"
    lowest, highest = min(written), max(written)
    assert lowest >= -0.09 - bob - 0.01, f"lift grew past one bob: {lowest:.3f}"
    assert highest <= -0.09 + 0.02, f"pushed the actor into the ground: {highest:.3f}"
    assert max(written) - min(written) > 0.01, "no bob at all: the gait never lifted"


def test_gait_adds_motion_while_walking() -> None:
    """With the gait on, the commanded track oscillates instead of being a straight rail."""
    client = FakePersonClient()
    samples: list[float] = []
    with _actor(client, cosmetic_walk=True) as actor:
        actor.move_velocity(1.4, 0.0)
        for _ in range(14):
            time.sleep(0.05)
            samples.append(actor.state().z)
        actor.stop()
    spread = max(samples) - min(samples)
    assert spread > 0.01, f"gait moved the body by only {spread * 100:.1f} cm"
