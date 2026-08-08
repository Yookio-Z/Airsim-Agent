"""
Agentic Blueprint - LLM Agent 控制层
在 BasicBlueprint 基础上添加 MCP 工具暴露
"""

from __future__ import annotations

from dataclasses import dataclass

from .basic import BasicBlueprint


@dataclass
class AgenticBlueprint(BasicBlueprint):
    """Agentic 蓝图：基础连接 + MCP 工具暴露

    用法:
        bp = AgenticBlueprint()
        with bp:
            # AirSim 和 MAVLink 已自动连接
            # 启动 MCP Server
            from src.server import mcp
            mcp.run()
    """

    mcp_port: int = 9990

    def run_mcp_server(self) -> None:
        """启动 MCP Server"""
        from src.server import mcp

        mcp.run()
