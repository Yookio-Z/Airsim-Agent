"""Compatibility entry point for the AirSim MCP server.

Prefer `python -m src.server_airsim` for AirSim simulation and
`python -m src.server_px4` for PX4/MAVLink. This module remains as a
legacy alias so older launch commands keep working.
"""

from __future__ import annotations

from src.server_airsim import main, mcp

__all__ = ["mcp", "main"]


if __name__ == "__main__":
    main()
