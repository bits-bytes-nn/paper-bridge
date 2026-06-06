"""Tests that the deduplicated subsystem loggers still expose their public API.

After the refactor each subsystem ``logger.py`` is a thin shim over
``paper_bridge.shared.logger``. These tests lock in the contract that existing
import sites rely on (``logger``, ``is_aws_env`` / ``is_running_in_aws``).
"""

import logging
from importlib import import_module

import pytest


@pytest.mark.unit
class TestSharedLogger:
    def test_is_aws_env_false_when_clean(self) -> None:
        from paper_bridge.shared.logger import is_aws_env

        assert is_aws_env() is False

    def test_is_aws_env_true_with_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from paper_bridge.shared.logger import is_aws_env

        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "fn")
        assert is_aws_env() is True

    def test_get_log_level_default_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from paper_bridge.shared.logger import get_log_level

        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert get_log_level() == logging.INFO

    def test_get_log_level_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from paper_bridge.shared.logger import get_log_level

        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        assert get_log_level() == logging.DEBUG

    def test_console_handler_targets_stdout_and_flushes(self) -> None:
        # Logs must go to stdout via a per-record-flushing handler so short-lived
        # containers (Batch/Lambda) don't lose their tail before exit/OOM.
        import sys

        from paper_bridge.shared.logger import (
            LoggerConfig,
            _FlushingStreamHandler,
            create_logger,
        )

        lg = create_logger(
            LoggerConfig(name="paper_bridge.test.flush2", level=logging.INFO)
        )
        console = [
            h
            for h in lg.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console) == 1
        assert isinstance(console[0], _FlushingStreamHandler)
        assert console[0].stream is sys.stdout

    def test_create_logger_is_idempotent_no_duplicate_handlers(self) -> None:
        # Re-creating a logger with the same name must not stack handlers
        # (otherwise every line is logged multiple times).
        from paper_bridge.shared.logger import LoggerConfig, create_logger

        name = "paper_bridge.test.idempotent"
        cfg = LoggerConfig(name=name, level=logging.INFO)
        first = create_logger(cfg)
        console_after_first = [
            h
            for h in first.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        create_logger(cfg)
        console_after_second = [
            h
            for h in first.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_after_first) == 1
        assert len(console_after_second) == 1


# NOTE: import the submodule via import_module rather than
# ``from <pkg>.src import logger``. The indexer/cleaner package ``__init__``
# eagerly bind the name ``logger`` to the Logger instance, which would shadow the
# submodule under attribute access; import_module resolves the real module.


@pytest.mark.unit
class TestSummarizerLoggerShim:
    def test_exports_logger_and_is_aws_env(self) -> None:
        mod = import_module("paper_bridge.summarizer.src.logger")

        assert isinstance(mod.logger, logging.Logger)
        assert callable(mod.is_aws_env)


@pytest.mark.unit
class TestIndexerLoggerShim:
    def test_exports_logger_and_is_aws_env(self) -> None:
        mod = import_module("paper_bridge.indexer.src.logger")

        assert isinstance(mod.logger, logging.Logger)
        assert callable(mod.is_aws_env)


@pytest.mark.unit
class TestCleanerLoggerShim:
    def test_exports_logger_and_is_running_in_aws(self) -> None:
        mod = import_module("paper_bridge.cleaner.src.logger")

        assert isinstance(mod.logger, logging.Logger)
        # cleaner historically named the predicate differently; must be preserved.
        assert callable(mod.is_running_in_aws)

    def test_logger_does_not_propagate(self) -> None:
        mod = import_module("paper_bridge.cleaner.src.logger")

        assert mod.logger.propagate is False
