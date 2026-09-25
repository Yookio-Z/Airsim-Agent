"""Background guardians: the flight-envelope watchdog and the tracking assist.

Both are daemon threads that sample telemetry on their own schedule and act
without the Agent loop (abort+land on an envelope breach; keep the locked target
centred). Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from .run_state import RunState
from src.config import config
import math
import os
import threading
import time


class GuardiansMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _maybe_start_envelope_guard(self, run: RunState) -> None:
        """为执行任务启动飞行包线看门狗。

        历史事故：飞机曾在无人干预下带着错误的估计值飞了好几分钟（真实失控，
        或 EKF 位置估计发散）。逐条指令的安全校验发现不了这种情况——车上飘着
        的时候根本没有指令下发——所以需要一个按秒采样的持续兜底：连续 3 次越界
        就中止任务并降落。

        包线分两档，因为两类任务的工作包线差得很远：
          * 近距离识别/追踪：2~3m 定高、小范围机动，越界即失控，用紧包线；
          * 普通飞行任务：用安全包线本身（带余量），作为逐条校验背后的连续兜底。
        以前只有一个硬编码的 8m/70m，把它套到所有飞行任务上会让任何在 15m
        （config.search_altitude 的默认值）正常作业的任务被强制降落。
        """
        if run is None or not getattr(run, "execute", False):
            return
        profile = self._envelope_profile(run)
        if profile is None:
            return
        max_alt_m, max_dist_m, from_takeoff = profile
        existing = self._envelope_thread
        if existing is not None and existing.is_alive() and self._envelope_run_id == run.run_id:
            return
        # 换 run 时必须先停掉旧看门狗再启动新的，否则旧线程会一直活着读 _current
        # （两条线程同时探测、又都可能在越界时下达中止+降落）。
        if existing is not None and existing.is_alive():
            self._stop_envelope_guard()
        self._envelope_stop.clear()
        self._envelope_run_id = run.run_id
        self._envelope_thread = threading.Thread(
            target=self._envelope_guard_loop,
            args=(run.run_id, max_alt_m, max_dist_m, from_takeoff),
            daemon=True,
            name="flight-envelope-guard",
        )
        self._envelope_thread.start()

    # 计划里"明确要求过高度"的参数名（含相对爬升与区域高度）
    _PLANNED_ALTITUDE_KEYS = ("altitude", "search_altitude", "up_m", "area_altitude")

    @classmethod
    def _planned_altitude_m(cls, run: RunState) -> float:
        """计划里明确要求过的最大高度（米）；无法判断时返回 0。

        看门狗要抓的是"没有被指令要求过的偏移"，所以它的天花板不能低于计划
        自己要求的高度。一个"靠近确认"的任务若在计划里写了 up_m=15，把天花板
        钉在 8m 会把它当成失控强制降落，还会报出"位置估计发散"这种错误诊断。
        """
        steps = list(getattr(getattr(run, "plan", None), "steps", None) or [])
        best = 0.0
        for step in steps:
            params = getattr(step, "params", None)
            if not isinstance(params, dict):
                continue
            for key in cls._PLANNED_ALTITUDE_KEYS:
                raw = params.get(key)
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    best = max(best, abs(value))
            # drone_fly_to / 航点里的 z 是 NED 高度（负值朝上）
            try:
                z = float(params.get("z"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(z):
                best = max(best, abs(z))
        return best

    def _envelope_profile(self, run: RunState) -> tuple[float, float, bool] | None:
        """这条任务该用哪档包线；不需要监控时返回 None。

        第三项是水平距离的基准：True = 相对起飞点（近距离小范围机动的语义），
        False = 相对 NED 原点（与围栏同一基准）。
        """
        try:
            close_range = bool(self.planner._is_close_range_visual_command(run.command))
        except Exception:
            close_range = False
        margin = max(1.0, float(config.envelope_margin_ratio))
        if close_range:
            # 2~3m 定高、小范围机动：越界即失控，量的是"离起飞点多远"。
            # 天花板取"配置的紧包线"与"计划要求的高度 × 余量"中的较大者：前者
            # 抓无指令偏移，后者保证不会把计划自己要求的爬升判成失控。
            planned = self._planned_altitude_m(run)
            return (
                max(float(config.close_range_envelope_altitude_m), planned * margin),
                float(config.close_range_envelope_horizontal_m),
                True,
            )
        # 任何会真的动飞机的执行任务都应受连续监控。
        try:
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if not capabilities.get("flight_control"):
                return None
        except Exception:
            return None
        margin = max(1.0, float(config.envelope_margin_ratio))
        # 水平判据必须与围栏同基准（NED 原点），不能量"相对起飞点的位移"：
        # 在 (20,0) 起飞的合法任务飞到围栏西边界 (-100,0)，位移 120m 而距原点
        # 只有 100m —— 位移判据会把一架完全合规的飞机判成失控并强制降落，还会
        # 报出"可能是位置估计发散"这种错误诊断。余量只用来让看门狗落在逐条
        # 校验之后，不承担基准换算。
        return (
            float(config.safety_max_altitude_m) * margin,
            float(config.safety_geofence_m) * margin,
            False,
        )

    def _stop_envelope_guard(self, run_id: str = "") -> None:
        """停止包线看门狗。

        传入 run_id 时只关"属于该 run"的看门狗：不带归属的调用会把另一个正在
        飞行的任务的看门狗一起关掉。不传（run_id=""）表示无条件关闭，保留给
        后端切换/急停这类确实要全关的路径。
        """
        if run_id and self._envelope_run_id and self._envelope_run_id != run_id:
            return
        self._envelope_stop.set()
        thread = self._envelope_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._envelope_thread = None
        self._envelope_run_id = ""

    def _envelope_guard_loop(
        self, run_id: str, max_alt_m: float, max_dist_m: float, from_takeoff: bool = True
    ) -> None:
        # 包线由 _envelope_profile 按任务类型与配置给出（以前是这里硬编码的
        # 8m/70m，套到所有飞行任务上会误伤在 15m 正常作业的任务）。
        breaches = 0
        origin: tuple[float, float] | None = None
        while not self._envelope_stop.is_set():
            with self._lock:
                current = self._current
            # awaiting_approval 也是"活着"的状态：等待审批只是暂时不是 running，
            # 以前这里会被当成终态退出，而 plan-execute 路径没有重挂点，于是
            # 一次审批之后整段任务就再也没有包线保护了。
            if (
                current is None
                or current.run_id != run_id
                or current.status not in {"running", "paused", "awaiting_approval"}
            ):
                break
            if self.supervisor.is_emergency_stopped():
                break
            try:
                drone = self.tools.status_snapshot().get("drone") or {}
            except Exception:
                drone = {}
            pos = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            try:
                alt = abs(float((pos or {}).get("z", 0.0) or 0.0))
                x = float((pos or {}).get("x", 0.0) or 0.0)
                y = float((pos or {}).get("y", 0.0) or 0.0)
            except (TypeError, ValueError):
                alt, x, y = 0.0, 0.0, 0.0
            flying = bool(drone.get("flying"))
            # 水平基准：普通任务与围栏同基准（NED 原点），近距离任务量相对起飞点
            # 的位移。见 _envelope_profile —— 用错基准会把合规飞行判成失控。
            if from_takeoff:
                if origin is None and flying:
                    origin = (x, y)
                dist = math.hypot(x - origin[0], y - origin[1]) if origin else 0.0
            else:
                dist = math.hypot(x, y)
            if flying and (alt > max_alt_m or dist > max_dist_m):
                breaches += 1
            else:
                breaches = 0
            if breaches >= 3:
                basis = "相对起飞点位移" if from_takeoff else "距 NED 原点"
                self._append_event(
                    "danger", "system",
                    f"飞行包线保护：高度 {alt:.1f}m / {basis} {dist:.1f}m 超出安全包线，"
                    "已中止任务并执行降落（可能是真实失控或位置估计发散，请检查飞控估计与罗盘）",
                    {
                        "altitude_m": round(alt, 2),
                        "distance_m": round(dist, 1),
                        "distance_basis": "takeoff" if from_takeoff else "origin",
                        "run_id": run_id,
                    },
                )
                try:
                    self._cancel_active_work()
                except Exception:
                    pass
                try:
                    self.tools.execute("drone_land", {}, dry_run=False, blocked_by_supervisor=False)
                except Exception:
                    pass
                with self._lock:
                    if self._current is not None and self._current.run_id == run_id:
                        self._current.status = "failed"
                        self._current.failure_reason = "飞行包线保护触发：超出安全高度/范围，已中止并降落"
                break
            time.sleep(1.0)

    def _maybe_start_tracking_assist(self, run: RunState) -> None:
        """任务带追踪意图且已看到目标时，启动算法级居中伺服线程。

        默认关闭（DRONE_TRACKING_ASSIST=true 才启用）。原因：该伺服依赖
        OFFBOARD 偏航率流保持连续，一旦因与 Agent 工具抢占飞控而中断，PX4 会
        回退到 POSCTL/ALTCTL；在位置估计不稳时表现为飞机下沉/漂移（实测已多次
        触发）。目标居中的稳定性收益目前抵不过飞行风险，故改为显式开启。
        """
        if str(os.environ.get("DRONE_TRACKING_ASSIST", "")).strip().lower() not in {"1", "true", "yes", "on"}:
            return
        if run is None or not run.execute:
            return
        if run.status not in {"running", "queued"}:
            return
        # 追踪语义由计划声明（keep_reacting）：词表判断会让"保持锁定""别跟丢"
        # 这类换个说法的同一任务拿不到伺服。
        if not bool(getattr(getattr(run, "plan", None), "keep_reacting", False)):
            return
        existing = self._tracking_assist
        if existing is not None and existing.is_alive() and self._tracking_assist_run_id == run.run_id:
            return
        axis = self.perception_axis
        if axis is None or not getattr(axis, "enabled", False):
            return
        try:
            if not (axis.snapshot() or {}).get("primary"):
                return  # 还没看到目标，等确认后再启动
        except Exception:
            return
        self._tracking_assist_stop.clear()
        self._tracking_assist_run_id = run.run_id
        self._tracking_assist = threading.Thread(
            target=self._tracking_assist_loop, args=(run.run_id,), daemon=True, name="tracking-assist"
        )
        self._tracking_assist.start()
        self._append_event("info", "tracking", "居中伺服已启动（目标保持在画面中央）", {"run_id": run.run_id})

    def _stop_tracking_assist(self, reason: str = "") -> None:
        self._tracking_assist_stop.set()
        thread = self._tracking_assist
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        if thread is not None and reason:
            self._append_event("info", "tracking", f"居中伺服已停止（{reason}）", {})
        self._tracking_assist = None
        self._tracking_assist_run_id = ""

    def _tracking_assist_loop(self, run_id: str) -> None:
        """算法闭环：把感知轴锁定的目标持续拉回画面中央（仅横向 yaw）。

        安全约束（针对历史掉机事故）：
        - 只在拿到飞控串行闸门时动作；Agent 飞行工具执行期间完全不插手。
        - 只用 yaw-rate 一个原语，进入 OFFBOARD 后按 4Hz 持续发送设定值；
          高度由零速度设定保持，不再发升降/位置目标。
        - 目标丢失、任务结束、急停或闸门被抢占过久 → 退出并释放 OFFBOARD。
        """
        axis = self.perception_axis
        controller = getattr(self.tools, "controller", None)
        if axis is None or controller is None:
            return
        # 只有具备连续速度/偏航率原语的后端（PX4 MAVLink）才启用自动居中，
        # 避免在 AirSim 等后端上调用不存在的方法。
        if not all(
            hasattr(controller, name)
            for name in ("is_velocity_control_active", "prepare_velocity_control",
                         "release_velocity_control", "send_yaw_rate_setpoint", "is_flying_now")
        ):
            return
        lost_since = None
        frame_size = None
        velocity_mode = False
        last_prepare = 0.0
        try:
            while not self._tracking_assist_stop.is_set():
                if self.supervisor.is_emergency_stopped():
                    break
                with self._lock:
                    current = self._current
                if current is None or current.run_id != run_id or current.status not in {"running", "paused"}:
                    break
                try:
                    snap = axis.snapshot() or {}
                except Exception:
                    snap = {}
                primary = snap.get("primary")
                if not primary:
                    lost_since = lost_since or time.time()
                    if time.time() - lost_since > 3.0:
                        break
                    time.sleep(0.25)
                    continue
                lost_since = None
                if frame_size is None:
                    # Frame size comes from the snapshot; only fall back to
                    # decoding the annotated JPEG if the axis has not reported
                    # one yet (e.g. the first tick after start).
                    snap = axis.snapshot() or {}
                    width = int(snap.get("frame_width") or 0)
                    height = int(snap.get("frame_height") or 0)
                    if width > 0 and height > 0:
                        frame_size = (width, height)
                    else:
                        try:
                            import cv2
                            import numpy as np

                            annotated = axis.annotated_frame()
                            img = cv2.imdecode(np.frombuffer(annotated[0], np.uint8), cv2.IMREAD_UNCHANGED)
                            if img is not None:
                                frame_size = (img.shape[1], img.shape[0])
                        except Exception:
                            frame_size = (640, 480)
                # 只在没有 Agent 飞行工具占用飞控时动作；拿不到就跳过这一拍。
                if not self.tools.acquire_control_gate(blocking=False):
                    time.sleep(0.25)
                    continue
                try:
                    if not velocity_mode or not controller.is_velocity_control_active():
                        # 重新进入 OFFBOARD 前确认在飞，且失败时不反复重试刷屏。
                        if not bool(getattr(controller, "is_flying_now", lambda: True)()):
                            velocity_mode = False
                            time.sleep(0.25)
                            continue
                        now = time.time()
                        if now - last_prepare < 2.0:
                            time.sleep(0.25)
                            continue
                        last_prepare = now
                        velocity_mode = bool(controller.prepare_velocity_control())
                    if velocity_mode:
                        self.tools.servo_step(primary, frame_size=frame_size)
                except Exception:
                    velocity_mode = False
                finally:
                    self.tools.release_control_gate()
                time.sleep(0.25)
        finally:
            # 退出时必须释放 OFFBOARD，否则飞控会因设定值中断进入 failsafe。
            if velocity_mode:
                try:
                    if self.tools.acquire_control_gate(blocking=True, timeout=3.0):
                        try:
                            controller.release_velocity_control()
                        finally:
                            self.tools.release_control_gate()
                except Exception:
                    pass
