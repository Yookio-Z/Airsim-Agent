"""PX4 Mission 端到端测试脚本。

默认非侵入式，仅在显式传入 `--execute` / `--clear` / `--restore` 时
才执行侵入式操作。所有侵入式操作前会打印明确警告，需要 `--yes` 二次确认
（除非显式 `--yes` 跳过）。

默认（非侵入式）流程:
  1. drone_connect
  2. drone_get_status
  3. drone_download_mission  → 保存原 mission 到本地（用于 --restore）
  4. drone_get_mission_progress

`--execute` 流程（在默认流程之上）:
  5. drone_upload_mission   → 上传测试 mission（takeoff 3m + 2 个航点 + RTL）
  6. drone_arm              → 解锁电机
  7. drone_start_mission    → 启动任务
  8. 循环 drone_get_mission_progress 监控执行进度（最多 60s）

`--clear` 流程:
  - drone_clear_mission      → 清空飞控 mission（不可恢复）

`--restore` 流程:
  - drone_upload_mission     → 把第 3 步保存的原 mission 重新上传回去

使用:
  python scripts/px4_mission_smoke.py                      # 非侵入式
  python scripts/px4_mission_smoke.py --execute            # 真实上传+启动
  python scripts/px4_mission_smoke.py --execute --yes      # 跳过确认
  python scripts/px4_mission_smoke.py --clear              # 清空飞控 mission
  python scripts/px4_mission_smoke.py --restore            # 恢复原 mission
  python scripts/px4_mission_smoke.py --execute --restore  # 执行后恢复
  python scripts/px4_mission_smoke.py --mission-alt 5      # 自定义测试 mission 高度

退出码:
  0   全部步骤通过
  1   至少一项失败
  2   环境错误（UI server 不可达 / 未连接 / 参数冲突）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_CONNECTION_URL = os.environ.get(
    "DRONE_PX4_CONNECTION_STRING",
    "udp:127.0.0.1:14550",
)
DEFAULT_MONITOR_TIMEOUT = 60.0   # 监控 mission 执行的最大时长（秒）
DEFAULT_MONITOR_INTERVAL = 1.0   # 每次查询 progress 的间隔

# AirSim PX4 SITL 默认 home（北京），用于测试 mission 的航点坐标。
# 真实 PX4 SITL 中该值会被飞控的 home position 覆盖，这里只是 fallback。
DEFAULT_HOME_LAT = 39.9042
DEFAULT_HOME_LON = 116.4074


# ─────────────────────────── HTTP / API 工具 ────────────────────────────

def call_tool(base_url: str, tool: str, params: dict[str, Any] | None = None,
              timeout: float = 15.0) -> dict[str, Any]:
    """通过 /api/tool 调用 UI server 工具。返回原始响应 dict。"""
    payload = json.dumps({"tool": tool, "params": params or {}, "dry_run": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/tool",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tool {tool}: 响应不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"tool {tool}: 响应不是 JSON 对象")
    return data


def unwrap(data: dict[str, Any], tool: str) -> dict[str, Any]:
    """解析 /api/tool 响应。实际响应结构为：
       {ok: bool, result: {tool, params, ok: bool, data: {...}, duration_ms, safety}}

    返回 result.data（工具实际返回的数据）。若任一层 ok=false，抛 RuntimeError。
    """
    if not data.get("ok", False):
        err = data.get("error") or data.get("message") or "server returned ok=false"
        raise RuntimeError(f"server error from {tool}: {err}")
    result = data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{tool}: response missing 'result' object")
    if not result.get("ok", False):
        inner = result.get("data") or {}
        err = inner.get("message") or inner.get("error") or "tool returned ok=false"
        raise RuntimeError(f"{tool} failed: {err}")
    tool_data = result.get("data")
    if not isinstance(tool_data, dict):
        return {k: v for k, v in result.items() if k not in ("tool", "params", "ok", "duration_ms", "safety")}
    return tool_data


def check_server_alive(base_url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/state", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ─────────────────────────── 测试 mission 构造 ────────────────────────────

def build_test_mission(home_lat: float, home_lon: float, alt_m: float) -> list[dict[str, Any]]:
    """构造小范围测试 mission:
      - takeoff 3m
      - waypoint 5m north
      - waypoint 5m north, 5m east
      - RTL

    使用 backend-neutral MissionItem schema:
      {id, type, frame, lat, lon, alt_m, speed_mps, hold_s, acceptance_radius_m, actions, metadata}
    """
    # 1m 纬度 ≈ 111320 m；1m 经度 ≈ 111320 * cos(lat) m
    dlat_5m = 5.0 / 111320.0
    dlon_5m = 5.0 / (111320.0 * max(0.001, abs(__import__("math").cos(__import__("math").radians(home_lat)))))

    return [
        {
            "id": "wp_takeoff",
            "type": "takeoff",
            "frame": "global_relative_alt",
            "lat": home_lat,
            "lon": home_lon,
            "alt_m": float(alt_m),
            "speed_mps": 0.0,
            "hold_s": 0.0,
            "acceptance_radius_m": 2.0,
            "actions": [],
            "metadata": {"mav_command": "MAV_CMD_NAV_TAKEOFF"},
        },
        {
            "id": "wp_1",
            "type": "waypoint",
            "frame": "global_relative_alt",
            "lat": home_lat + dlat_5m,
            "lon": home_lon,
            "alt_m": float(alt_m),
            "speed_mps": 2.0,
            "hold_s": 1.0,
            "acceptance_radius_m": 2.0,
            "actions": [],
            "metadata": {"mav_command": "MAV_CMD_NAV_WAYPOINT"},
        },
        {
            "id": "wp_2",
            "type": "waypoint",
            "frame": "global_relative_alt",
            "lat": home_lat + dlat_5m,
            "lon": home_lon + dlon_5m,
            "alt_m": float(alt_m),
            "speed_mps": 2.0,
            "hold_s": 1.0,
            "acceptance_radius_m": 2.0,
            "actions": [],
            "metadata": {"mav_command": "MAV_CMD_NAV_WAYPOINT"},
        },
        {
            "id": "wp_rtl",
            "type": "rtl",
            "frame": "global_relative_alt",
            "lat": home_lat,
            "lon": home_lon,
            "alt_m": 0.0,
            "speed_mps": 0.0,
            "hold_s": 0.0,
            "acceptance_radius_m": 2.0,
            "actions": [],
            "metadata": {"mav_command": "MAV_CMD_NAV_RETURN_TO_LAUNCH"},
        },
    ]


# ─────────────────────────── 步骤实现 ────────────────────────────

class StepResult:
    def __init__(self, name: str, ok: bool, msg: str, data: Any = None):
        self.name = name
        self.ok = ok
        self.msg = msg
        self.data = data

    def __repr__(self) -> str:
        flag = "PASS" if self.ok else "FAIL"
        return f"[{flag}] {self.name}: {self.msg}"


def step_connect(base_url: str, connection_url: str, timeout: float) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_connect",
                                params={"url": connection_url}, timeout=timeout),
                      "drone_connect")
        elapsed = time.time() - t0
        connected = bool(data.get("connected", False))
        mode = data.get("mode", "?")
        armed = data.get("armed", False)
        msg = f"connected={connected} mode={mode} armed={armed} ({elapsed:.2f}s)"
        return StepResult("drone_connect", connected, msg, data)
    except Exception as exc:
        return StepResult("drone_connect", False, f"异常: {exc}")


def step_get_status(base_url: str, timeout: float) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_get_status", timeout=timeout),
                      "drone_get_status")
        elapsed = time.time() - t0
        mode = data.get("mode", "?")
        armed = data.get("armed", False)
        gps = data.get("gps") or data.get("global_position") or {}
        position = data.get("position", {})
        msg = (f"mode={mode} armed={armed} "
               f"ned=({position.get('x', 0):.2f},{position.get('y', 0):.2f},{position.get('z', 0):.2f}) "
               f"gps=({gps.get('lat', 0):.6f},{gps.get('lon', 0):.6f}) ({elapsed:.2f}s)")
        return StepResult("drone_get_status", True, msg, data)
    except Exception as exc:
        return StepResult("drone_get_status", False, f"异常: {exc}")


def step_download_mission(base_url: str, timeout: float) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_download_mission", timeout=timeout),
                      "drone_download_mission")
        elapsed = time.time() - t0
        items = data.get("items") or data.get("waypoints") or []
        if isinstance(items, dict):
            items = items.get("items", [])
        count = len(items) if isinstance(items, list) else 0
        status = data.get("status", "ok")
        msg = f"downloaded {count} items, status={status} ({elapsed:.2f}s)"
        return StepResult("drone_download_mission", True, msg, data)
    except Exception as exc:
        return StepResult("drone_download_mission", False, f"异常: {exc}")


def step_get_mission_progress(base_url: str, timeout: float) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_get_mission_progress", timeout=timeout),
                      "drone_get_mission_progress")
        elapsed = time.time() - t0
        current_seq = data.get("current_seq")
        total = data.get("total")
        reached = data.get("reached_seq")
        running = data.get("running", False)
        msg = (f"current_seq={current_seq} reached_seq={reached} "
               f"total={total} running={running} ({elapsed:.2f}s)")
        return StepResult("drone_get_mission_progress", True, msg, data)
    except Exception as exc:
        return StepResult("drone_get_mission_progress", False, f"异常: {exc}")


def step_upload_mission(base_url: str, mission_items: list[dict[str, Any]],
                        timeout: float = 30.0) -> StepResult:
    t0 = time.time()
    try:
        payload_json = json.dumps({"items": mission_items}, ensure_ascii=False)
        data = unwrap(call_tool(base_url, "drone_upload_mission",
                                params={"waypoints_json": payload_json}, timeout=timeout),
                      "drone_upload_mission")
        elapsed = time.time() - t0
        status = data.get("status", "?")
        sent = data.get("sent_count", "?")
        total = data.get("count", len(mission_items))
        ack = data.get("ack", "?")
        msg = f"status={status} sent={sent}/{total} ack={ack} ({elapsed:.2f}s)"
        ok = status in ("ok", "accepted", "success") or (status != "error" and ack == "MAV_MISSION_ACCEPTED")
        return StepResult("drone_upload_mission", ok, msg, data)
    except Exception as exc:
        return StepResult("drone_upload_mission", False, f"异常: {exc}")


def step_arm(base_url: str, timeout: float = 15.0) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_arm", timeout=timeout), "drone_arm")
        elapsed = time.time() - t0
        status = data.get("status", "?")
        msg_text = data.get("message", "")
        ok = status == "ok"
        msg = f"status={status} message={msg_text} ({elapsed:.2f}s)"
        return StepResult("drone_arm", ok, msg, data)
    except Exception as exc:
        return StepResult("drone_arm", False, f"异常: {exc}")


def step_start_mission(base_url: str, timeout: float = 15.0) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_start_mission", timeout=timeout),
                      "drone_start_mission")
        elapsed = time.time() - t0
        status = data.get("status", "?")
        msg_text = data.get("message", "")
        ok = status in ("ok", "accepted", "success")
        msg = f"status={status} message={msg_text} ({elapsed:.2f}s)"
        return StepResult("drone_start_mission", ok, msg, data)
    except Exception as exc:
        return StepResult("drone_start_mission", False, f"异常: {exc}")


def step_clear_mission(base_url: str, timeout: float = 15.0) -> StepResult:
    t0 = time.time()
    try:
        data = unwrap(call_tool(base_url, "drone_clear_mission", timeout=timeout),
                      "drone_clear_mission")
        elapsed = time.time() - t0
        status = data.get("status", "?")
        msg_text = data.get("message", "")
        ok = status in ("ok", "accepted", "success")
        msg = f"status={status} message={msg_text} ({elapsed:.2f}s)"
        return StepResult("drone_clear_mission", ok, msg, data)
    except Exception as exc:
        return StepResult("drone_clear_mission", False, f"异常: {exc}")


def monitor_mission_progress(base_url: str, monitor_timeout: float,
                              interval: float, timeout: float) -> StepResult:
    """循环查询 mission progress，直到任务完成或超时。"""
    print(f"\n  监控 mission 执行进度 (最长 {monitor_timeout:.0f}s, 间隔 {interval:.1f}s)")
    t_start = time.time()
    last_seq = None
    last_total = None
    reached_final = False

    while time.time() - t_start < monitor_timeout:
        try:
            data = unwrap(call_tool(base_url, "drone_get_mission_progress", timeout=timeout),
                          "drone_get_mission_progress")
        except Exception as exc:
            print(f"  [warn] 查询 progress 失败: {exc}")
            time.sleep(interval)
            continue

        current_seq = data.get("current_seq")
        total = data.get("total")
        reached = data.get("reached_seq")
        running = data.get("running", False)
        elapsed = time.time() - t_start

        if current_seq != last_seq or total != last_total:
            print(f"  [{elapsed:5.1f}s] current_seq={current_seq} reached={reached} "
                  f"total={total} running={running}")
            last_seq = current_seq
            last_total = total

        # 判断完成：reached_seq 等于 total-1，或 running=False 且 total>0
        if total is not None and reached is not None and reached >= total - 1:
            print(f"  [{elapsed:5.1f}s] mission 已到达最后一个航点 (reached={reached})")
            reached_final = True
            break
        if not running and total is not None and total > 0 and elapsed > 5.0:
            # 5s 后还在 running=False，认为任务已停止
            print(f"  [{elapsed:5.1f}s] running=False, 认为任务已停止")
            break

        time.sleep(interval)

    elapsed = time.time() - t_start
    if reached_final:
        msg = f"mission 完成于 {elapsed:.1f}s, final_seq={last_seq}"
        return StepResult("monitor_mission", True, msg)
    if last_seq is not None:
        msg = f"monitor 超时于 {elapsed:.1f}s, last_seq={last_seq} total={last_total}"
        return StepResult("monitor_mission", False, msg)
    msg = f"monitor 超时于 {elapsed:.1f}s, 未收到任何 progress"
    return StepResult("monitor_mission", False, msg)


# ─────────────────────────── 主流程 ────────────────────────────

def print_step_result(r: StepResult) -> None:
    flag = "OK  " if r.ok else "FAIL"
    print(f"  [{flag}] {r.name:30s} {r.msg}")


def confirm(action: str, skip_confirm: bool) -> bool:
    """侵入式操作前的二次确认。"""
    if skip_confirm:
        return True
    print()
    print("=" * 60)
    print(f"  即将执行侵入式操作: {action}")
    print("=" * 60)
    try:
        answer = input("  确认执行? 输入 'yes' 继续, 其他取消: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("yes", "y", "ok")


def run(args: argparse.Namespace) -> int:
    print("=" * 60)
    print("PX4 Mission 端到端测试")
    print("=" * 60)
    print(f"  UI server       : {args.base_url}")
    print(f"  PX4 connect     : {args.connection_url}")
    print(f"  execute         : {args.execute}")
    print(f"  clear           : {args.clear}")
    print(f"  restore         : {args.restore}")
    print(f"  mission alt     : {args.mission_alt} m")
    print(f"  monitor timeout : {args.monitor_timeout}s")

    if not check_server_alive(args.base_url):
        print(f"\n[FAIL] UI server 不可达: {args.base_url}")
        print("  请先启动: AIRSIM_AGENT_BACKEND=px4_mavlink airsim-agent-ui")
        return 2

    results: list[StepResult] = []
    saved_mission: list[dict[str, Any]] | None = None
    home_lat = DEFAULT_HOME_LAT
    home_lon = DEFAULT_HOME_LON

    # Step 1: connect
    print("\n--- Step 1: drone_connect ---")
    r = step_connect(args.base_url, args.connection_url, args.timeout)
    print_step_result(r)
    results.append(r)
    if not r.ok:
        print("\n连接失败，后续步骤无法执行。")
        return _summarize(results)
    # 从连接结果中提取 home position
    if r.data:
        home = r.data.get("home_position") or r.data.get("home") or {}
        if isinstance(home, dict):
            if home.get("lat") is not None:
                home_lat = float(home["lat"])
            if home.get("lon") is not None:
                home_lon = float(home["lon"])

    # Step 2: get_status
    print("\n--- Step 2: drone_get_status ---")
    r = step_get_status(args.base_url, args.timeout)
    print_step_result(r)
    results.append(r)
    # 从状态中提取 GPS 作为更准确的 home
    if r.ok and r.data:
        gps = r.data.get("gps") or r.data.get("global_position") or {}
        if gps.get("lat") and abs(gps.get("lat", 0)) > 0.001:
            home_lat = float(gps["lat"])
            home_lon = float(gps["lon"])

    # Step 3: download_mission (保存原 mission 用于 --restore)
    print("\n--- Step 3: drone_download_mission (保存原 mission) ---")
    r = step_download_mission(args.base_url, args.timeout)
    print_step_result(r)
    results.append(r)
    if r.ok and r.data:
        items = r.data.get("items") or r.data.get("waypoints") or []
        if isinstance(items, list) and items:
            saved_mission = items
            print(f"  → 已保存原 mission ({len(items)} 个 items) 用于 --restore")

    # Step 4: get_mission_progress
    print("\n--- Step 4: drone_get_mission_progress ---")
    r = step_get_mission_progress(args.base_url, args.timeout)
    print_step_result(r)
    results.append(r)

    # Step 5+: execute (侵入式)
    if args.execute:
        print("\n" + "=" * 60)
        print("  侵入式模式: --execute")
        print("=" * 60)

        if not confirm("upload test mission + arm + start_mission", args.yes):
            print("  已取消 execute。")
        else:
            # 5a. upload
            print("\n--- Step 5a: drone_upload_mission (测试 mission) ---")
            test_mission = build_test_mission(home_lat, home_lon, args.mission_alt)
            print(f"  测试 mission: {len(test_mission)} items, "
                  f"home=({home_lat:.6f},{home_lon:.6f}), alt={args.mission_alt}m")
            r = step_upload_mission(args.base_url, test_mission, timeout=30.0)
            print_step_result(r)
            results.append(r)
            if not r.ok:
                print("  上传失败，跳过 arm/start。")
            else:
                # 5b. arm
                print("\n--- Step 5b: drone_arm ---")
                r = step_arm(args.base_url, timeout=15.0)
                print_step_result(r)
                results.append(r)

                # 5c. start_mission
                print("\n--- Step 5c: drone_start_mission ---")
                r = step_start_mission(args.base_url, timeout=15.0)
                print_step_result(r)
                results.append(r)

                if r.ok:
                    # 5d. monitor
                    print("\n--- Step 5d: monitor mission progress ---")
                    r = monitor_mission_progress(args.base_url,
                                                 args.monitor_timeout,
                                                 DEFAULT_MONITOR_INTERVAL,
                                                 args.timeout)
                    print_step_result(r)
                    results.append(r)

    # Step 6: clear (侵入式)
    if args.clear:
        print("\n" + "=" * 60)
        print("  侵入式模式: --clear")
        print("=" * 60)
        if not confirm("clear mission on vehicle (不可恢复)", args.yes):
            print("  已取消 clear。")
        else:
            print("\n--- Step 6: drone_clear_mission ---")
            r = step_clear_mission(args.base_url, timeout=15.0)
            print_step_result(r)
            results.append(r)

    # Step 7: restore (侵入式)
    if args.restore:
        print("\n" + "=" * 60)
        print("  侵入式模式: --restore")
        print("=" * 60)
        if saved_mission is None:
            print("  [SKIP] 没有保存原 mission（下载失败或飞控无 mission），跳过 restore")
        elif not confirm("restore original mission to vehicle", args.yes):
            print("  已取消 restore。")
        else:
            print(f"\n--- Step 7: drone_upload_mission (恢复原 mission: {len(saved_mission)} items) ---")
            r = step_upload_mission(args.base_url, saved_mission, timeout=30.0)
            print_step_result(r)
            results.append(r)

    return _summarize(results)


def _summarize(results: list[StepResult]) -> int:
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for r in results:
        print_step_result(r)
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    print(f"\n结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PX4 Mission 端到端测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"UI server 地址 (默认: {DEFAULT_BASE_URL})")
    parser.add_argument("--connection-url", default=DEFAULT_CONNECTION_URL,
                        help=f"PX4 连接字符串 (默认: {DEFAULT_CONNECTION_URL})")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="单步 HTTP 调用超时秒数 (默认: 15)")
    parser.add_argument("--execute", action="store_true",
                        help="执行侵入式测试: upload + arm + start_mission + monitor")
    parser.add_argument("--clear", action="store_true",
                        help="清空飞控 mission（不可恢复）")
    parser.add_argument("--restore", action="store_true",
                        help="恢复测试前下载的原 mission")
    parser.add_argument("--yes", action="store_true",
                        help="跳过侵入式操作的二次确认（用于 CI/自动化）")
    parser.add_argument("--mission-alt", type=float, default=3.0,
                        help="测试 mission 起飞高度 m (默认: 3.0)")
    parser.add_argument("--monitor-timeout", type=float, default=DEFAULT_MONITOR_TIMEOUT,
                        help=f"monitor mission 执行的最长秒数 (默认: {DEFAULT_MONITOR_TIMEOUT})")
    args = parser.parse_args()

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
