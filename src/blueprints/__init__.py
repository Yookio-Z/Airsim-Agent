"""
Blueprint 模块化架构
参考 dimOS 的 Blueprint 组合模式

每个 Blueprint 是一组模块的组合，可以独立运行或叠加使用。
"""

from .basic import BasicBlueprint
from .agentic import AgenticBlueprint

__all__ = ["BasicBlueprint", "AgenticBlueprint"]
