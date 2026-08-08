"""Smoke-test provider bridge for the Windows Agent runtime.

Run this in WSL to verify that the Agent can reach a provider bridge before a
real ROS obstacle node is wired in:

    python3 scripts/ros_provider_bridge_stub.py --host 0.0.0.0 --port 8766

Then set AIRSIM_AGENT_ROS_BRIDGE_URL=http://127.0.0.1:8766 in Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _obstacle_distance() -> float:
    try:
        return float(os.environ.get("OBSTACLE_DISTANCE_M", "8.0"))
    except ValueError:
        return 8.0


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ProviderHandler(BaseHTTPRequestHandler):
    server_version = "AirSimAgentProviderStub/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[provider] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        if self.path == "/health":
            _json_response(
                self,
                {
                    "ok": True,
                    "status": "ready",
                    "providers": ["obstacle"],
                    "message": "stub provider bridge is reachable",
                },
            )
            return
        _json_response(self, {"ok": False, "status": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            _json_response(self, {"ok": False, "status": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/providers/obstacle/summary":
            distance = _obstacle_distance()
            level = "clear" if distance >= 5.0 else ("caution" if distance >= 2.0 else "blocked")
            _json_response(
                self,
                {
                    "ok": True,
                    "status": "ok",
                    "data": {
                        "level": level,
                        "nearest_distance_m": distance,
                        "direction": "front",
                        "source": "stub",
                        "timestamp": time.time(),
                        "request": payload,
                    },
                },
            )
            return

        if self.path == "/providers/obstacle/validate_motion":
            distance = _obstacle_distance()
            motion = payload.get("motion") if isinstance(payload, dict) else {}
            forward_m = float((motion or {}).get("forward_m") or 0.0)
            safety_margin_m = 1.0
            safe = not (forward_m > 0 and distance < forward_m + safety_margin_m)
            _json_response(
                self,
                {
                    "ok": safe,
                    "status": "safe" if safe else "blocked",
                    "message": "motion accepted" if safe else "front obstacle inside safety margin",
                    "data": {
                        "nearest_distance_m": distance,
                        "safety_margin_m": safety_margin_m,
                        "source": "stub",
                        "request": payload,
                    },
                },
            )
            return

        _json_response(self, {"ok": False, "status": "not_found"}, HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ProviderHandler)
    print(f"provider bridge stub listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
