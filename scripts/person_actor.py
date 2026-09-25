"""Drive the scene's walking person over the AirSim object-pose API.

Standalone helper (not wired into the agent runtime): ``scripts/person_control.py``
is the CLI/keyboard entry point that uses this class. It only needs the airsim
package and the AirSim RPC port; it imports nothing from ``src`` unless the
project config happens to be importable, which is only used to pick a default port.

AirSim simulates *vehicles*; the person in the Blocks scene is an ordinary level
actor: no vehicle API, no controller, no navmesh. The object-pose API is the one
hook AirSim exposes for every actor, and it is enough to steer the character:

    simGetObjectPose(name) / simSetObjectPose(name, pose, teleport=True)

Measured against the running Blocks build (``ThirdPersonCharacter_2``, 2026-09-21):

* x/y/yaw writes stick, and the build accepted ~600 pose writes per second in a
  tight loop, so a 40 Hz drive loop with a read-back per tick has a wide margin;
* the character keeps its own ground contact (lift it and it settles back), so the
  controller commands x/y/yaw only and follows the actor's own z;
* a teleport write does not trigger locomotion animation: the actor slides in its
  idle pose. Measured 2026-09-21 - the AnimBP *is* alive (dropping the actor makes it
  play an airborne pose), but the Walk/Run states need a real movement-component
  velocity, which a teleport never produces. ``teleport=False`` behaves the same,
  ``simAddVehicle`` refuses a character pawn, and the game keyboard belongs to the
  AirSim HUD. So the gap is cosmetic here (``cosmetic_walk`` adds a step-linked
  bob/sway/lean) and structural for real foot animation: that needs a UE-side pawn
  driven through its movement component.

Frames follow AirSim's NED convention: x = north, y = east, z = down, yaw in
degrees clockwise from north. Body-frame helpers use forward/right, which is what
a "walk this way" instruction from a person-facing operator means.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import airsim

logger = logging.getLogger("person_actor")

DEFAULT_PERSON_NAME = "ThirdPersonCharacter_2"

# CameraActor / MenuActor / WeatherActor also match "actor", hence the reject list.
_PERSON_HINT = re.compile(r"thirdperson|person|human|mannequin|character|pedestrian|npc|soldier", re.I)
_PERSON_REJECT = re.compile(r"camera|menu|weather|sky|hud|light|playerstart|debugger", re.I)


class PersonActorError(RuntimeError):
    """Raised when the person actor cannot be reached or driven."""


@dataclass
class PersonState:
    """Last observed pose plus the controller's current command."""

    name: str = ""
    connected: bool = False
    x: float = float("nan")
    y: float = float("nan")
    z: float = float("nan")
    yaw_deg: float = float("nan")
    speed_mps: float = 0.0
    moving: bool = False
    mode: str = "idle"  # idle | velocity | goto | turn
    target: tuple[float, float] | None = None
    blocked: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        def num(value: float, digits: int = 3) -> float | None:
            return None if math.isnan(value) else round(value, digits)

        return {
            "name": self.name,
            "connected": self.connected,
            "x": num(self.x),
            "y": num(self.y),
            "z": num(self.z),
            "yaw_deg": num(self.yaw_deg, 1),
            "speed_mps": round(self.speed_mps, 3),
            "moving": self.moving,
            "mode": self.mode,
            "target": None if self.target is None else [round(self.target[0], 3), round(self.target[1], 3)],
            "blocked": self.blocked,
            "error": self.error,
        }


@dataclass
class _Command:
    """One command; the tick loop consumes this and nothing else."""

    mode: str = "idle"
    vx: float = 0.0
    vy: float = 0.0
    frame: str = "world"  # "body" re-derives the world vector from the live heading
    yaw_rate: float = 0.0  # deg/s, velocity mode only
    expires_at: float = 0.0  # 0.0 = run until replaced
    target_xy: tuple[float, float] | None = None
    target_yaw: float = 0.0
    speed: float = 1.4
    arrive: float = 0.35
    deadline: float = 0.0
    face_travel: bool = True
    seq: int = 0
    reason: str = ""


def _wrap_deg(value: float) -> float:
    """Normalise to (-180, 180]."""
    return ((float(value) + 180.0) % 360.0) - 180.0


def _yaw_diff(target_deg: float, current_deg: float) -> float:
    return _wrap_deg(float(target_deg) - float(current_deg))


