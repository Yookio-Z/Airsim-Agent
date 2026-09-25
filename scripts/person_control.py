"""Control the walking person in the AirSim scene.

The person is an ordinary level actor, so control goes through AirSim's object
pose API; ``scripts/person_actor.py`` owns the 40 Hz drive loop. This script
is the manual entry point: keyboard teleop, one-shot commands, and a short demo
routine that doubles as an end-to-end smoke check.

    python scripts/person_control.py --list                  # who can I drive
    python scripts/person_control.py --state                 # pose + command
    python scripts/person_control.py --place 10 5 --yaw 90   # put him down
    python scripts/person_control.py --walk 3 0              # 3 m ahead
    python scripts/person_control.py --goto 20 6             # walk to a NED point
    python scripts/person_control.py --turn 180              # face a heading
    python scripts/person_control.py --patrol "10,5;14,5;14,9"
    python scripts/person_control.py --demo                  # scripted routine
    python scripts/person_control.py                         # keyboard teleop
    python scripts/person_control.py --repl                  # line-command mode

Keyboard teleop keys::

    w / s    walk forward / backward      a / d    step left / right
    q / e    turn left / right            space    stop
    f        toggle walk (1.4) / run (3.6) m/s
    c        toggle the cosmetic gait (bob / hip sway / lean)
    p        print state                  h        help
    x        quit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.person_actor import (  # noqa: E402
    DEFAULT_PERSON_NAME,
    PersonActor,
    PersonActorError,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")

WALK_SPEED = 1.4
RUN_SPEED = 3.6
TURN_RATE = 90.0

HELP = """\
键盘遥控（按一下切换一个动作，可同时按多个）:
  w / s   前进 / 后退          a / d   左移 / 右移
  q / e   左转 / 右转          space   全部停止
  f       切换 走(1.4)/跑(3.6)  m/s
  c       切换假步态（上下轻微起伏，纯视觉）
  p       打印状态              h       本帮助
  x       退出
