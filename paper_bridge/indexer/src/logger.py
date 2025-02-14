import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from .constants import LocalPaths

DEFAULT_LOG_FORMAT: str = "%(asctime)s - %(levelname)s - %(message)s"


def is_aws_env() -> bool:
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
    def __init__(
        self,
        name: str = __name__,
        level: int = logging.INFO,
        log_format: str = DEFAULT_LOG_FORMAT,
        logs_dir_path: Optional[Path] = None,
    ):
        self.name = name
        self.level = level
        self.log_format = log_format
        self.logs_dir_path = logs_dir_path


def create_logger(config: LoggerConfig) -> logging.Logger:
    logger = logging.getLogger(config.name)
    logger.setLevel(config.level)
    formatter = logging.Formatter(config.log_format)

    _add_console_handler(logger, formatter)
    if config.logs_dir_path and not is_aws_env():
        _add_file_handler(logger, formatter, config.logs_dir_path)

    return logger


def _add_console_handler(logger: logging.Logger, formatter: logging.Formatter) -> None:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def _add_file_handler(
    logger: logging.Logger, formatter: logging.Formatter, logs_dir_path: Path
) -> None:
    logs_dir_path.mkdir(parents=True, exist_ok=True)
    log_filename = _generate_log_filename(LocalPaths.LOGS_FILE.value)
    logs_path = logs_dir_path / log_filename
    file_handler = logging.FileHandler(logs_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def _generate_log_filename(base_filename: str) -> str:
    name, ext = base_filename.rsplit(".", 1)
    return f"{name}_{datetime.now().strftime('%Y-%m-%d')}.{ext}"


level = (
    logging.DEBUG if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" else logging.INFO
)
default_logger_config = LoggerConfig(
    # level=level,
    level=logging.DEBUG,
    logs_dir_path=Path(__file__).parent.parent.parent.parent
    / LocalPaths.LOGS_DIR.value,
)
logger = create_logger(default_logger_config)
