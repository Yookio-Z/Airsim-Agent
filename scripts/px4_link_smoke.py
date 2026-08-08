"""PX4 链路冒烟测试 — 非侵入式。

默认执行三项只读检查：
  1. drone_connect
  2. drone_get_status
  3. drone_get_mission_progress

不会 arm、不会改 mode、不会改 mission。便于在开发阶段快速验证
PX4 SITL <-> MAVLink <-> UI server <-> 工具链路是否通畅。

使用:
  python scripts/px4_link_smoke.py
  python scripts/px4_link_smoke.py --base-url http://127.0.0.1:8765
  python scripts/px4_link_smoke.py --connection-url udp:127.0.0.1:14540
  python scripts/px4_link_smoke.py --timeout 8

退出码:
  0  全部通过
  1  至少一项失败
  2  UI server 不可达 / 环境错误
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


def call_tool(base_url: str, tool: str, params: dict[str, Any] | None = None,
              timeout: float = 10.0) -> dict[str, Any]:
    """通过 /api/tool 调用 UI server 工具。

    返回解析后的 JSON dict。失败时抛 urllib.error.URLError 或 RuntimeError。
    """
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


class ToolError(RuntimeError):
    """工具执行失败（result.ok=false）。"""

    def __init__(self, tool: str, message: str):
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.message = message


def check_server_alive(base_url: str, timeout: float = 3.0) -> bool:
    """探测 UI server 是否在线（GET /api/state）。"""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/state", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def run_smoke(base_url: str, connection_url: str, timeout: float) -> int:
    print("=" * 60)
    print("PX4 链路冒烟测试（非侵入式）")
    print("=" * 60)
    print(f"  UI server   : {base_url}")
    print(f"  PX4 connect : {connection_url}")
    print(f"  timeout     : {timeout}s")

    if not check_server_alive(base_url):
        print(f"\n[FAIL] UI server 不可达: {base_url}")
        print("  请先启动: airsim-agent-ui (或 AIRSIM_AGENT_BACKEND=px4_mavlink airsim-agent-ui)")
        return 2

    results: list[tuple[str, bool, str]] = []

    # Step 1: connect
    print("\n--- Step 1/3: drone_connect ---")
    t0 = time.time()
    try:
        data = call_tool(base_url, "drone_connect",
                         params={"url": connection_url}, timeout=timeout)
        elapsed = time.time() - t0
        info = _unwrap_result(data)
        connected = bool(info.get("connected", False))
        backend = info.get("backend", "?")
        mode = info.get("mode", "?")
        armed = info.get("armed", False)
        msg = f"backend={backend} mode={mode} armed={armed} ({elapsed:.2f}s)"
        if connected:
            print(f"  [OK]  {msg}")
            results.append(("drone_connect", True, msg))
        else:
            err = info.get("message", "")
            print(f"  [FAIL] {msg}" + (f" message={err}" if err else ""))
            results.append(("drone_connect", False, msg + (f" message={err}" if err else "")))
    except Exception as exc:
        print(f"  [FAIL] drone_connect 异常: {exc}")
        results.append(("drone_connect", False, str(exc)))

    # Step 2: get_status
    print("\n--- Step 2/3: drone_get_status ---")
    t0 = time.time()
    try:
        data = call_tool(base_url, "drone_get_status", timeout=timeout)
        elapsed = time.time() - t0
        # server 返回 {ok: true, result: "<json string>"} 或 {ok: true, ...fields}
        status = _unwrap_result(data)
        backend = status.get("backend", "?")
        mode = status.get("mode", "?")
        armed = status.get("armed", False)
        position = status.get("position", {})
        gps = status.get("gps") or status.get("global_position") or {}
        msg = (f"backend={backend} mode={mode} armed={armed} "
               f"ned=({position.get('x', 0):.2f},{position.get('y', 0):.2f},{position.get('z', 0):.2f}) "
               f"gps=({gps.get('lat', 0):.6f},{gps.get('lon', 0):.6f}) ({elapsed:.2f}s)")
        print(f"  [OK]  {msg}")
        results.append(("drone_get_status", True, msg))
    except Exception as exc:
        print(f"  [FAIL] drone_get_status 异常: {exc}")
        results.append(("drone_get_status", False, str(exc)))

    # Step 3: get_mission_progress
    print("\n--- Step 3/3: drone_get_mission_progress ---")
    t0 = time.time()
    try:
        data = call_tool(base_url, "drone_get_mission_progress", timeout=timeout)
        elapsed = time.time() - t0
        progress = _unwrap_result(data)
        current_seq = progress.get("current_seq")
        total = progress.get("total")
        reached = progress.get("reached_seq")
        running = progress.get("running", False)
        msg = (f"current_seq={current_seq} reached_seq={reached} "
               f"total={total} running={running} ({elapsed:.2f}s)")
        print(f"  [OK]  {msg}")
        results.append(("drone_get_mission_progress", True, msg))
    except Exception as exc:
        print(f"  [FAIL] drone_get_mission_progress 异常: {exc}")
        results.append(("drone_get_mission_progress", False, str(exc)))

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total_tests = len(results)
    for name, ok, msg in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:30s} {msg}")
    print(f"\n结果: {passed}/{total_tests} 通过")

    return 0 if passed == total_tests else 1


def _unwrap_result(data: dict[str, Any]) -> dict[str, Any]:
    """解析 /api/tool 响应。实际响应结构为：
       {ok: bool, result: {tool, params, ok: bool, data: {...}, duration_ms, safety}}

    返回 result.data（工具实际返回的数据）。若任一层 ok=false，抛 ToolError。
    """
    if not data.get("ok", False):
        err = data.get("error") or data.get("message") or "server returned ok=false"
        raise RuntimeError(f"server error: {err}")
    result = data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("response missing 'result' object")
    if not result.get("ok", False):
        # 工具执行失败：从 result.data.message 提取错误信息
        inner = result.get("data") or {}
        err = inner.get("message") or inner.get("error") or "tool returned ok=false"
        raise ToolError(result.get("tool", "?"), err)
    tool_data = result.get("data")
    if not isinstance(tool_data, dict):
        # 兜底：返回 result 自身去掉元字段
        return {k: v for k, v in result.items() if k not in ("tool", "params", "ok", "duration_ms", "safety")}
    return tool_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PX4 链路冒烟测试（非侵入式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"UI server 地址 (默认: {DEFAULT_BASE_URL})")
    parser.add_argument("--connection-url", default=DEFAULT_CONNECTION_URL,
                        help=f"PX4 连接字符串 (默认: {DEFAULT_CONNECTION_URL})")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="每步超时秒数 (默认: 10)")
    args = parser.parse_args()

    return run_smoke(args.base_url, args.connection_url, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
