# AGENTS.md

> 这份文件给两类读者：维护本项目的开发者，以及 AI 编码代理（ZCode / Codex 等会自动
> 读取仓库根目录的 `AGENTS.md`）。目标只有一件事：**目录职责、入库边界、改动纪律，
> 一处说清**，人和代理都照这个来。

## 项目一句话

自然语言任务 → LLM 规划 + **判据契约**（完成标准由任务声明、运行时只评估）→
原子工具执行。飞行后端以 **PX4 SITL（px4_mavlink）为主线**，AirSim 可用于纯仿真；
**px4_ros2 链路已冻结**（机载端要额外起 ROS 服务，暂不走这条路）。感知路线是
**「视觉模型给语义 + 深度给距离」**，不使用目标检测器（2026-10-12 决议）。

## 一、目录地图

### 入库（推送到 GitHub：`Yookio-Z/Airsim-Agent`）

| 路径 | 职责 | 说明 |
|---|---|---|
| `src/` | 产品代码 | agent 运行时、原子工具、感知轴、编队控制器、GCS 服务、Web UI |
| `.airsim_agent/` | **Agent 的家** | 手册入库：`SYSTEM.md`（常驻系统提示词）、`NOTES.md`（长期笔记，随 SYSTEM.md 每轮注入）、`skills/<名>/SKILL.md`（技能）、`README.md`；运行数据不入库（见下） |
| `tests/` | 测试套件 | 60 个 pytest 文件 + 3 个前端 `.cjs` 套件；**CI 每次 push 都跑它们**，不要移除 |
| `ros2/` | px4_ros2 后端网关节点 | **冻结**：保留入库、不再维护；不要基于它做新功能 |
| `.github/` | CI 质量门 | push/PR 时跑 pytest + 覆盖率门槛（fail_under 45）+ 前端测试，blocking；不要删 |
| 根文件 | `README.md`、`pyproject.toml`、`uv.lock`、`.coveragerc`、`.gitignore`、`AGENTS.md` | 说明、依赖、锁、覆盖率门槛、忽略规则 |

**`src/tools/` 文件地图**（一个文件一个职责域；注册统一由 `src/agent/tool_executor.py`
的 `ToolRuntime.ensure_ready()` 做，工具文件只依赖 `FlightController`/感知轴，绝不反向
import agent 层）：

| 文件 | 注册什么 | 模型可见 |
|---|---|---|
| `manifest.py` | 工具清单：名称/类别/能力/可见性依据（零依赖，契约测试的锚） | — |
| `flight.py` | 飞行工具组：`drone_*` 链路/遥测/飞行指令（后端中立） | 是 |
| `mission.py` | 任务工具组：上传/下载/清空/启动/进度 | 否（GCS 专用） |
| `sensors.py` | AirSim 仿真传感器（`airsim_get_sensors`）+ captures 目录助手 | 是（仅 airsim） |
| `perception_axis.py` | 感知轴三件套：`perception_status` / `inspect_current_frame` / `locate_image_point` | 是（非真机） |
| `providers.py` | ROS2 provider 桥（冻结期，整体可裁） | 是（仅 px4_ros2） |

> **`.airsim_agent/` 的结构对照 pi 项目的 `.pi/`：一个「Agent 的家」，手册入库、数据
> 不入库。** gitignore 规则是"忽略全部、白名单三样"——新放进来的数据文件默认不会
> 被提交。结构历史：系统提示词 `config/agent_system.md` → 本目录 `SYSTEM.md`；
> 技能 `skills/` → 本目录 `skills/`（2026-10-14）。

### 本机专属（被 `.gitignore` 挡在版本库外，不推 GitHub）

| 路径 | 是什么 | 说明 |
|---|---|---|
| `.airsim_agent/` 内的数据 | Agent 自身的运行数据 | `memory.json`、`sessions/`（界面会话）、`session_memory/`、`task_runs/`（最多 80 条）、`runs/`（最多 200 个）、`attachments/`、`settings.json`、`models.json`、`secrets.json`（**绝不提交**）；清空 = 失忆，会自动重建 |
| `data/` | **飞行数据资产**（本机，不入库） | `flightlog/<session>/`：`meta.json` + `frames.jsonl` + `summary.json`，外加 `index.json`；只增不改，供离线分析/导出。刻意独立于 `.airsim_agent/`，避免"清空 Agent 家"时误删 |
| `docs/` | 设计文档（49 份） | 故意出库；入口是 `docs/README.md` 索引 |
| `.env` | 隐私配置（API key、端口等） | `src/config.py` 启动时读取；**永不入库** |
| `third_party/` | 学习参考项目（11 个，596 MB） | 不参与构建；运行时仅读 qgroundcontrol 里一个 12 KB 的 json |
| `models/` | 权重目录 | `README.md` 入库；`*.pt`（休眠的检测器权重）不上传 |
| `weights/` | CLIP 权重（338 MB） | 代码零引用 |
| `PX4-Autopilot.zip` | 1.7 GB 下载件 | 本地保留，部署用 |
| ~~`src/data/`~~ | 已删除（2026-10-19） | 数据统一迁到 `.airsim_agent/`；`src/` 只放代码 |
| `logs/` `captures/` `*server*.log` | 运行期产物 | 可删可再生 |
| `.venv/` `.pytest_cache/` `.coverage` `.zcode/` | 环境与缓存 | 可删可重建（`.venv` 用 `uv sync`） |

