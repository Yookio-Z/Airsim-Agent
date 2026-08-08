"""
任务进度追踪器
参考 dimOS ToolStream 设计，为 MCP 长任务提供进度反馈

MCP 协议本身是请求-响应模式，长任务（如跟踪30秒）无法中途推送。
本模块通过以下方式解决:
  1. 长任务在后台线程执行，MCP tool 立即返回任务ID
  2. 大模型可随时调用 airsim_task_status(task_id) 查询进度
  3. 任务完成后结果缓存在内存中
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import Enum

from ..logging_config import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    task_id: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "message": self.message,
        }
        if self.result is not None:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        elapsed = 0.0
        if self.started_at > 0:
            end = self.finished_at if self.finished_at > 0 else time.time()
            elapsed = round(end - self.started_at, 1)
        d["elapsed_sec"] = elapsed
        return d


class TaskManager:
    """后台任务管理器。

    用法:
        manager = TaskManager()

        # 启动长任务
        task_id = manager.start_task(
            task_type="track_object",
            target="red car",
            func=my_tracking_function,
            args=(controller, stream),
        )

        # 查询进度
        info = manager.get_task(task_id)

        # 取消任务
        manager.cancel_task(task_id)
    """

    def __init__(self, max_history: int = 50) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._max_history = max_history

    def start_task(
        self,
        task_type: str,
        func: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[dict] = None,
        message: str = "",
    ) -> str:
        """启动后台任务，返回 task_id。"""
        with self._lock:
            self._counter += 1
            task_id = f"{task_type}_{self._counter}_{int(time.time())}"

        info = TaskInfo(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            message=message or f"任务 {task_type} 已创建",
            created_at=time.time(),
        )

        with self._lock:
            self._tasks[task_id] = info

        kwargs = kwargs or {}

        def _run():
            info.status = TaskStatus.RUNNING
            info.started_at = time.time()
            info.message = f"任务 {task_type} 执行中..."
            try:
                result = func(*args, task_info=info, **kwargs)
                info.result = result
                if info.cancel_flag or info.status == TaskStatus.CANCELLED:
                    info.status = TaskStatus.CANCELLED
                else:
                    info.status = TaskStatus.COMPLETED
                    info.progress = 100.0
                    info.message = f"任务 {task_type} 完成"
            except Exception as e:
                if info.cancel_flag or info.status == TaskStatus.CANCELLED:
                    info.status = TaskStatus.CANCELLED
                else:
                    info.status = TaskStatus.FAILED
                    info.error = str(e)
                    info.message = f"任务 {task_type} 失败: {e}"
                    logger.error(f"Task {task_id} failed: {e}")
            finally:
                info.finished_at = time.time()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            info = self._tasks.get(task_id)
            if info is None:
                return False
            if info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            info.cancel_flag = True
            info.status = TaskStatus.CANCELLED
            info.message = f"任务 {info.task_type} 已取消"
            info.finished_at = time.time()
            return True

    def list_tasks(self, task_type: str = "") -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        return [t.to_dict() for t in reversed(tasks)]

    def cleanup(self) -> None:
        with self._lock:
            if len(self._tasks) <= self._max_history:
                return
            sorted_ids = sorted(
                self._tasks.keys(),
                key=lambda k: self._tasks[k].created_at,
            )
            to_remove = sorted_ids[: len(self._tasks) - self._max_history]
            for tid in to_remove:
                del self._tasks[tid]