def _slew(current: float, target: float, up_rate: float, down_rate: float, dt: float) -> float:
    """Move ``current`` toward ``target`` no faster than the given rate (units/s)."""
    delta = target - current
    rate = up_rate if abs(target) > abs(current) else down_rate
    return current + math.copysign(min(abs(delta), max(0.0, rate) * dt), delta)


def _heading_of(vec_x: float, vec_y: float) -> float:
    """Heading in degrees for a north/east vector."""
    return math.degrees(math.atan2(vec_y, vec_x))


class PersonActor:
    """Teleport-driven walker for one scene actor.

    One instance owns one actor and one tick thread (default 40 Hz). Public methods
    are safe to call from other threads; the blocking helpers (``goto``, ``walk``,
    ``turn_to``, ``patrol``, ``follow``) wait for the tick loop to finish the
    command they queued.

    Motion is deliberately not "command == instant velocity": the applied velocity
    ramps at ``max_accel``/``max_decel``, turns ramp at ``max_yaw_accel``, and
    ``cosmetic_walk`` adds a step-linked bob / hip sway / counter-rotation / lean.
    That is what removes the ice-skating feel of raw teleports. It does not articulate
    the legs - a teleport cannot produce the locomotion velocity the animation
    blueprint reads, so the character keeps its idle pose (see the module docstring).
    """

    def __init__(
        self,
        name: str = DEFAULT_PERSON_NAME,
        *,
        client: Any | None = None,
        ip: str = "127.0.0.1",
        port: int | None = None,
        tick_hz: float = 40.0,
        walk_speed: float = 1.4,
        run_speed: float = 3.6,
        turn_rate_dps: float = 120.0,
        arrive_tolerance: float = 0.35,
        cosmetic_walk: bool = False,
        max_accel: float = 2.5,
        max_decel: float = 4.0,
        max_yaw_accel: float = 300.0,
        stride_m: float = 0.75,
        gait_bob: float = 0.035,
        gait_sway: float = 0.045,
        gait_yaw: float = 3.5,
        gait_lean: float = 3.0,
        resync_tolerance: float = 0.3,
        blocked_ratio: float = 0.4,
        blocked_ticks: int = 4,
        auto_start: bool = False,
    ) -> None:
        self.name = name
        self.ip = ip
        self.port = port
        self.tick_hz = max(5.0, min(120.0, float(tick_hz)))
        self.walk_speed = float(walk_speed)
        self.run_speed = float(run_speed)
        self.turn_rate_dps = float(turn_rate_dps)
        self.arrive_tolerance = float(arrive_tolerance)
        self.cosmetic_walk = bool(cosmetic_walk)
        self.max_accel = max(0.05, float(max_accel))
        self.max_decel = max(0.05, float(max_decel))
        self.max_yaw_accel = max(1.0, float(max_yaw_accel))
        self.stride_m = max(0.1, float(stride_m))
        self.gait_bob = float(gait_bob)
        self.gait_sway = float(gait_sway)
        self.gait_yaw = float(gait_yaw)
        self.gait_lean = float(gait_lean)
        self.resync_tolerance = max(0.05, float(resync_tolerance))
        self.blocked_ratio = float(blocked_ratio)
        self.blocked_ticks = max(1, int(blocked_ticks))

        self._client = client
        self._owns_client = client is None
        # 串行化所有 RPC：tick 线程与调用方线程共用一个 msgpack-rpc 连接，
        # 并发发送会撞坏发送缓冲（BufferError: Existing exports of data）。
        self._rpc_lock = threading.Lock()
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._cmd = _Command(speed=self.walk_speed, arrive=self.arrive_tolerance)
        self._seq = 0
        self._completed_seq = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._x = float("nan")
        self._y = float("nan")
        self._z = float("nan")
        self._yaw = float("nan")
        self._speed = 0.0
        # 施加速度（带加减速斜坡）与偏航角速度：命令是目标值，这两个才是真正写到位姿上的量。
        self._av_x = 0.0
        self._av_y = 0.0
        self._a_yaw = 0.0
        # 名义轨迹：控制回路自己的积分轨道（不含步态扰动）。观测位姿只用来重新对齐，
        # 否则"为了好看"加上的摆动会被当作真实位置读回来，导致越走越偏。
        self._nx = float("nan")
        self._ny = float("nan")
        self._nyaw = float("nan")
        self._ground_z = float("nan")
        self._gait_travelled = 0.0  # 步态相位里程（米）：步频跟着它走
        self._gait_last_bob = 0.0   # 上一帧施加的抬升量（用于判断能否刷新地面基准）
        self._blocked = False
        self._blocked_count = 0
        self._error = ""
        self._error_source = ""

        if auto_start:
            self.connect()

    # ------------------------------------------------------------------ setup

    def _ensure_client(self) -> Any:
        """Create the RPC client on first use (no actor resolution, no tick loop)."""
        if self._client is None:
            if self.port is None:
                self.port = self._resolve_port()
            client = airsim.MultirotorClient(ip=self.ip, port=int(self.port), timeout_value=10)
            try:
                client.confirmConnection()
            except Exception as exc:  # pragma: no cover - needs a live simulator
                raise PersonActorError(f"AirSim RPC connect failed on {self.ip}:{self.port}: {exc}") from exc
            self._client = client
        return self._client

    def connect(self) -> PersonState:
        """Open the RPC client, resolve the actor name and start the tick loop."""
        self._ensure_client()

        if not self._actor_exists(self.name):
            candidates = self.list_candidates()
            if not candidates:
                raise PersonActorError(f"no person-like actor in the scene (looked for {self.name!r})")
            logger.warning(f"person actor {self.name!r} not found; using {candidates[0]!r}")
            self.name = candidates[0]

        self._read_pose()
        self._sync_nominal(self._x, self._y, self._yaw)
        self._start_tick()
        return self.state()

    @staticmethod
    def _resolve_port() -> int:
        """Default RPC port: the project config when importable, else AirSim's own."""
        try:
            from src.config import DroneConfig

            return int(DroneConfig().airsim_port)
        except Exception:
            return 41451

    def close(self) -> None:
        """Stop the tick loop and release the client."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        if self._owns_client and self._client is not None:
            try:
                close = getattr(self._client, "close", None)
                if callable(close):
                    with self._rpc_lock:
                        close()
            except Exception:
                pass
        self._client = None

    def __enter__(self) -> "PersonActor":
        self.connect()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -------------------------------------------------------------- discovery

    def list_candidates(self) -> list[str]:
        """Person-like actor names that report a finite pose."""
        try:
            with self._rpc_lock:
                names = list(self._ensure_client().simListSceneObjects(".*"))
        except Exception as exc:
            raise PersonActorError(f"simListSceneObjects failed: {exc}") from exc
        return [
            name
            for name in sorted(names)
            if _PERSON_HINT.search(name) and not _PERSON_REJECT.search(name) and self._actor_exists(name)
        ]

    def _actor_exists(self, name: str) -> bool:
        try:
            with self._rpc_lock:
                pose = self._require_client().simGetObjectPose(name)
        except Exception:
            return False
        position = getattr(pose, "position", None)
        return position is not None and all(
            math.isfinite(float(getattr(position, axis))) for axis in ("x_val", "y_val", "z_val")
        )

    def _require_client(self) -> Any:
        if self._client is None:
            raise PersonActorError("not connected: call connect() first")
        return self._client

    # ------------------------------------------------------------------ state

    def state(self, *, fresh: bool = False) -> PersonState:
        """Last observed state; ``fresh=True`` reads the pose straight from RPC."""
        if fresh:
            self._read_pose()
        with self._lock:
            return PersonState(
                name=self.name,
                connected=self._client is not None,
                x=self._x,
                y=self._y,
                z=self._z,
                yaw_deg=self._yaw,
                speed_mps=self._speed,
                moving=self._speed > 0.05 or self._cmd.mode in {"goto", "turn"},
                mode=self._cmd.mode,
                target=self._cmd.target_xy,
                blocked=self._blocked,
                error=self._error,
            )

    # ------------------------------------------------------ direct placement

    def set_pose(
        self,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        yaw_deg: float | None = None,
        *,
        teleport: bool = True,
    ) -> PersonState:
        """Place the actor immediately, cancelling any running command."""
        self.stop(reason="set_pose")
        if any(v is None and not math.isfinite(cur) for v, cur in ((x, self._x), (y, self._y), (z, self._z))):
            self._read_pose()
        with self._lock:
            targets = (
                self._x if x is None else float(x),
                self._y if y is None else float(y),
                self._z if z is None else float(z),
                self._yaw if yaw_deg is None else float(yaw_deg),
            )
        if not all(math.isfinite(v) for v in targets):
            raise PersonActorError(f"cannot place {self.name!r}: actor pose is not finite (missing actor?)")
        self._write_pose(*targets, teleport=teleport)
        self._read_pose()
        self._sync_nominal(self._x, self._y, self._yaw)
        if math.isfinite(self._z):
            self._ground_z = self._z
        return self.state()

    place = set_pose

    # --------------------------------------------------------------- commands

    def move_velocity(
        self, vx: float, vy: float, *, duration: float = 0.0, frame: str = "world"
    ) -> PersonState:
        """Drive at a world-frame NED velocity (m/s) for ``duration`` seconds.

        ``duration=0`` holds the velocity until the next command, which is what a
        keyboard teleop wants; a positive duration auto-stops afterwards.
        ``frame="body"`` reads the pair as (forward, right) in the actor's heading.
        """
        with self._lock:
            self._blocked = False
            self._blocked_count = 0
            self._queue_locked(
                _Command(
                    mode="velocity",
                    vx=float(vx),
                    vy=float(vy),
                    frame="body" if frame == "body" else "world",
                    expires_at=0.0 if duration <= 0 else time.monotonic() + float(duration),
                    reason="move_velocity",
                )
            )
        return self.state()

    def move_body(
        self,
        forward: float,
        right: float,
        *,
        duration: float = 0.0,
        speed: float | None = None,
    ) -> PersonState:
        """Body-frame velocity: +forward walks ahead, +right steps right.

        ``speed`` rescales the pair to a fixed magnitude, so
        ``move_body(1, 0.5, speed=2.0)`` walks diagonally at 2 m/s.
        """
        return self.drive(forward, right, speed=speed, frame="body", duration=duration)

    def drive(
        self,
        forward: float = 0.0,
        right: float = 0.0,
        turn: float = 0.0,
        *,
        speed: float | None = None,
        frame: str = "body",
        duration: float = 0.0,
    ) -> PersonState:
        """Teleop-style command: walk and turn at the same time.

        ``forward``/``right`` are m/s (body frame by default), ``turn`` is deg/s
        clockwise. ``speed`` optionally rescales the walk vector to a fixed
        magnitude while keeping its direction, so a keypad can hold "walk" and
        "turn" at once.
        """
        fwd, side = float(forward), float(right)
        if speed is not None:
            norm = math.hypot(fwd, side)
            if norm > 1e-6:
                fwd, side = fwd / norm * float(speed), side / norm * float(speed)
        with self._lock:
            self._blocked = False
            self._blocked_count = 0
            self._queue_locked(
                _Command(
                    mode="velocity",
                    vx=fwd,
                    vy=side,
                    frame="body" if frame == "body" else "world",
                    yaw_rate=float(turn),
                    expires_at=0.0 if duration <= 0 else time.monotonic() + float(duration),
                    reason="drive",
                )
            )
        return self.state()

    def stop(self, *, reason: str = "stop") -> PersonState:
        """Cancel the running command and hold position."""
        with self._lock:
            self._queue_locked(_Command(mode="idle", reason=reason))
        return self.state()

    def goto(
        self,
        x: float,
        y: float,
        *,
        speed: float | None = None,
        arrive: float | None = None,
        timeout: float | None = None,
        face_travel: bool = True,
    ) -> bool:
        """Walk to a world NED point; True on arrival.

        The actor eases out inside the arrival tolerance instead of snapping, so an
        approach reads as walking rather than a last-step teleport.
        """
        target = (float(x), float(y))
        speed = self.walk_speed if speed is None else max(0.05, float(speed))
        arrive = self.arrive_tolerance if arrive is None else max(0.05, float(arrive))
        with self._lock:
            distance = self._distance_to(*target)
            if timeout is None:
                timeout = distance / speed * 2.0 + 5.0
            self._blocked = False
            self._blocked_count = 0
            seq = self._queue_locked(
                _Command(
                    mode="goto",
                    target_xy=target,
                    speed=speed,
                    arrive=arrive,
                    deadline=time.monotonic() + float(timeout),
                    face_travel=bool(face_travel),
                    reason="goto",
                )
            )
        ok = self._wait_for(seq, float(timeout) + 0.5)
        if not ok and self._is_current(seq):
            self.stop(reason="goto_incomplete")
        return ok

    def walk(
        self,
        forward: float = 0.0,
        right: float = 0.0,
        *,
        speed: float | None = None,
        arrive: float | None = None,
    ) -> bool:
        """Walk a body-frame offset ("3 m ahead, 1 m right") and settle there."""
        nx, ny, nyaw = self._nominal()
        with self._lock:
            vx, vy = self._body_to_world(float(forward), float(right), nyaw)
        return self.goto(nx + vx, ny + vy, speed=speed, arrive=arrive)

    def turn_to(self, yaw_deg: float, *, rate: float | None = None, timeout: float | None = None) -> bool:
        """Rotate to a heading (shortest way round) and wait for it."""
        rate = self.turn_rate_dps if rate is None else max(1.0, float(rate))
        with self._lock:
            delta = abs(_yaw_diff(yaw_deg, self._yaw)) if math.isfinite(self._yaw) else 180.0
            if timeout is None:
                timeout = delta / rate * 1.5 + 2.0
            self._blocked = False
            self._blocked_count = 0
            seq = self._queue_locked(
                _Command(
                    mode="turn",
                    target_yaw=_wrap_deg(float(yaw_deg)),
                    speed=rate,
                    deadline=time.monotonic() + float(timeout),
                    reason="turn_to",
                )
            )
        ok = self._wait_for(seq, float(timeout) + 0.5)
        if not ok and self._is_current(seq):
            self.stop(reason="turn_incomplete")
        return ok

    def look_at(self, x: float, y: float, *, rate: float | None = None) -> bool:
        """Face a world NED point without moving."""
        nx, ny, _ = self._nominal()
        return self.turn_to(_heading_of(float(x) - nx, float(y) - ny), rate=rate)

    def patrol(
        self,
        points: list[tuple[float, float]] | list[list[float]],
        *,
        speed: float | None = None,
        loops: int = 1,
        arrive: float | None = None,
    ) -> int:
        """Walk a waypoint list; returns how many legs completed."""
        legs = [(float(point[0]), float(point[1])) for point in points]
        done = 0
        for _ in range(max(1, int(loops))):
            for x, y in legs:
                if self._stop_event.is_set():
                    return done
                if not self.goto(x, y, speed=speed, arrive=arrive):
                    return done
                done += 1
        return done

    def follow(
        self,
        target_fn: Callable[[], tuple[float, float]],
        *,
        speed: float | None = None,
        standoff: float = 1.5,
        duration: float = 10.0,
        period: float = 0.4,
    ) -> int:
        """Chase a moving point for ``duration`` seconds, holding ``standoff`` metres.

        ``target_fn`` is polled at 1/``period`` Hz: enough for a slow target, and it
        keeps the person visibly walking behind it without a closed-loop controller.
        """
        speed = self.walk_speed if speed is None else max(0.05, float(speed))
        deadline = time.monotonic() + float(duration)
        updates = 0
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                tx, ty = target_fn()
            except Exception as exc:
                logger.warning(f"follow target_fn failed: {exc}")
                break
            with self._lock:
                dx, dy = float(tx) - self._x, float(ty) - self._y
            distance = math.hypot(dx, dy)
            if distance > standoff:
                self.move_velocity(dx / distance * speed, dy / distance * speed)
                updates += 1
            else:
                self.move_velocity(0.0, 0.0, duration=0.3)
            time.sleep(max(0.05, float(period)))
        self.stop(reason="follow_done")
        return updates

    def wait(self, duration: float) -> bool:
        """Let the current command run for ``duration`` seconds.

        Returns True when it is still running at the end (a timed velocity command
        or a goto that has not arrived yet), False when it finished or was replaced.
        """
        seq = self._current_seq()
        deadline = time.monotonic() + max(0.0, float(duration))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        with self._lock:
            return self._seq == seq and self._cmd.mode != "idle"

    # --------------------------------------------------------------- tick loop

    def _start_tick(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_tick, name=f"person-actor-{self.name}", daemon=True)
        self._thread.start()

    def _run_tick(self) -> None:
        period = 1.0 / self.tick_hz
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._tick(period)
            except Exception as exc:  # keep the loop alive; state() carries the reason
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"person tick failed: {exc}")
                time.sleep(0.2)
                continue
            time.sleep(max(0.0, period - (time.monotonic() - started)))

    def _tick(self, period: float) -> None:
        now = time.monotonic()
        with self._lock:
            cmd = _Command(**vars(self._cmd))
            prev_xy = (self._x, self._y)
            obs_x, obs_y, obs_z, obs_yaw = self._x, self._y, self._z, self._yaw

        if self._stop_event.is_set():
            return

        # Re-anchor the nominal track when reality disagrees with it (physics push,
        # an external writer, the actor settling somewhere else).
        nx, ny, nyaw = self._nominal()
        if not all(math.isfinite(v) for v in (nx, ny, nyaw)) or (
            math.isfinite(obs_x)
            and math.hypot(nx - obs_x, ny - obs_y) > self.resync_tolerance
        ):
            self._sync_nominal(obs_x, obs_y, obs_yaw)
            nx, ny, nyaw = self._nominal()

        if cmd.mode == "idle":
            if abs(self._av_x) < 0.02 and abs(self._av_y) < 0.02 and abs(self._a_yaw) < 5.0:
                # Truly stopped: no writes, just keep the observed pose fresh.
                self._av_x = self._av_y = self._a_yaw = 0.0
                self._speed = 0.0
                self._read_pose()
                if math.isfinite(self._z):
                    self._ground_z = self._z
                    self._sync_nominal(self._x, self._y, self._yaw)
                return
            # Braking: keep writing while the applied velocity decays to zero.
            self._av_x = _slew(self._av_x, 0.0, self.max_decel, self.max_decel, period)
            self._av_y = _slew(self._av_y, 0.0, self.max_decel, self.max_decel, period)
            self._a_yaw = _slew(self._a_yaw, 0.0, self.max_yaw_accel, self.max_yaw_accel, period)
            self._speed = math.hypot(self._av_x, self._av_y)
            self._drive_track(nx, ny, nyaw, obs_z, self._av_x, self._av_y, self._a_yaw, period, 0.0)
            self._read_pose()
            return

        if not all(math.isfinite(v) for v in (obs_x, obs_y, obs_z, obs_yaw, nx, ny, nyaw)):
            self._read_pose()
            with self._lock:
                obs_x, obs_y, obs_z, obs_yaw = self._x, self._y, self._z, self._yaw
            if not all(math.isfinite(v) for v in (obs_x, obs_y, obs_z, obs_yaw)):
                self._complete(cmd, blocked=True, reason="pose_unavailable")
                return
            self._sync_nominal(obs_x, obs_y, obs_yaw)
            nx, ny, nyaw = self._nominal()

        if cmd.mode == "velocity" and cmd.expires_at and now >= cmd.expires_at:
            self._complete(cmd, blocked=False, reason="done")
            self._read_pose()
            return

        if cmd.deadline and now >= cmd.deadline:
            self._complete(cmd, blocked=True, reason="timeout")
            self._read_pose()
            return

        vx = vy = 0.0
        yaw_rate = 0.0

        if cmd.mode == "velocity":
            if cmd.frame == "body":
                vx, vy = self._body_to_world(cmd.vx, cmd.vy, nyaw)
            else:
                vx, vy = cmd.vx, cmd.vy
            yaw_rate = cmd.yaw_rate
        elif cmd.mode == "goto" and cmd.target_xy is not None:
            dx, dy = cmd.target_xy[0] - nx, cmd.target_xy[1] - ny
            distance = math.hypot(dx, dy)
            if distance <= cmd.arrive:
                self._complete(cmd, blocked=False, reason="arrived")
                self._read_pose()
                return
            # Ease out over the last 1.5 m so the final step does not pop.
            speed = cmd.speed * min(1.0, max(0.25, distance / 1.5))
            vx, vy = dx / distance * speed, dy / distance * speed
            if cmd.face_travel:
                yaw_rate = _wrap_deg(_heading_of(dx, dy) - nyaw) * 2.0
                yaw_rate = max(-self.turn_rate_dps, min(self.turn_rate_dps, yaw_rate))
        elif cmd.mode == "turn":
            delta = _yaw_diff(cmd.target_yaw, nyaw)
            if abs(delta) <= 2.0:
                self._complete(cmd, blocked=False, reason="turned")
                self._read_pose()
                return
            yaw_rate = math.copysign(min(abs(delta) * 2.0, self.turn_rate_dps), delta)

        # 命令速度不会立刻生效：按 max_accel/max_decel 斜坡逼近，去掉"瞬间起步/瞬间停住"。
        prev_av_x, prev_av_y = self._av_x, self._av_y
        self._av_x = _slew(self._av_x, vx, self.max_accel, self.max_decel, period)
        self._av_y = _slew(self._av_y, vy, self.max_accel, self.max_decel, period)
        vx, vy = self._av_x, self._av_y
        accel = math.hypot(self._av_x - prev_av_x, self._av_y - prev_av_y) / max(period, 1e-3)
        self._speed = math.hypot(vx, vy)
        self._a_yaw = _slew(self._a_yaw, yaw_rate, self.max_yaw_accel, self.max_yaw_accel, period)

        moved = self._drive_track(nx, ny, nyaw, obs_z, vx, vy, self._a_yaw, period, accel)
        if not moved:
            self._speed = 0.0
            self._complete(cmd, blocked=True, reason="write_rejected")
            return

        self._read_pose()
        if self._stalled(cmd, prev_xy, period):
            self._blocked_count += 1
            if self._blocked_count >= self.blocked_ticks:
                logger.warning(f"person {self.name} stalled at ({self._x:.2f}, {self._y:.2f})")
                self._speed = 0.0
                self._complete(cmd, blocked=True, reason="blocked")
        else:
            self._blocked_count = 0

        if cmd.mode == "goto" and cmd.target_xy is not None:
            nx2, ny2, _ = self._nominal()
            if math.hypot(cmd.target_xy[0] - nx2, cmd.target_xy[1] - ny2) <= cmd.arrive:
                self._speed = 0.0
                self._complete(cmd, blocked=False, reason="arrived")
        return

    def _drive_track(
        self,
        nx: float,
        ny: float,
        nyaw: float,
        obs_z: float,
        vx: float,
        vy: float,
        yaw_rate: float,
        period: float,
        accel: float,
    ) -> bool:
        """Advance the nominal track by one tick and write the perturbed pose.

        The nominal track is what control sees; the gait offsets (sway, bob, yaw
        waggle, lean) are added only on the way out to the simulator.
        """
        new_nx = nx + vx * period
        new_ny = ny + vy * period
        new_nyaw = nyaw + yaw_rate * period
        heading = _heading_of(vx, vy) if math.hypot(vx, vy) > 1e-6 else new_nyaw

        dx = dy = 0.0
        bob = 0.0
        dyaw = 0.0
        pitch = 0.0
        moving = self._speed > 0.05 or abs(yaw_rate) > 15.0
        if self.cosmetic_walk and moving:
            # 相位由实例持有：按走过的距离推进，等同"一步一次起伏"。
            self._gait_travelled += (self._speed + abs(yaw_rate) / 90.0 * self.stride_m) * period
            dx, dy, bob, dyaw, pitch = self._gait_offsets(self._gait_travelled, heading, accel)
        # 地面基准只在"上一帧完全没抬升"时刷新：那一帧写出去的就是纯地面高度，读回来
        # 没有被 bob 污染。真机里人物每帧会被重力拽回地面，所以刷新同样成立，不会累加。
        if math.isfinite(obs_z) and (not math.isfinite(self._ground_z) or self._gait_last_bob <= 0.0):
            self._ground_z = obs_z
        self._gait_last_bob = bob
        z = self._ground_z - bob if math.isfinite(self._ground_z) else obs_z

        ok = self._write_pose(new_nx + dx, new_ny + dy, z, new_nyaw + dyaw, pitch_deg=pitch)
        if ok:
            self._sync_nominal(new_nx, new_ny, new_nyaw)
        return ok

    def _nominal(self) -> tuple[float, float, float]:
        with self._lock:
            return self._nx, self._ny, self._nyaw

    def _sync_nominal(self, x: float, y: float, yaw: float) -> None:
        with self._lock:
            if math.isfinite(x) and math.isfinite(y) and math.isfinite(yaw):
                self._nx, self._ny, self._nyaw = float(x), float(y), float(yaw)

    def _stalled(self, cmd: _Command, prev_xy: tuple[float, float], period: float) -> bool:
        """True when the actor moved far less than the command asked for.

        Teleport writes always report success, so the only honest signal that the
        actor is stuck (wall, ledge, mobility lock) is the pose not changing.
        """
        if cmd.mode == "turn":
            return False
        expected_step = self._speed * period  # self._speed is the commanded magnitude
        if expected_step < 0.01:
            return False
        with self._lock:
            if not (math.isfinite(self._x) and math.isfinite(self._y) and math.isfinite(prev_xy[0])):
                return False
            actual_step = math.hypot(self._x - prev_xy[0], self._y - prev_xy[1])
        return actual_step < self.blocked_ratio * expected_step

    def _gait_offsets(
        self, travelled_m: float, heading_deg: float, accel: float
    ) -> tuple[float, float, float, float, float]:
        """Offsets added on top of the nominal track: (dx, dy, bob, dyaw, pitch).

        One bob per stride, hip sway along the body's right axis, a small yaw
        counter-rotation, and a forward lean proportional to acceleration. Purely
        cosmetic - it is what stops a teleported actor from reading as a statue on a
        conveyor belt. It cannot move the legs: that needs real locomotion velocity
        from the movement component (UE-side change).
        """
        cycles = travelled_m / self.stride_m
        phase = math.pi * cycles
        swing = math.sin(phase)
        # 过零点附近把抬升直接归零：给地面基准留出"干净"的采样帧（见 _drive_track）。
        bob = self.gait_bob * abs(swing) if abs(swing) > 0.05 else 0.0
        sway = self.gait_sway * swing
        psi = math.radians(heading_deg)
        lean = 0.0
        if self.gait_lean > 0.0:
            accel_ratio = max(-1.0, min(1.0, accel / max(1e-3, self.max_accel)))
            lean = -self.gait_lean * accel_ratio
        return -math.sin(psi) * sway, math.cos(psi) * sway, bob, self.gait_yaw * swing, lean

    def _queue_locked(self, cmd: _Command) -> int:
        """Replace the running command. Caller holds the lock."""
        self._seq += 1
        cmd.seq = self._seq
        self._cmd = cmd
        if cmd.mode == "idle":
            # Cancelling releases anyone waiting on the replaced command.
            self._speed = 0.0
            self._completed_seq = self._seq
        self._cond.notify_all()
        return self._seq

    def _complete(self, cmd: _Command, *, blocked: bool, reason: str = "") -> None:
        with self._lock:
            if self._cmd.seq == cmd.seq:
                self._cmd = _Command(mode="idle", speed=self.walk_speed, arrive=self.arrive_tolerance, reason=reason)
            self._blocked = bool(blocked)
            self._blocked_count = 0
            self._completed_seq = max(self._completed_seq, cmd.seq)
            self._cond.notify_all()

    def _current_seq(self) -> int:
        with self._lock:
            return self._seq

    def _is_current(self, seq: int) -> bool:
        with self._lock:
            return self._seq == seq

    def _wait_for(self, seq: int, timeout: float) -> bool:
        """Wait for the tick loop to finish command ``seq``.

        True means it finished on its own (arrived / turned); False means the wait
        expired or a newer command replaced it.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            while self._completed_seq < seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(0.2, remaining))
            return self._seq == seq and not self._blocked

    def _distance_to(self, x: float, y: float) -> float:
        nx, ny, _ = self._nominal()
        if not (math.isfinite(nx) and math.isfinite(ny)):
            return 0.0
        return math.hypot(float(x) - nx, float(y) - ny)

    def _body_to_world(self, forward: float, right: float, yaw: float | None = None) -> tuple[float, float]:
        """Body-frame (forward, right) -> world NED (vx, vy). Defaults to the live heading."""
        if yaw is None:
            yaw = self._yaw if math.isfinite(self._yaw) else 0.0
        psi = math.radians(yaw)
        return (
            forward * math.cos(psi) - right * math.sin(psi),
            forward * math.sin(psi) + right * math.cos(psi),
        )

    # -------------------------------------------------------------- RPC bridge

    def _read_pose(self) -> PersonState:
        with self._rpc_lock:
            pose = self._require_client().simGetObjectPose(self.name)
        position = pose.position
        x, y, z = float(position.x_val), float(position.y_val), float(position.z_val)
        if not all(math.isfinite(v) for v in (x, y, z)):
            with self._lock:
                self._error = f"pose of {self.name!r} is not finite (actor missing?)"
                self._error_source = "read"
            return self.state()
        yaw = math.degrees(airsim.to_eularian_angles(pose.orientation)[2])
        with self._lock:
            self._x, self._y, self._z, self._yaw = x, y, z, yaw
            if self._error_source == "read":
                self._error, self._error_source = "", ""
        return self.state()

    def _write_pose(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float,
        *,
        pitch_deg: float = 0.0,
        teleport: bool = True,
    ) -> bool:
        pose = airsim.Pose(
            airsim.Vector3r(float(x), float(y), float(z)),
            airsim.to_quaternion(math.radians(float(pitch_deg)), 0.0, math.radians(float(yaw_deg))),
        )
        try:
            with self._rpc_lock:
                ok = bool(self._require_client().simSetObjectPose(self.name, pose, bool(teleport)))
        except Exception as exc:
            with self._lock:
                self._error = f"simSetObjectPose failed: {exc}"
                self._error_source = "write"
            return False
        with self._lock:
            if ok:
                if self._error_source in {"", "write"}:
                    self._error, self._error_source = "", ""
            else:
                self._error = f"simSetObjectPose rejected for {self.name!r} (actor must be movable)"
                self._error_source = "write"
        return ok
