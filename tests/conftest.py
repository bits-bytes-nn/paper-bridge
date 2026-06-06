"""Shared pytest fixtures for Paper Bridge tests.

All fixtures here keep tests hermetic: no real network, no real AWS. AWS clients
are stubbed via ``moto`` / fakes, HTTP via ``responses``, and any environment
coupling is reset per-test.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _clean_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests never accidentally look like they run inside AWS.

    ``is_aws_env()`` keys off a set of AWS-injected env vars; clearing them makes
    environment detection deterministic regardless of where the suite runs.
    """
    for var in (
        "AWS_BATCH_JOB_ID",
        "AWS_ECS_CONTAINER_METADATA_URI",
        "AWS_ECS_CONTAINER_METADATA_URI_V4",
        "AWS_EXECUTION_ENV",
        "AWS_LAMBDA_FUNCTION_NAME",
        "AWS_LAMBDA_RUNTIME_API",
        "ECS_CONTAINER_METADATA_URI",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dummy AWS credentials so boto3/moto never touch a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")


@pytest.fixture
def fake_paper() -> SimpleNamespace:
    """A minimal Paper-like object usable wherever a Paper is consumed.

    Uses ``SimpleNamespace`` to avoid importing the heavy fetcher module (which
    pulls in graphrag/llama-index) for tests that only need the data shape.
    """
    return SimpleNamespace(
        arxiv_id="2503.23461",
        title="A Very Interesting Paper About Graphs",
        pdf_url="https://arxiv.org/pdf/2503.23461",
        published_at=datetime(2025, 3, 28),
        upvotes=42,
        content="Some parsed paper content.",
    )


@pytest.fixture
def captured_requests() -> Iterator[list]:
    """Collects outbound HTTP calls when used with the ``responses`` library."""
    calls: list = []
    yield calls
