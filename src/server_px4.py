"""PX4 MAVLink MCP server.

This server exposes the PX4/SITL control surface through pymavlink. It does not
provide AirSim camera tools.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from src.modules.mavlink_controller import MavlinkController
from src.tools.core import register_core_tools

mcp = FastMCP(
    "PX4 MAVLink MCP Server",
    instructions=(
        "PX4 MAVLink backend for SITL or vehicle control.\n\n"
        "Coordinate frame: local NED. X is north, Y is east, Z is down. "
        "Negative Z means altitude above the local origin.\n\n"
        "Recommended simple workflow:\n"
        "1. drone_connect(url='udp:127.0.0.1:14550')\n"
        "2. drone_arm()\n"
        "3. drone_set_mode(mode='OFFBOARD') when the backend requires it\n"
        "4. drone_takeoff(altitude=5)\n"
        "5. drone_fly_to(x=10, y=0, z=-5)\n"
        "6. drone_land() or drone_set_mode(mode='RTL')\n\n"
        "This backend has no native image capture. Visual skills must use a "
        "separate camera provider or ROS/AirSim camera source adapted into the "
        "image_source surface."
    ),
)

_controller = MavlinkController()


def _fmt_result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


register_core_tools(mcp, _controller, _fmt_result)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
