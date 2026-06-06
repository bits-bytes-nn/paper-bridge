"""Thin shim delegating to ``paper_bridge.shared.logger``."""

import logging
from pathlib import Path

from paper_bridge.shared.logger import (
    LoggerConfig,
    create_logger,
    get_log_level,
)
from paper_bridge.shared.logger import is_aws_env as is_running_in_aws

from .constants import LocalPaths

# Public re-exports (this module is a shim; these names are part of its API even
# though they are not referenced inside the module itself).
__all__ = [
    "CLEANER_LOG_FORMAT",
    "LoggerConfig",
    "create_logger",
    "default_logger_config",
    "get_log_level",
    "is_running_in_aws",
    "logger",
]

CLEANER_LOG_FORMAT: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

default_logger_config = LoggerConfig(
    name="app",
    level=get_log_level(),
    log_format=CLEANER_LOG_FORMAT,
    logs_dir_path=Path(__file__).resolve().parent.parent.parent
    / LocalPaths.LOGS_DIR.value,
)
# Clear any pre-existing handlers so re-importing this module (the "app"
# logger is a process-wide singleton) does not duplicate handlers.
logging.getLogger(default_logger_config.name).handlers.clear()
logger = create_logger(default_logger_config)
logger.propagate = False
