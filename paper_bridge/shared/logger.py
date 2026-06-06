"""Shared logging configuration for Paper Bridge modules."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_LOG_FORMAT: str = "%(asctime)s - %(levelname)s - %(message)s"


class _FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every record.

    In short-lived containers (AWS Batch / Lambda) a buffered handler can lose
    its tail when the process exits or is OOM-killed before the buffer drains —
    which is exactly why app logs were missing from CloudWatch during the E2E
    run. Flushing per-record guarantees each line reaches the awslogs driver.
    """

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def is_aws_env() -> bool:
    """Check if the code is running in an AWS environment (Lambda, ECS, Batch)."""
    aws_env_vars = [
        "AWS_BATCH_JOB_ID",
        "AWS_ECS_CONTAINER_METADATA_URI",
        "AWS_ECS_CONTAINER_METADATA_URI_V4",
        "AWS_EXECUTION_ENV",
        "AWS_LAMBDA_FUNCTION_NAME",
        "AWS_LAMBDA_RUNTIME_API",
        "ECS_CONTAINER_METADATA_URI",
    ]
    return any(env_var in os.environ for env_var in aws_env_vars)


class LoggerConfig:
    """Configuration for logger setup."""

    def __init__(
        self,
        name: str = __name__,
        level: int = logging.INFO,
        log_format: str = DEFAULT_LOG_FORMAT,
        logs_dir_path: Path | None = None,
    ):
        self.name = name
        self.level = level
        self.log_format = log_format
        self.logs_dir_path = logs_dir_path


def create_logger(config: LoggerConfig) -> logging.Logger:
    """Create and configure a logger based on the provided configuration."""
    logger = logging.getLogger(config.name)
    logger.setLevel(config.level)
    formatter = logging.Formatter(config.log_format)

    _add_console_handler(logger, formatter)
    if config.logs_dir_path and not is_aws_env():
        _add_file_handler(logger, formatter, config.logs_dir_path)

    return logger


def _add_console_handler(logger: logging.Logger, formatter: logging.Formatter) -> None:
    """Add a console handler, unless one is already attached.

    The guard makes ``create_logger`` idempotent: re-importing a module that
    configures a named logger will not stack duplicate handlers (which would
    otherwise emit every log line multiple times).
    """
    if any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        return
    # Target stdout with a per-record flush so logs survive short-lived containers
    # (Batch/Lambda) and reach the awslogs driver even on early/abnormal exit.
    console_handler = _FlushingStreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def _add_file_handler(
    logger: logging.Logger, formatter: logging.Formatter, logs_dir_path: Path
) -> None:
    """Add a file handler for local environments, unless one already exists."""
    logs_dir_path.mkdir(parents=True, exist_ok=True)
    log_filename = _generate_log_filename("logs.txt")
    logs_path = logs_dir_path / log_filename
    if any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(logs_path)
        for h in logger.handlers
    ):
        return
    file_handler = logging.FileHandler(logs_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def _generate_log_filename(base_filename: str) -> str:
    """Generate a timestamped log filename."""
    name, ext = base_filename.rsplit(".", 1)
    return f"{name}_{datetime.now().strftime('%Y-%m-%d')}.{ext}"


def get_log_level() -> int:
    """Get log level from environment variable."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    return logging.DEBUG if log_level_str == "DEBUG" else logging.INFO
