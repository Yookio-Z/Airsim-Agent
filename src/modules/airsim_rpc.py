"""AirSim RPC 代理（拆分自 airsim_controller.py）。"""
from __future__ import annotations

import time
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

class _RpcProxy:
    """AirSim Client 的线程隔离代理（带执行锁 + 超时保护）。"""

    HEAVY_METHODS = {"simGetImage", "simGetImages", "simGetPointCloud"}
    HEAVY_TIMEOUT = 15.0
    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        real_client: airsim.MultirotorClient,
        rpc_exec_lock: threading.Lock,
    ) -> None:
        self._executor = executor
        self._real = real_client
        self._rpc_exec_lock = rpc_exec_lock

    def __getattr__(self, name: str) -> Any:
        real_method = getattr(self._real, name)

        def _wrapped(*args, **kwargs):
            timeout = self.HEAVY_TIMEOUT if name in self.HEAVY_METHODS else self.DEFAULT_TIMEOUT
            result_box = {"value": None, "error": None}

            def _call():
                acquired = self._rpc_exec_lock.acquire(timeout=20.0)
                if not acquired:
                    result_box["error"] = TimeoutError("RPC 执行锁获取超时(20s)")
                    return
                try:
                    result_box["value"] = real_method(*args, **kwargs)
                except Exception as e:
                    result_box["error"] = e
                finally:
                    self._rpc_exec_lock.release()

            future = self._executor.submit(_call)
            try:
                future.result(timeout=timeout + 20.0)
            except FutureTimeoutError:
                raise TimeoutError(f"AirSim RPC '{name}' 超时 ({timeout}s)")

            if isinstance(result_box["error"], Exception):
                raise result_box["error"]
            return result_box["value"]

        return _wrapped


