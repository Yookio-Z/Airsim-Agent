"""
AirSimVideoStream - AirSim 伪视频流模块
后台线程持续轮询 simGetImage，提供 subscribe / get_latest_frame 接口
设计参考 dimOS 的流式架构：推模式替代拉模式
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np
import cv2
import airsim

from ..logging_config import get_logger

logger = get_logger(__name__)

FrameCallback = Callable[[np.ndarray, float], None]
"""回调签名: (image_array: np.ndarray, timestamp: float) -> None"""


class AirSimVideoStream:
    """AirSim 相机视频流封装。

    在独立后台线程中持续调用 simGetImage，将最新帧缓存到线程安全缓冲区。
    主线程可随时 get_latest_frame() 读取，无需等待 RPC。

    用法:
        stream = AirSimVideoStream(client, camera_name="0", fps=10)
        stream.start()

        # 方式1: 注册回调（类似 dimOS subscribe）
        def on_frame(frame, ts):
            print(f"收到帧 {frame.shape}, 时间戳 {ts:.3f}")
        stream.subscribe(on_frame)

        # 方式2: 主动拉取最新帧
        frame = stream.get_latest_frame()

        stream.stop()
    """

    def __init__(
        self,
        client: airsim.MultirotorClient,
        camera_name: str = "0",
        vehicle_name: str = "",
        image_type: int = airsim.ImageType.Scene,
        fps: float = 10.0,
        timeout_sec: float = 5.0,
    ) -> None:
        self._client = client
        self._camera_name = camera_name
        self._vehicle_name = vehicle_name
        self._image_type = image_type
        self._interval = 1.0 / max(fps, 1.0)
        self._timeout = timeout_sec

        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frame_count = 0
        self._drop_count = 0
        self._start_time = 0.0

        self._callbacks: list[FrameCallback] = []
        self._cb_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def subscribe(self, callback: FrameCallback) -> None:
        """注册帧回调函数（类似 dimOS 的 subscribe）。"""
        with self._cb_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
                logger.info(f"VideoStream: 注册回调，当前 {len(self._callbacks)} 个订阅者")

    def unsubscribe(self, callback: FrameCallback) -> None:
        """注销帧回调函数。"""
        with self._cb_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """获取最新一帧（非阻塞，可能返回 None）。"""
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def get_latest_timestamp(self) -> float:
        """获取最新帧的时间戳。"""
        with self._lock:
            return self._latest_timestamp

    @property
    def fps(self) -> float:
        """实际平均帧率。"""
        elapsed = time.time() - self._start_time
        if elapsed > 0:
            return self._frame_count / elapsed
        return 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动后台取流线程。"""
        if self._running:
            logger.warning("VideoStream: 已经在运行")
            return

        self._running = True
        self._frame_count = 0
        self._drop_count = 0
        self._start_time = time.time()

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info(
            f"VideoStream: 启动 camera={self._camera_name}, "
            f"vehicle={self._vehicle_name or 'default'}, "
            f"target_fps={1.0/self._interval:.1f}"
        )

    def stop(self) -> None:
        """停止后台取流线程。"""
        if not self._running:
            return

        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._timeout + 2.0)

        with self._lock:
            self._latest_frame = None

        logger.info(
            f"VideoStream: 停止，总帧数={self._frame_count}, "
            f"丢帧={self._drop_count}, 平均FPS={self.fps:.1f}"
        )

    # ------------------------------------------------------------------
    # 后台循环
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """后台线程主循环：定时拉取 AirSim 图像。"""
        while self._running:
            loop_start = time.time()

            frame, ts = self._fetch_frame()

            if frame is not None:
                # 更新缓冲区
                with self._lock:
                    self._latest_frame = frame
                    self._latest_timestamp = ts
                self._frame_count += 1

                # 触发回调
                self._notify_callbacks(frame, ts)
            else:
                self._drop_count += 1

            # 精确睡眠，维持目标帧率
            elapsed = time.time() - loop_start
            sleep_time = self._interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # 如果单帧处理已超过目标间隔，说明 AirSim 跟不上，跳过睡眠
                pass

    def _fetch_frame(self) -> tuple[Optional[np.ndarray], float]:
        """单次取帧，用 simGetImages（比 simGetImage 更稳定），带超时保护。"""
        result: dict = {"frame": None}

        def fetch():
            try:
                request = airsim.ImageRequest(
                    self._camera_name, self._image_type, False, True
                )
                responses = self._client.simGetImages(
                    [request], vehicle_name=self._vehicle_name
                )
                if responses and len(responses) > 0:
                    img_data = responses[0].image_data_uint8
                    if img_data and len(img_data) > 0:
                        raw = bytes(img_data)
                        nparr = np.frombuffer(raw, np.uint8)
                        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if img is not None:
                            result["frame"] = img
            except Exception as e:
                logger.debug(f"VideoStream fetch error: {e}")

        t = threading.Thread(target=fetch)
        t.daemon = True
        t.start()
        t.join(timeout=self._timeout)

        if t.is_alive():
            logger.warning("VideoStream: simGetImages 超时")
            return None, 0.0

        return result["frame"], time.time()

    def _notify_callbacks(self, frame: np.ndarray, ts: float) -> None:
        """通知所有订阅者。"""
        with self._cb_lock:
            callbacks = self._callbacks.copy()

        for cb in callbacks:
            try:
                cb(frame, ts)
            except Exception as e:
                logger.warning(f"VideoStream: 回调异常: {e}")
