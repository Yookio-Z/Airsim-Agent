"""
AirSim MCP - 通过MCP协议控制AirSim仿真环境的Python库

两个独立入口:
  - airsim-mcp-airsim: 纯 AirSim 仿真模式
  - airsim-mcp-px4: PX4 真机/SITL 模式
"""

__version__ = "0.2.0"

def main():
    """Hermes 默认入口 - 纯 AirSim 仿真模式.

    Keep the AirSim server import lazy so lightweight modules such as the web UI
    can start even when the simulator-specific runtime is not loaded yet.
    """
    from .server_airsim import main as server_main

    return server_main()

__all__ = ["main", "__version__"]
