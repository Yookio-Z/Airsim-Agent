"""AirSim VLA agent runtime.

The in-process agent runtime (src.agent.AgentRuntime) is the single execution
surface: the web UI drives it directly, tools are registered through the local
ToolCollector, and the LLM plans/executes missions without any external MCP
process. Backend switching (AirSim / PX4 MAVLink / PX4 ROS2) happens through
the backend registry inside ToolRuntime.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