"""


def _print_state(actor: PersonActor, prefix: str = "") -> None:
    state = actor.state(fresh=True)
    target = "" if state.target is None else f" -> ({state.target[0]:.1f}, {state.target[1]:.1f})"
    print(
        f"{prefix}[{state.mode}] pos=({state.x:.2f}, {state.y:.2f}) yaw={state.yaw_deg:.0f} "
        f"speed={state.speed_mps:.2f} blocked={state.blocked}{target}"
        + (f" error={state.error}" if state.error else ""),
        flush=True,
    )


def _parse_waypoints(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for chunk in text.replace(";", " ").split():
        x_str, _, y_str = chunk.partition(",")
        if not y_str:
            raise ValueError(f"waypoint {chunk!r} is not 'x,y'")
        points.append((float(x_str), float(y_str)))
    if not points:
        raise ValueError("no waypoints given")
    return points


# --------------------------------------------------------------------- teleop


def _read_key() -> str | None:
    """Non-blocking single keypress on Windows; None when nothing is buffered."""
    if os.name != "nt":
        return None
    import msvcrt

    if not msvcrt.kbhit():
        return None
    char = msvcrt.getch()
    if char in (b"\x00", b"\xe0"):  # arrow / function key prefix
        msvcrt.getch()
        return None
    try:
        return char.decode("utf-8", errors="ignore").lower()
    except Exception:
        return None


def teleop(actor: PersonActor) -> int:
    if os.name != "nt":
        print("非 Windows 终端：改用逐行指令模式（walk 3 0 / goto 12 6 / turn 90 / stop / state / quit）")
        return _repl(actor)

    print(HELP, flush=True)
    keys = {"forward": False, "back": False, "left": False, "right": False, "turn_left": False, "turn_right": False}
    run = False
    last_line = ""
    last_status = time.monotonic()
    try:
        while True:
            key = _read_key()
            if key is not None:
                if key in {"x", "\x1b"}:
                    break
                if key == "h":
                    print(HELP, flush=True)
                elif key == "p":
                    _print_state(actor, "state  ")
                elif key == " ":
                    keys = dict.fromkeys(keys, False)
                elif key == "f":
                    run = not run
                elif key == "c":
                    actor.cosmetic_walk = not actor.cosmetic_walk
                    print(f"cosmetic gait -> {actor.cosmetic_walk}", flush=True)
                elif key == "w":
                    keys["forward"], keys["back"] = not keys["forward"], False
                elif key == "s":
                    keys["back"], keys["forward"] = not keys["back"], False
                elif key == "a":
                    keys["left"], keys["right"] = not keys["left"], False
                elif key == "d":
                    keys["right"], keys["left"] = not keys["right"], False
                elif key == "q":
                    keys["turn_left"], keys["turn_right"] = not keys["turn_left"], False
                elif key == "e":
                    keys["turn_right"], keys["turn_left"] = not keys["turn_right"], False
                if key in {"w", "s", "a", "d", "q", "e", "f", "c", " "}:
                    speed = RUN_SPEED if run else WALK_SPEED
                    forward = (1.0 if keys["forward"] else 0.0) - (1.0 if keys["back"] else 0.0)
                    side = (1.0 if keys["right"] else 0.0) - (1.0 if keys["left"] else 0.0)
                    turn = (TURN_RATE if keys["turn_right"] else 0.0) - (TURN_RATE if keys["turn_left"] else 0.0)
                    if forward or side or turn:
                        actor.drive(forward, side, turn, speed=speed if (forward or side) else None)
                    else:
                        actor.stop()
                    line = (
                        f"cmd: {'run' if run else 'walk'} fwd={forward:+.0f} side={side:+.0f} "
                        f"turn={turn:+.0f}deg/s"
                    )
                    if line != last_line:
                        print(line, flush=True)
                        last_line = line
            if time.monotonic() - last_status >= 5.0:
                _print_state(actor, "  ")
                last_status = time.monotonic()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        actor.stop()
    print("teleop stopped", flush=True)
    return 0


def _repl(actor: PersonActor) -> int:
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        verb, rest = parts[0], parts[1:]
        try:
            if verb in {"quit", "exit", "q"}:
                break
            if verb == "state":
                _print_state(actor)
            elif verb == "walk":
                print("walk ->", actor.walk(float(rest[0]), float(rest[1])), flush=True)
            elif verb == "goto":
                print("goto ->", actor.goto(float(rest[0]), float(rest[1])), flush=True)
            elif verb == "turn":
                print("turn ->", actor.turn_to(float(rest[0])), flush=True)
            elif verb == "place":
                print(json.dumps(actor.set_pose(float(rest[0]), float(rest[1])).to_dict(), ensure_ascii=False))
            elif verb == "stop":
                actor.stop()
            else:
                print("未知指令：walk FWD RIGHT | goto X Y | turn YAW | place X Y | state | stop | quit")
        except (IndexError, ValueError) as exc:
            print(f"参数错误：{exc}")
    actor.stop()
    return 0


# --------------------------------------------------------------------- demo


def demo(actor: PersonActor, *, speed: float = 1.4) -> int:
    """Short scripted routine; every step must succeed for exit code 0."""
    print("=== demo: 摆位 -> 前进 -> 转身 -> 走折线 ===", flush=True)
    actor.set_pose(-10.0, 10.0, yaw_deg=0.0)
    _print_state(actor, "start ")

    checks: list[tuple[str, bool]] = []
    started = time.monotonic()
    checks.append(("walk 3 m forward", actor.walk(forward=3.0, speed=speed)))
    checks.append(("turn to 90 deg", actor.turn_to(90.0)))
    checks.append(("walk 2 m forward", actor.walk(forward=2.0, speed=speed)))
    legs = actor.patrol([(-10.0, 10.0), (-6.0, 10.0)], speed=speed, arrive=0.4)
    checks.append((f"patrol 2 legs (got {legs})", legs == 2))
    # 边走边转：机体速度 + 偏航角速度应当走出弧线
    actor.drive(forward=speed, turn=TURN_RATE, duration=1.2)
    actor.wait(1.2)
    actor.stop()
    state = actor.state(fresh=True)
    checks.append(("arc drive (mode back to idle)", state.mode == "idle" and not state.blocked))
    elapsed = time.monotonic() - started

    print(f"=== 用时 {elapsed:.1f}s，最终 {json.dumps(state.to_dict(), ensure_ascii=False)} ===", flush=True)
    for label, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}", flush=True)
    return 0 if all(ok for _, ok in checks) else 1


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--person", default=DEFAULT_PERSON_NAME, help=f"actor name (default {DEFAULT_PERSON_NAME})")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="AirSim RPC port (default: DRONE_AIRSIM_PORT or 41451)")
    parser.add_argument("--speed", type=float, default=WALK_SPEED, help="walk speed for one-shot commands")
    parser.add_argument("--tick-hz", type=float, default=40.0)
    parser.add_argument(
        "--gait",
        dest="gait",
        action="store_true",
        default=True,
        help="step-linked bob/sway/lean while moving (default on; it is cosmetic only)",
    )
    parser.add_argument("--no-gait", dest="gait", action="store_false", help="disable the gait cosmetics")
    parser.add_argument("--list", action="store_true", help="list person-like actors and exit")
    parser.add_argument("--state", action="store_true", help="print the current state and exit")
    parser.add_argument("--place", nargs=2, type=float, metavar=("X", "Y"))
    parser.add_argument("--yaw", type=float, default=None, help="heading for --place")
    parser.add_argument("--walk", nargs=2, type=float, metavar=("FWD", "RIGHT"))
    parser.add_argument("--goto", nargs=2, type=float, metavar=("X", "Y"))
    parser.add_argument("--turn", type=float, metavar="YAW")
    parser.add_argument("--patrol", metavar="\"x,y;x,y\"")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument(
        "--repl",
        action="store_true",
        help="line-command mode instead of raw keyboard (use it when the terminal cannot do single-key input)",
    )
    args = parser.parse_args()

    actor = PersonActor(
        args.person,
        ip=args.ip,
        port=args.port,
        tick_hz=args.tick_hz,
        cosmetic_walk=args.gait,
    )
    try:
        if args.list:
            names = actor.list_candidates()
            print("person-like actors:", names or "(none)")
            return 0 if names else 1
        actor.connect()
    except PersonActorError as exc:
        print(f"连接失败：{exc}\n提示：确认 Blocks/AirSim 已启动，且 settings.json 的 ApiServerPort 与 --port 一致。")
        return 2

    try:
        if args.state:
            _print_state(actor)
            return 0
        if args.place:
            state = actor.set_pose(args.place[0], args.place[1], yaw_deg=args.yaw)
            print(json.dumps(state.to_dict(), ensure_ascii=False))
            return 0
        if args.walk:
            ok = actor.walk(args.walk[0], args.walk[1], speed=args.speed)
            _print_state(actor, "walk  ")
            return 0 if ok else 1
        if args.goto:
            ok = actor.goto(args.goto[0], args.goto[1], speed=args.speed)
            _print_state(actor, "goto  ")
            return 0 if ok else 1
        if args.turn is not None:
            ok = actor.turn_to(args.turn)
            _print_state(actor, "turn  ")
            return 0 if ok else 1
        if args.patrol:
            legs = actor.patrol(_parse_waypoints(args.patrol), speed=args.speed)
            _print_state(actor, f"patrol({legs})  ")
            return 0
        if args.demo:
            return demo(actor, speed=args.speed)
        if args.repl:
            return _repl(actor)
        return teleop(actor)
    finally:
        actor.close()


if __name__ == "__main__":
    raise SystemExit(main())
