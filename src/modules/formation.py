"""Deterministic multi-vehicle formation and coverage control (AirSim /
PX4 MAVLink backends).

Architecture (validated by openclaw-swarm): the LLM issues one high-level
intent per turn (formation_command tool call); a background control thread at
~10Hz closes the velocity loop per vehicle. The LLM never sees per-tick state.

Safety properties:
  * velocity commands carry a short duration (0.2s) and are re-issued every
    tick — if the thread dies or stops, AirSim auto-hovers the vehicles;
  * a ``should_stop`` callback (emergency stop / cancel) turns the loop into
    hover_all + idle immediately;
  * N consecutive tick errors stop the loop automatically;
  * flight actions are sequential per vehicle and report partial failures.

The pure functions (formation_offsets, plan_coverage) are side-effect free and
unit-testable without any backend.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

FORMATION_TYPES = ("line", "v_shape", "triangle", "diamond", "square", "hexagon", "circle", "arrow")

PARTITION_ALGORITHMS = ("balanced", "stripe", "quadrant")
PATH_ALGORITHMS = ("boustrophedon", "spiral", "nearest")

FLIGHT_ACTIONS = ("takeoff", "move_center", "rotate", "scale", "land_all", "coverage_start")


# ---------------------------------------------------------------------------
# Pure functions: formation geometry
# ---------------------------------------------------------------------------


def formation_offsets(formation_type: str, count: int, spacing: float = 5.0) -> list[dict[str, float]]:
    """Per-drone offsets (NED, z=0) for a virtual-structure formation.

    All formations are centered on the origin; the control loop adds the
    formation center to each offset to get the per-drone target.
    """
    count = max(1, int(count))
    spacing = max(1.0, float(spacing))
    n = count
    pts: list[tuple[float, float]] = []

    if formation_type == "line":
        pts = [((i - (n - 1) / 2.0) * spacing, 0.0) for i in range(n)]
    elif formation_type == "v_shape":
        pts = [(0.0, 0.0)]
        for k in range(1, n):
            arm = (k - 1) // 2 + 1
            side = 1.0 if (k - 1) % 2 == 0 else -1.0
            pts.append((arm * spacing * 0.6, side * arm * spacing * 0.6))
    elif formation_type == "triangle":
        row = 0
        while len(pts) < n:
            for k in range(row + 1):
                if len(pts) >= n:
                    break
                pts.append((row * spacing, (k - row / 2.0) * spacing))
            row += 1
    elif formation_type == "arrow":
        pts = [(0.0, 0.0)]
        for k in range(1, n):
            side = 1.0 if (k - 1) % 2 == 0 else -1.0
            pts.append((-k * spacing, side * ((k + 1) // 2) * spacing * 0.5))
    elif formation_type in {"square", "diamond", "hexagon"}:
        if formation_type == "square":
            ring = lambda r: [(r * spacing, r * spacing), (r * spacing, -r * spacing), (-r * spacing, -r * spacing), (-r * spacing, r * spacing)]
        elif formation_type == "diamond":
            ring = lambda r: [(r * spacing, 0.0), (0.0, r * spacing), (-r * spacing, 0.0), (0.0, -r * spacing)]
        else:  # hexagon
            ring = lambda r: [
                (r * spacing * math.cos(math.pi / 3 * k), r * spacing * math.sin(math.pi / 3 * k)) for k in range(6)
            ]
        ring1 = ring(1)
        if n <= len(ring1):
            pts = ring1[:n]
        else:
            pts = [(0.0, 0.0)]
            r = 1
            while len(pts) < n:
                remaining = n - len(pts)
                positions = ring(r)
                if remaining >= len(positions):
                    pts.extend(positions)
                else:
                    # spread the partial ring as evenly as possible around it
                    indices = sorted({round(i * (len(positions) - 1) / max(1, remaining - 1)) for i in range(remaining)})
                    pts.extend(positions[i] for i in indices)
                r += 1
    elif formation_type == "circle":
        if n == 1:
            pts = [(0.0, 0.0)]
        else:
            radius = spacing * n / (2.0 * math.pi)
            pts = [
                (radius * math.cos(2.0 * math.pi * k / n), radius * math.sin(2.0 * math.pi * k / n))
                for k in range(n)
            ]
    else:
        raise ValueError(f"unknown formation type: {formation_type!r}")

    return [{"x": round(x, 3), "y": round(y, 3), "z": 0.0} for x, y in pts]


# ---------------------------------------------------------------------------
# Pure functions: coverage planning
# ---------------------------------------------------------------------------


def plan_coverage(
    area: dict[str, Any],
    resolution: float = 5.0,
    partition: str = "balanced",
    path_algo: str = "boustrophedon",
    drone_ids: list[str] | None = None,
) -> dict[str, list[dict[str, float]]]:
    """Partition an area into per-drone waypoint lists.

    Pipeline: grid cells -> partition (balanced/stripe/quadrant) -> path
    ordering (boustrophedon/spiral/nearest). Altitude is always treated as a
    positive height and converted to NED z internally.
    """
    if partition not in PARTITION_ALGORITHMS:
        raise ValueError(f"unknown partition algorithm: {partition!r}")
    if path_algo not in PATH_ALGORITHMS:
        raise ValueError(f"unknown path algorithm: {path_algo!r}")
    if not drone_ids:
        raise ValueError("drone_ids must not be empty")
    drone_ids = list(drone_ids)
    resolution = max(1.0, float(resolution))
    cells = _area_cells(area, resolution)
    if not cells:
        raise ValueError("area produced no coverage cells")
    groups = _partition_cells(cells, partition, len(drone_ids))
    tasks: dict[str, list[dict[str, float]]] = {}
    for index, drone_id in enumerate(drone_ids):
        tasks[drone_id] = _order_path(groups[index], path_algo)
    return tasks


def _area_cells(area: dict[str, Any], resolution: float) -> list[dict[str, float]]:
    shape = str(area.get("shape") or "rectangle")
    altitude = abs(float(area.get("altitude") or 10.0))
    z = -altitude
    if shape == "circle":
        radius = abs(float(area.get("radius") or 25.0))
        cx = float(area.get("x") or 0.0)
        cy = float(area.get("y") or 0.0)
        cells: list[dict[str, float]] = []
        y = cy - radius
        while y <= cy + radius + 1e-6:
            x = cx - radius
            while x <= cx + radius + 1e-6:
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                    cells.append({"x": round(x, 2), "y": round(y, 2), "z": z})
                x += resolution
            y += resolution
        return cells
    width = abs(float(area.get("width") or 100.0))
    height = abs(float(area.get("height") or 100.0))
    cx = float(area.get("x") or 0.0)
    cy = float(area.get("y") or 0.0)
    cells = []
    half_w = width / 2.0
    half_h = height / 2.0
    y = cy - half_h + resolution / 2.0
    while y <= cy + half_h - resolution / 2.0 + 1e-6:
        x = cx - half_w + resolution / 2.0
        while x <= cx + half_w - resolution / 2.0 + 1e-6:
            cells.append({"x": round(x, 2), "y": round(y, 2), "z": z})
            x += resolution
        y += resolution
    return cells


def _partition_cells(cells: list[dict[str, float]], partition: str, group_count: int) -> list[list[dict[str, float]]]:
    groups: list[list[dict[str, float]]] = [[] for _ in range(group_count)]
    if partition == "stripe":
        xs = sorted({cell["x"] for cell in cells})
        for index, x in enumerate(xs):
            groups[index % group_count].extend(cell for cell in cells if cell["x"] == x)
    elif partition == "quadrant":
        cx = sum(cell["x"] for cell in cells) / len(cells)
        cy = sum(cell["y"] for cell in cells) / len(cells)

        def quadrant_key(cell: dict[str, float]) -> tuple[int, float, float]:
            return (1 if cell["x"] >= cx else 0) + 2 * (1 if cell["y"] >= cy else 0), cell["x"], cell["y"]

        ordered = sorted(cells, key=quadrant_key)
        for index, cell in enumerate(ordered):
            groups[index % group_count].append(cell)
    else:  # balanced: round-robin over the cell list
        for index, cell in enumerate(cells):
            groups[index % group_count].append(cell)
    return groups


def _order_path(cells: list[dict[str, float]], path_algo: str) -> list[dict[str, float]]:
    if not cells:
        return []
    if path_algo == "spiral":
        cx = sum(cell["x"] for cell in cells) / len(cells)
        cy = sum(cell["y"] for cell in cells) / len(cells)

        def spiral_key(cell: dict[str, float]) -> tuple[float, float]:
            radius = round(math.hypot(cell["x"] - cx, cell["y"] - cy), 1)
            return radius, math.atan2(cell["y"] - cy, cell["x"] - cx)

        return sorted(cells, key=spiral_key)
    if path_algo == "nearest":
        ordered = [cells[0]]
        rest = list(cells[1:])
        while rest:
            last = ordered[-1]
            index = min(
                range(len(rest)),
                key=lambda i: (rest[i]["x"] - last["x"]) ** 2 + (rest[i]["y"] - last["y"]) ** 2,
            )
            ordered.append(rest.pop(index))
        return ordered
    # boustrophedon: band rows by y, snake left-right / right-left
    rows: dict[float, list[dict[str, float]]] = {}
    for cell in cells:
        rows.setdefault(round(cell["y"], 2), []).append(cell)
    ordered = []
    flip = False
    for y in sorted(rows):
        row = sorted(rows[y], key=lambda cell: cell["x"])
        if flip:
            row.reverse()
        ordered.extend(row)
        flip = not flip
    return ordered


# ---------------------------------------------------------------------------
# Control loop
# ---------------------------------------------------------------------------


def _p_velocity(target: dict[str, float], position: dict[str, float], kp: float, max_velocity: float) -> dict[str, float]:
    vx = (float(target["x"]) - float(position.get("x") or 0.0)) * kp
    vy = (float(target["y"]) - float(position.get("y") or 0.0)) * kp
    vz = (float(target["z"]) - float(position.get("z") or 0.0)) * kp
    speed = math.hypot(vx, vy, vz)
    if speed > max_velocity and speed > 0:
        scale = max_velocity / speed
        vx, vy, vz = vx * scale, vy * scale, vz * scale
    return {"vx": round(vx, 3), "vy": round(vy, 3), "vz": round(vz, 3)}


class FormationController:
    """10Hz velocity-closed-loop formation/coverage controller.

    ``controller`` must implement the FlightController surface used here:
    get_status(vehicle_name), arm/takeoff/land/hover/move_by_velocity with a
    vehicle_name parameter — the real AirSimController satisfies this and a
    fake can be injected in tests.
    """

    def __init__(
        self,
        controller: Any,
        hz: float = 10.0,
        kp: float = 1.5,
        max_velocity: float = 5.0,
        waypoint_threshold: float = 0.5,
        deceleration_radius: float = 3.0,
        velocity_duration: float = 0.2,
        max_consecutive_errors: int = 50,
        min_spacing: float = 2.0,
    ) -> None:
        self.controller = controller
        self.hz = max(1.0, float(hz))
        self.kp = float(kp)
        self.max_velocity = max(0.5, float(max_velocity))
        self.waypoint_threshold = float(waypoint_threshold)
        self.deceleration_radius = max(0.5, float(deceleration_radius))
        self.velocity_duration = float(velocity_duration)
        self.max_consecutive_errors = max(1, int(max_consecutive_errors))
        self.min_spacing = max(0.5, float(min_spacing))

        self.mode: str = "idle"  # idle | formation | coverage
        self.drone_ids: list[str] = []
        self.offsets: dict[str, dict[str, float]] = {}
        self.center: dict[str, float] = {"x": 0.0, "y": 0.0, "z": -10.0}
        self.coverage_tasks: dict[str, list[dict[str, float]]] = {}
        self.coverage_indices: dict[str, int] = {}
        self.coverage_speed: float = 3.0
        self.states: dict[str, dict[str, Any]] = {}
        self.consecutive_errors = 0
        # Per-drone tick error counters (observability; cleared when a fully
        # successful tick resets the consecutive-error streak).
        self.drone_errors: dict[str, int] = {}
        # Control-loop health metrics, updated by the runner thread (EMA
        # average so no history is kept).
        self.tick_metrics: dict[str, float] = {
            "tick_count": 0,
            "avg_tick_ms": 0.0,
            "max_tick_ms": 0.0,
            "dropped_ticks": 0,
        }
        self.should_stop: Callable[[], bool] | None = None
        # Recent notable events (shutdown reasons, auto-stop, coverage
        # completion) surfaced to the agent through status().
        self.events: list[dict[str, Any]] = []

        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()
        self._effective_duration = float(velocity_duration)
        # bump every start() so a stuck old tick thread can never double-command
        # the swarm after a restart
        self._generation = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            thread = self._thread
            if thread is not None and thread.is_alive():
                # wait for the previous tick thread to actually exit (it may be
                # stuck in a slow RPC) before starting a fresh one
                thread.join(timeout=3.0)
            retired = getattr(self, "_retired_thread", None)
            if retired is not None and retired.is_alive() and retired is not threading.current_thread():
                retired.join(timeout=3.0)
            self._generation += 1
            generation = self._generation
            self._running = True

        def runner() -> None:
            period = 1.0 / self.hz
            next_tick = time.time() + period
            while self._running and self._generation == generation:
                t0 = time.time()
                try:
                    self.tick()
                except Exception:
                    self.consecutive_errors += 1
                elapsed = time.time() - t0
                # Adaptive velocity duration covers the gap when a tick is
                # slow, but it must never grow toward the failure mode: a dead
                # control thread leaves the swarm drifting for the last
                # commanded duration. The 0.4s cap keeps the fail-safe bound
                # tight (0.4s at max 5m/s => <=2m drift before AirSim/PX4
                # auto-hovers).
                self._effective_duration = max(self.velocity_duration, min(0.4, elapsed * 1.5))
                self._record_tick_metrics(elapsed)
                # Drift-compensated self-scheduling: the next tick is anchored
                # to the ORIGINAL cadence (not to this tick's end), so one slow
                # tick does not shift the whole schedule. Falling more than a
                # period behind resets the schedule and counts the missed
                # ticks — the loop never catches up with a burst of
                # back-to-back ticks.
                next_tick = self._schedule_next(next_tick, period, time.time(), self.tick_metrics)
                time.sleep(max(0.0, next_tick - time.time()))

        self._thread = threading.Thread(target=runner, name="formation_control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        # Never join the calling thread on itself (auto-stop runs inside the
        # tick thread): CPython raises "cannot join current thread".
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        # Keep the retired thread reference so a later start() can wait for it
        # to truly exit (a tick stuck in a slow RPC must not overlap the next
        # generation's ticks by even one iteration). Only keep it when a
        # thread actually ran — a second stop() with no live thread must not
        # drop the reference of a still-exiting one.
        if thread is not None:
            self._retired_thread = thread
        self._thread = None

    def _record_tick_metrics(self, elapsed: float) -> None:
        """Track control-loop health: running average/max tick time (EMA
        average, no history kept). Dropped-tick accounting lives in
        ``_schedule_next`` — the scheduler is the single source of truth."""
        metrics = self.tick_metrics
        metrics["tick_count"] = metrics.get("tick_count", 0) + 1
        ms = elapsed * 1000.0
        metrics["max_tick_ms"] = max(metrics.get("max_tick_ms", 0.0), ms)
        count = metrics.get("tick_count", 1)
        metrics["avg_tick_ms"] = metrics.get("avg_tick_ms", 0.0) + (ms - metrics.get("avg_tick_ms", 0.0)) / count

    @staticmethod
    def _schedule_next(next_tick: float, period: float, now: float, metrics: dict[str, float]) -> float:
        """Advance the self-scheduled cadence with drift compensation.

        The next tick stays anchored to the original schedule (``next_tick +
        period``). When the loop falls more than one period behind, the missed
        ticks are counted in ``metrics["dropped_ticks"]`` and the schedule
        resets to now — the loop never catches up with a burst of ticks.
        """
        next_tick += period
        delay = next_tick - now
        if delay < -period:
            skipped = math.floor(-delay / period)
            metrics["dropped_ticks"] = metrics.get("dropped_ticks", 0) + skipped
            next_tick = now + period
        return next_tick

    def ensure_started(self) -> None:
        """Start the control thread if a flight action is about to run."""
        if not self._running:
            self.start()

    def shutdown(self, reason: str) -> bool:
        """Hover every drone, drop to idle, stop the thread.

        Called on run end, backend switch, and emergency stop so the swarm
        never flies without an owner. Returns True when a formation/coverage
        mission was actually active. Residual state (drone ids, offsets) is
        kept so a later run can re-engage; coverage plans are cleared so a
        restart begins from scratch.
        """
        with self._lock:
            was_active = self.mode != "idle"
            self.mode = "idle"
            self.coverage_tasks = {}
            self.coverage_indices = {}
            ids = list(self.drone_ids)
        self.stop()
        for drone_id in ids:
            try:
                self.controller.hover(vehicle_name=drone_id)
            except Exception:
                pass
        self._release_velocity_control(ids)
        self._note_event("shutdown", reason)
        return was_active

    def _note_event(self, event_type: str, detail: Any) -> None:
        self.events.append({"type": event_type, "detail": detail, "ts": time.time()})
        self.events = self.events[-10:]

    # -- actions ------------------------------------------------------------

    def set_drones(self, drone_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            if self.mode != "idle":
                return {"status": "error", "message": "stop the current mission (hover_all/land_all) before changing drones"}
        known = set(self._list_vehicles())
        selected = [str(drone_id) for drone_id in drone_ids if str(drone_id) in known]
        unknown = [str(drone_id) for drone_id in drone_ids if str(drone_id) not in known]
        if not selected:
            return {"status": "error", "message": "no known drone ids selected", "unknown": unknown}
        with self._lock:
            self.drone_ids = selected
            # drop stale offsets/coverage for removed drones
            self.offsets = {k: v for k, v in self.offsets.items() if k in selected}
        return {"status": "ok", "drones": selected, "unknown": unknown}

    def set_formation(self, formation_type: str, spacing: float = 5.0) -> dict[str, Any]:
        with self._lock:
            if self.mode != "idle":
                return {"status": "error", "message": "stop the current mission (hover_all/land_all) before changing the formation"}
            ids = list(self.drone_ids)
        if not ids:
            return {"status": "error", "message": "set_drones first"}
        offsets = formation_offsets(formation_type, len(ids), spacing)
        if self._min_pairwise_distance(offsets) < self.min_spacing:
            return {
                "status": "error",
                "message": f"formation spacing too tight: min pairwise distance {self._min_pairwise_distance(offsets):.1f}m < {self.min_spacing}m",
            }
        with self._lock:
            self.offsets = {drone_id: dict(offset) for drone_id, offset in zip(ids, offsets)}
        return {"status": "ok", "formation_type": formation_type, "count": len(ids), "message": f"Formation set to {formation_type}."}

    def takeoff(self, altitude: float = 10.0) -> dict[str, Any]:
        with self._lock:
            ids = list(self.drone_ids)
            has_offsets = bool(self.offsets)
        altitude = max(0.5, abs(float(altitude)))
        failed: list[str] = []
        for drone_id in ids:
            try:
                if not self.controller.arm(vehicle_name=drone_id):
                    failed.append(drone_id)
                    continue
                if not self.controller.takeoff(altitude, vehicle_name=drone_id):
                    failed.append(drone_id)
            except Exception:
                failed.append(drone_id)
        with self._lock:
            self.center["z"] = -altitude
            if has_offsets and not failed:
                self.mode = "formation"  # activate the loop once everyone is up
        if failed:
            return {
                "status": "error",
                "message": f"takeoff failed for: {', '.join(failed)}",
                "failed": failed,
                "succeeded": [drone_id for drone_id in ids if drone_id not in failed],
            }
        if self.mode == "formation":
            # enter the backend's velocity-control mode (PX4: OFFBOARD). A
            # failure here means the loop must not start commanding.
            prepare = getattr(self.controller, "prepare_velocity_control", None)
            if callable(prepare):
                prepare_failed: list[str] = []
                prepared_ok: list[str] = []
                for drone_id in ids:
                    try:
                        if prepare(drone_id):
                            prepared_ok.append(drone_id)
                        else:
                            prepare_failed.append(drone_id)
                    except Exception:
                        prepare_failed.append(drone_id)
                if prepare_failed:
                    with self._lock:
                        self.mode = "idle"
                    # roll back the vehicles that did enter OFFBOARD
                    release = getattr(self.controller, "release_velocity_control", None)
                    if callable(release):
                        for drone_id in prepared_ok:
                            try:
                                release(drone_id)
                            except Exception:
                                pass
                    self.hover_all()
                    self._note_event("prepare_failed", prepare_failed)
                    return {
                        "status": "error",
                        "message": f"velocity-control mode (OFFBOARD) activation failed for: {', '.join(prepare_failed)}",
                        "failed": prepare_failed,
                    }
            self.ensure_started()
        return {"status": "ok", "altitude": altitude, "mode": self.mode, "message": f"Formation took off to {altitude}m."}

    def move_center(self, x: float, y: float, z: float | None = None) -> dict[str, Any]:
        with self._lock:
            if self.mode != "formation":
                return {"status": "error", "message": "move_center requires formation mode (takeoff after set_formation)"}
            self.center["x"] = float(x)
            self.center["y"] = float(y)
            if z is not None:
                self.center["z"] = float(z)
            center = dict(self.center)
        return {"status": "ok", "center": center, "message": f"Formation center moved to ({center['x']}, {center['y']})."}

    def rotate(self, angle_deg: float) -> dict[str, Any]:
        theta = math.radians(float(angle_deg))
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        with self._lock:
            for drone_id, offset in self.offsets.items():
                x, y = offset["x"], offset["y"]
                offset["x"] = round(x * cos_t - y * sin_t, 3)
                offset["y"] = round(x * sin_t + y * cos_t, 3)
        return {"status": "ok", "angle_deg": float(angle_deg), "message": f"Formation rotated {angle_deg} degrees."}

    def scale(self, factor: float) -> dict[str, Any]:
        factor = max(0.1, float(factor))
        with self._lock:
            scaled = {
                drone_id: {
                    "x": round(offset["x"] * factor, 3),
                    "y": round(offset["y"] * factor, 3),
                    "z": offset["z"],
                }
                for drone_id, offset in self.offsets.items()
            }
        if self._min_pairwise_distance(list(scaled.values())) < self.min_spacing:
            return {
                "status": "error",
                "message": f"scale would violate min spacing {self.min_spacing}m; scale factor rejected",
            }
        with self._lock:
            self.offsets = scaled
        return {"status": "ok", "factor": factor, "message": f"Formation scaled by {factor}."}

    def coverage_plan(
        self,
        area: dict[str, Any],
        resolution: float = 5.0,
        partition: str = "balanced",
        path_algo: str = "boustrophedon",
        speed: float = 3.0,
    ) -> dict[str, Any]:
        with self._lock:
            if self.mode != "idle":
                return {"status": "error", "message": "stop the current mission (hover_all/land_all) before planning a new coverage"}
            ids = list(self.drone_ids)
        if not ids:
            return {"status": "error", "message": "set_drones first"}
        try:
            tasks = plan_coverage(area, resolution, partition, path_algo, ids)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        with self._lock:
            self.coverage_tasks = tasks
            self.coverage_indices = {drone_id: 0 for drone_id in ids}
            self.coverage_speed = max(0.5, float(speed))
        total = sum(len(waypoints) for waypoints in tasks.values())
        return {
            "status": "ok",
            "total_waypoints": total,
            "per_drone": {drone_id: len(waypoints) for drone_id, waypoints in tasks.items()},
            "message": f"Coverage planned: {total} waypoints across {len(ids)} drones.",
        }

    def coverage_start(self) -> dict[str, Any]:
        with self._lock:
            if not self.coverage_tasks:
                return {"status": "error", "message": "coverage_plan first"}
            ids = list(self.drone_ids)
        # live check: at least one drone must be airborne, otherwise the
        # mission would hang forever with no velocity commands
        airborne = False
        for drone_id in ids:
            try:
                status = self.controller.get_status(drone_id)
                state = status.to_dict() if hasattr(status, "to_dict") else dict(status)
                if state.get("flying"):
                    airborne = True
                    break
            except Exception:
                continue
        if not airborne:
            return {"status": "error", "message": "no airborne drones; use takeoff before coverage_start"}
        # enter the backend's velocity-control mode (PX4: OFFBOARD) — coverage
        # streams velocity setpoints exactly like formation does
        prepare = getattr(self.controller, "prepare_velocity_control", None)
        if callable(prepare):
            prepare_failed: list[str] = []
            prepared_ok: list[str] = []
            for drone_id in ids:
                try:
                    if prepare(drone_id):
                        prepared_ok.append(drone_id)
                    else:
                        prepare_failed.append(drone_id)
                except Exception:
                    prepare_failed.append(drone_id)
            if prepare_failed:
                # roll back the vehicles that did enter OFFBOARD
                release = getattr(self.controller, "release_velocity_control", None)
                if callable(release):
                    for drone_id in prepared_ok:
                        try:
                            release(drone_id)
                        except Exception:
                            pass
                return {
                    "status": "error",
                    "message": f"velocity-control mode (OFFBOARD) activation failed for: {', '.join(prepare_failed)}",
                    "failed": prepare_failed,
                }
        with self._lock:
            self.mode = "coverage"
        self.ensure_started()
        return {"status": "ok", "mode": "coverage", "message": "Coverage mission started."}

    def hover_all(self) -> dict[str, Any]:
        with self._lock:
            ids = list(self.drone_ids)
            self.mode = "idle"
        for drone_id in ids:
            try:
                self.controller.hover(vehicle_name=drone_id)
            except Exception:
                pass
        self._release_velocity_control(ids)
        return {"status": "ok", "message": "All drones hover; formation mode idle."}

    def land_all(self) -> dict[str, Any]:
        with self._lock:
            ids = list(self.drone_ids)
            self.mode = "idle"
        failed: list[str] = []
        for drone_id in ids:
            try:
                if not self.controller.land(vehicle_name=drone_id):
                    failed.append(drone_id)
            except Exception:
                failed.append(drone_id)
        self._release_velocity_control(ids)
        if failed:
            return {"status": "error", "message": f"land failed for: {', '.join(failed)}", "failed": failed}
        return {"status": "ok", "message": "All drones landed."}

    def status(self) -> dict[str, Any]:
        with self._lock:
            mode = self.mode
            ids = list(self.drone_ids)
            center = dict(self.center)
            offsets = {drone_id: dict(offset) for drone_id, offset in self.offsets.items()}
            coverage_indices = dict(self.coverage_indices)
            coverage_tasks = {drone_id: list(waypoints) for drone_id, waypoints in self.coverage_tasks.items()}
            states = {drone_id: dict(state) for drone_id, state in self.states.items()}
        drones: list[dict[str, Any]] = []
        for drone_id in ids:
            state = states.get(drone_id) or {}
            position = state.get("position_ned")
            target = None
            if mode == "formation" and drone_id in offsets:
                offset = offsets[drone_id]
                target = {
                    "x": round(center["x"] + offset["x"], 3),
                    "y": round(center["y"] + offset["y"], 3),
                    "z": round(center["z"] + offset["z"], 3),
                }
            drones.append(
                {
                    "id": drone_id,
                    "position": position,
                    "airborne": bool(state.get("flying")),
                    "target": target,
                }
            )
        stable = False
        if mode == "formation" and drones:
            stable = all(
                drone["airborne"] and drone["target"] and self._distance(drone["position"], drone["target"]) <= 0.5
                for drone in drones
                if drone["target"] is not None
            )
        progress: dict[str, Any] = {"covered": 0, "total": 0, "percent": 0.0}
        if mode == "coverage":
            total = sum(len(waypoints) for waypoints in coverage_tasks.values())
            covered = sum(min(coverage_indices.get(drone_id, 0), len(waypoints)) for drone_id, waypoints in coverage_tasks.items())
            progress = {"covered": covered, "total": total, "percent": round(covered / total * 100, 1) if total else 0.0}
        return {
            "status": "ok",
            "mode": mode,
            "stable": stable,
            "drones": drones,
            "progress": progress,
            "consecutive_errors": self.consecutive_errors,
            "drone_errors": dict(self.drone_errors),
            "tick_metrics": dict(self.tick_metrics),
            "events": list(self.events[-5:]),
        }

    # -- control loop -------------------------------------------------------

    def tick(self) -> None:
        """One control iteration (public so tests can drive it synchronously)."""
        if self.mode == "idle":
            return
        if self.should_stop is not None and self.should_stop():
            self.hover_all()
            self._note_event("stopped", "emergency_stop")
            return
        if not self.drone_ids:
            return
        states: dict[str, dict[str, Any]] = {}
        tick_errors = 0
        for drone_id in self.drone_ids:
            if self.should_stop is not None and self.should_stop():
                self.hover_all()
                self._note_event("stopped", "emergency_stop")
                return
            try:
                status = self.controller.get_status(drone_id)
                state = status.to_dict() if hasattr(status, "to_dict") else dict(status)
                states[drone_id] = state
            except Exception:
                tick_errors += 1
                self.drone_errors[drone_id] = self.drone_errors.get(drone_id, 0) + 1
        self.states = states
        # PX4-style backends: if a vehicle left OFFBOARD (RC takeover, mode
        # switch), the velocity loop must stop immediately.
        active_check = getattr(self.controller, "is_velocity_control_active", None)
        if callable(active_check):
            for drone_id, state in states.items():
                if state.get("flying"):
                    try:
                        if not active_check(drone_id):
                            self.hover_all()
                            self._note_event("mode_lost", drone_id)
                            self.stop()
                            return
                    except Exception:
                        tick_errors += 1
        velocities = self._compute_velocities(states)
        if self.mode == "coverage" and self._coverage_exhausted():
            # all waypoints consumed: hover and drop to idle instead of idling
            # at the last waypoint forever
            self.hover_all()
            self._note_event("coverage_complete", self._coverage_progress())
            return
        for drone_id, velocity in velocities.items():
            if self.should_stop is not None and self.should_stop():
                self.hover_all()
                self._note_event("stopped", "emergency_stop")
                return
            if self.mode == "idle":
                # a hover_all/land_all raced with this tick: never send a
                # velocity command after the swarm was told to stop
                return
            try:
                sent = self._send_velocity(drone_id, velocity)
                if not sent:
                    # a failed send (link loss, backend rejection) counts as a
                    # tick error so the auto-stop threshold still works
                    tick_errors += 1
                    self.drone_errors[drone_id] = self.drone_errors.get(drone_id, 0) + 1
            except Exception:
                tick_errors += 1
                self.drone_errors[drone_id] = self.drone_errors.get(drone_id, 0) + 1
        # "Consecutive" semantics: a fully successful tick resets the error
        # counter, so only back-to-back failing ticks accumulate toward
        # auto-stop. A single transient error mid-mission must not stop the
        # swarm minutes later.
        if tick_errors:
            self.consecutive_errors += tick_errors
            if self.consecutive_errors >= self.max_consecutive_errors:
                self.hover_all()
                self._note_event("auto_stop", "too many consecutive errors")
                self.stop()
        else:
            self.consecutive_errors = 0
            self.drone_errors = {}

    def _compute_velocities(self, states: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
        if self.mode == "formation":
            velocities: dict[str, dict[str, float]] = {}
            for drone_id, state in states.items():
                offset = self.offsets.get(drone_id)
                if offset is None or not state.get("flying"):
                    continue
                position = state.get("position_ned") or {}
                target = {
                    "x": self.center["x"] + offset["x"],
                    "y": self.center["y"] + offset["y"],
                    "z": self.center["z"] + offset["z"],
                }
                velocities[drone_id] = _p_velocity(target, position, self.kp, self.max_velocity)
            return velocities
        if self.mode == "coverage":
            velocities = {}
            for drone_id, state in states.items():
                waypoints = self.coverage_tasks.get(drone_id)
                if not waypoints or not state.get("flying"):
                    continue
                index = self.coverage_indices.get(drone_id, 0)
                if index >= len(waypoints):
                    # This drone finished its share while others are still
                    # working. Keep a zero setpoint on the OFFBOARD stream so
                    # the PX4 OFFBOARD watchdog (drops the mode after ~0.5s
                    # without setpoints) does not trip the whole-swarm
                    # mode_lost auto-stop mid-coverage.
                    velocities[drone_id] = {"vx": 0.0, "vy": 0.0, "vz": 0.0}
                    continue
                position = state.get("position_ned") or {}
                target = waypoints[index]
                if self._distance(position, target) < self.waypoint_threshold:
                    index += 1
                    self.coverage_indices[drone_id] = index
                    if index >= len(waypoints):
                        continue
                    target = waypoints[index]
                current_dist = self._distance(position, target)
                direction_x = (target["x"] - float(position.get("x") or 0.0)) / max(1e-6, current_dist)
                direction_y = (target["y"] - float(position.get("y") or 0.0)) / max(1e-6, current_dist)
                direction_z = (target["z"] - float(position.get("z") or 0.0)) / max(1e-6, current_dist)
                deceleration = min(1.0, current_dist / self.deceleration_radius)
                speed = max(self.coverage_speed * deceleration, self.coverage_speed * 0.15)
                speed = min(speed, self.max_velocity)
                velocities[drone_id] = {
                    "vx": round(direction_x * speed, 3),
                    "vy": round(direction_y * speed, 3),
                    "vz": round(direction_z * speed, 3),
                }
            return velocities
        return {}

    # -- helpers ------------------------------------------------------------

    def _send_velocity(self, drone_id: str, velocity: dict[str, float]) -> bool:
        """One setpoint per tick: the loop IS the stream.

        Backends with the formation protocol (PX4) send a single setpoint;
        AirSim falls back to its duration-based move_by_velocity which the loop
        re-issues every tick.
        """
        send = getattr(self.controller, "send_velocity_setpoint", None)
        if callable(send):
            return send(velocity["vx"], velocity["vy"], velocity["vz"], vehicle_name=drone_id)
        return self.controller.move_by_velocity(
            velocity["vx"],
            velocity["vy"],
            velocity["vz"],
            self._effective_duration,
            vehicle_name=drone_id,
        )

    def _release_velocity_control(self, ids: list[str]) -> None:
        """Belt-and-suspenders offboard cleanup after hover/land/shutdown."""
        release = getattr(self.controller, "release_velocity_control", None)
        if not callable(release):
            return
        for drone_id in ids:
            try:
                release(drone_id)
            except Exception:
                pass

    def _list_vehicles(self) -> list[str]:
        try:
            vehicles = self.controller.list_vehicles() if hasattr(self.controller, "list_vehicles") else []
            return [str(vehicle) for vehicle in (vehicles or []) if str(vehicle)]
        except Exception:
            return []

    def _min_pairwise_distance(self, offsets: list[dict[str, float]]) -> float:
        best = math.inf
        for i in range(len(offsets)):
            for j in range(i + 1, len(offsets)):
                distance = math.hypot(
                    offsets[i]["x"] - offsets[j]["x"],
                    offsets[i]["y"] - offsets[j]["y"],
                )
                best = min(best, distance)
        return best

    def _coverage_exhausted(self) -> bool:
        return all(
            self.coverage_indices.get(drone_id, 0) >= len(waypoints)
            for drone_id, waypoints in self.coverage_tasks.items()
        )

    def _coverage_progress(self) -> dict[str, Any]:
        total = sum(len(waypoints) for waypoints in self.coverage_tasks.values())
        covered = sum(
            min(self.coverage_indices.get(drone_id, 0), len(waypoints))
            for drone_id, waypoints in self.coverage_tasks.items()
        )
        return {"covered": covered, "total": total, "percent": round(covered / total * 100, 1) if total else 0.0}

    @staticmethod
    def _distance(a: Any, b: Any) -> float:
        if not isinstance(a, dict) or not isinstance(b, dict):
            return math.inf
        try:
            return math.hypot(
                float(a.get("x") or 0.0) - float(b.get("x") or 0.0),
                float(a.get("y") or 0.0) - float(b.get("y") or 0.0),
                float(a.get("z") or 0.0) - float(b.get("z") or 0.0),
            )
        except (TypeError, ValueError):
            return math.inf
