"""结构化日志配置 — 从 dimOS 借鉴 structlog + 控制台颜色。

用法:
  from src.logging_config import get_logger
  logger = get_logger(__name__)
  logger.info("takeoff", altitude=5.0, result="ok")
  logger.error("arm_failed", reason="no_gps")
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean venvs
    structlog = None

_LOG_LEVEL = os.environ.get("DRONE_LOG_LEVEL", "INFO").upper()


def _console_renderer(logger: logging.Logger, method_name: str, event_dict: dict) -> str:
    """控制台格式: 时间 [级别] 消息 key=value ..."""
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    lvl = method_name.upper()
    event = event_dict.pop("event", "")
    extras = " ".join(f"{k}={v}" for k, v in event_dict.items() if k not in ("timestamp", "level"))
    return f"{ts} [{lvl}] {event} {extras}".strip()


def configure_logging(level: str = _LOG_LEVEL, json_file: str | None = None) -> None:
    """配置全局日志: 控制台彩色 + 可选 JSONL 文件。"""
    if structlog is None:
        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
            level=getattr(logging, level, logging.INFO),
        )
        if json_file:
            fh = logging.FileHandler(json_file)
            fh.setLevel(getattr(logging, level, logging.INFO))
            logging.getLogger().addHandler(fh)
        return

    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
        structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else _console_renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 设置根日志级别
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=getattr(logging, level))

    # 可选 JSONL 文件
    if json_file:
        fh = logging.FileHandler(json_file)
        fh.setLevel(getattr(logging, level))
        json_processors = [
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
        # 单独配置 JSON 日志
        structlog.configure(
            processors=json_processors,
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
        logging.getLogger().addHandler(fh)


class _StdlibLoggerAdapter:
    """Tiny adapter that accepts structlog-style keyword fields."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(self._format(event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(self._format(event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(self._format(event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(self._format(event, kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(self._format(event, kwargs))

    def _format(self, event: str, kwargs: dict[str, Any]) -> str:
        extras = " ".join(f"{k}={v}" for k, v in kwargs.items())
        return f"{event} {extras}".strip()


def get_logger(name: str | None = None) -> Any:
    """获取结构化日志记录器。"""
    if structlog is None:
        return _StdlibLoggerAdapter(logging.getLogger(name or "src"))
    return structlog.get_logger(name or "src")


# 模块导入时自动配置
configure_logging()
