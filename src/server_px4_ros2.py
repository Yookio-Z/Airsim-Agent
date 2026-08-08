"""PX4 ROS2 Gateway MCP server.

This server exposes the same atomic flight tools as the MAVLink backend, but the
actual control path goes through the ROS Provider Gateway running in WSL or on
an onboard companion computer.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from src.modules.ros_gateway_controller import RosGatewayController
from src.tools.core import register_core_tools
from src.tools.providers import register_provider_tools

mcp = FastMCP(
    "PX4 ROS2 Gateway MCP Server",
    instructions=(
        "PX4 ROS2 backend through the AirSim Agent ROS Provider Gateway.\n\n"
        "Run the ROS gateway in WSL or onboard first, then set "
        "AIRSIM_AGENT_ROS_BRIDGE_URL or DRONE_ROS_BRIDGE_URL.\n\n"
        "Recommended simple workflow:\n"
        "1. drone_connect()\n"
        "2. drone_arm()\n"
        "3. drone_takeoff(altitude=5)\n"
        "4. drone_fly_to(x=10, y=0, z=-5)\n"
        "5. drone_hover() or drone_land()\n"
    ),
)

_controller = RosGatewayController()


def _fmt_result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


register_core_tools(mcp, _controller, _fmt_result)
register_provider_tools(mcp, _controller, _fmt_result)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
