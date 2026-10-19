"""仓库级路径常量。

运行期数据只有一个根：``.airsim_agent/``（Agent 的家，整目录被 gitignore）。
代码目录（``src/``）里不再存放任何运行数据。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_HOME = REPO_ROOT / ".airsim_agent"

# 飞行数据资产（遥测记录，只增不改，供离线分析/导出）。刻意不进 .airsim_agent/：
# "清空 Agent 家 = 失忆"的操作不能连带删掉飞行数据。
DATA_HOME = REPO_ROOT / "data"
