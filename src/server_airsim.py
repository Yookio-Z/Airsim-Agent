"""AirSim-only MCP server.

This server exposes simulation flight control plus AirSim camera/perception
tools. It does not require PX4 or MAVLink.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from src.modules.airsim_controller import AirSimController
from src.tools.core import register_core_tools
from src.tools.perception import register_perception_tools
from src.tools.vision import register_vision_tools

mcp = FastMCP(
    "AirSim MCP Server",
    instructions=(
        "AirSim simulation backend for UAV control and camera perception.\n\n"
        "Coordinate frame: local NED. X is north, Y is east, Z is down. "
        "Negative Z means altitude above the local origin.\n\n"
        "Recommended simple workflow:\n"
        "1. drone_connect(ip='127.0.0.1', port=41452)\n"
        "2. drone_arm()\n"
        "3. drone_takeoff(altitude=3)\n"
        "4. drone_fly_to(...) or drone_move_relative(...)\n"
        "5. drone_hover() or drone_land()\n\n"
        "Keep MCP tools atomic. Search, tracking, patrol, and formation "
        "behaviors should be implemented as skills or provider-backed services, "
        "not hardcoded workflow tools."
    ),
)

_controller = AirSimController()


def _fmt_result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


register_core_tools(mcp, _controller, _fmt_result)
register_perception_tools(mcp, _controller, _fmt_result)
register_vision_tools(mcp, _controller, _fmt_result)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