## 二、scripts/ 已整体删除（2026-10-14）

`scripts/` 目录已删除（启动器、链路冒烟、实验探测、行人控制、ros2 组、检测时代脚本
全部移除）。需要时从 git 历史找回：`git log -- scripts/`。
手动操作改为直接命令行，例如启动地面站：

```powershell
python -m src.ui.server --host 127.0.0.1 --port 8765
```

## 三、常用命令

```powershell
uv sync                                    # 建/更新虚拟环境

# 启动地面站（操作员的实际用法）
.\.venv\Scripts\activate
python -m src.ui.server --host 127.0.0.1 --port 8765
# 或：uv run python -m src.ui.server --host 127.0.0.1 --port 8765 --backend px4_mavlink
# 或：uv run python -m src

uv run pytest -q                           # 全量测试，提交前必须全绿（当前 780+ 条）
```

## 四、改动纪律（每一条都有历史代价）

1. **测试**：新机制必须带回归用例；删代码时连带删掉只为它存在的测试；**提交前全量测试全绿**。
2. **判据属于任务**：完成标准由计划声明（`goal.success_criteria`），运行时只负责评估，
   不许替任务发明标准（"按任务类型兜底"已被操作员明确否决）。
3. **不做检测前置**：飞行动作不设目标检测前置条件；感知走 VLM + 深度。检测能力处于
   休眠状态（`models/README.md` 有说明），不要以它为依赖。
4. **技能面只有 SKILL.md**：技能是操作知识、不是动作；只加载 `.airsim_agent/skills/*/SKILL.md`，
   `SYSTEM.md` 是常驻提示词、不参与技能加载。新增能力先分清它是**工具**还是**技能**——
   同一意图只保留一条权威路径。
5. **数值要全局搜索**：站离 2 m、fill 0.25、28 步上限、90° 相机常数等目前存在多处
   副本（代码 / 系统提示词 / 技能文档），改一个数字前先全仓库搜一遍，别只改一处。
6. **死代码不留**：零引用模块直接删，git 有历史可以找回；不要把"暂时没用"的代码留在主干。
7. **ros2 冻结**：不要在 px4_ros2 链路（`ros2/`、`provider_*` 工具）上开发新功能；
   改动主线以 px4_mavlink + AirSim 仿真为准。
8. **文档**：`docs/` 出库但要有索引（`docs/README.md`）；文档过时就在头部加"现状提示"
   横幅并指向权威文档，不要静默修改历史结论。
9. **语言**：技能文档与面向模型的提示词用中文；代码标识符（工具名、metric 名、参数名）
   保持英文，不要翻译。

10. **多机与并发（2026-10-15 定稿）**：多机执行同一条指令的**并发在控制器内部**实现
    （`_run_per_vehicle` 每机一线程 + MAVLink 线程本地目标；AirSim 用每机客户端）；
    **"非阻塞"由编排层后台任务承担**（GCS `_submit_multi_job`：后台线程跑阻塞工具，
    立即返回任务号，进度走 `progress().details.jobs`）。**不要再新增 `dispatch_*` 一类
    的工具**——模型面只有一套阻塞语义 + `vehicle_name` 扇出，工具集保持正交。

11. **真机工具面最小（2026-10-15）**：`real_vehicle` 连接只暴露基本控制工具
    （PX4 对接的那套 drone_* 飞行/状态工具）；感知/相机类工具
    （`perception_status` / `inspect_current_frame` / `locate_image_point` /
    `airsim_get_sensors`）在真机上一律不注册——真机没有仿真相机链路。
    感知轴照常为操作员出预览，但那不是模型的工具面。

## 五、提交前自检

- [ ] `uv run pytest -q` 全绿
- [ ] `git status` 里没有 `docs/`、`.env`、`third_party/`、权重、`.airsim_agent/` 数据等本机专属内容
- [ ] 新增/修改的技能文档通过：描述以"当"开头、节标题用规范中文名
- [ ] 若改了行为：有对应测试或运行记录证据
