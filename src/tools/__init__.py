"""MCP tool registration groups.

Tool groups are loaded lazily so optional AirSim/PX4/perception dependencies do
not block unrelated backends. Keep this package focused on atomic tool
registration. Higher-level workflows belong in markdown skills and provider
implementations.
"""

__all__ = [
    "register_core_tools",
    "register_perception_tools",
    "register_vision_tools",
    "register_provider_tools",
]


def __getattr__(name: str):
    """Load optional tool groups only when the active backend needs them."""
    if name == "register_core_tools":
        from .core import register_core_tools

        return register_core_tools
    if name == "register_perception_tools":
        from .perception import register_perception_tools

        return register_perception_tools
    if name == "register_vision_tools":
        from .vision import register_vision_tools

        return register_vision_tools
    if name == "register_provider_tools":
        from .providers import register_provider_tools

        return register_provider_tools
    raise AttributeError(name)
