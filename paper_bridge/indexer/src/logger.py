"""Thin shim delegating to ``paper_bridge.shared.logger``."""

from pathlib import Path

from paper_bridge.shared.logger import (  # noqa: F401
    DEFAULT_LOG_FORMAT,
    LoggerConfig,
    create_logger,
    get_log_level,
    is_aws_env,
)

from .constants import LocalPaths

default_logger_config = LoggerConfig(
    level=get_log_level(),
    logs_dir_path=Path(__file__).parent.parent.parent.parent
    / LocalPaths.LOGS_DIR.value,
)
logger = create_logger(default_logger_config)
