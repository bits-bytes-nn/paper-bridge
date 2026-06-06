"""Tests for the shared arXiv client (static PDF download + batched metadata).

These lock in the 429-avoidance design: PDF downloads go to the static
arxiv.org/pdf host (never the API), honor Retry-After, and metadata is fetched
in one batched, serialized API call.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from paper_bridge.shared import arxiv_client


def _response(status, content=b"%PDF-1.5 data", retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    request = httpx.Request("GET", "https://arxiv.org/pdf/x")
    return httpx.Response(status, content=content, headers=headers, request=request)


def _patch_client(handler):
    """Patch httpx.Client so its .get(url) routes through ``handler(url)``."""
    captured: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            captured.append(url)
            return handler(url)

    return patch.object(httpx, "Client", FakeClient), captured


@pytest.mark.unit
class TestDownloadPdf:
    def test_hits_static_pdf_host_not_api(self, tmp_path: Path) -> None:
        patcher, captured = _patch_client(lambda url: _response(200))
        with patcher:
            out = arxiv_client.download_pdf("2606.03458", tmp_path / "p.pdf")

        assert out is not None and out.exists()
        assert "arxiv.org/pdf/2606.03458" in captured[0]
        assert "export.arxiv.org" not in captured[0]

    def test_retries_on_429_then_succeeds(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(url):
            calls["n"] += 1
            return _response(429, retry_after=2) if calls["n"] == 1 else _response(200)

        slept: list[int] = []
        patcher, _ = _patch_client(handler)
        with patcher:
            out = arxiv_client.download_pdf(
                "2606.03458", tmp_path / "p.pdf", sleep=slept.append
            )

        assert out is not None
        assert calls["n"] == 2
        assert slept == [2]  # honored Retry-After

    def test_gives_up_after_max_retries(self, tmp_path: Path) -> None:
        patcher, _ = _patch_client(lambda url: _response(503))
        with patcher:
            out = arxiv_client.download_pdf(
                "2606.03458", tmp_path / "p.pdf", sleep=lambda s: None
            )
        assert out is None

    def test_non_pdf_200_body_rejected(self, tmp_path: Path) -> None:
        # arxiv.org/pdf can 200 with an HTML interstitial for brand-new ids;
        # the magic-byte check must reject it rather than write a bad file.
        dest = tmp_path / "p.pdf"
        patcher, _ = _patch_client(
            lambda url: _response(200, content=b"<html>not ready</html>")
        )
        with patcher:
            out = arxiv_client.download_pdf("2606.03458", dest, sleep=lambda s: None)
        assert out is None
        assert not dest.exists()

    def test_non_retryable_status_returns_none(self, tmp_path: Path) -> None:
        patcher, _ = _patch_client(lambda url: _response(404))
        with patcher:
            out = arxiv_client.download_pdf(
                "2606.03458", tmp_path / "p.pdf", sleep=lambda s: None
            )
        assert out is None


@pytest.mark.unit
class TestFetchMetadata:
    def test_empty_ids_returns_empty(self) -> None:
        assert arxiv_client.fetch_metadata([]) == {}

    def test_batches_all_ids_into_one_search(self) -> None:
        # Two ids -> exactly one Search constructed with both, one client.results.
        r1 = MagicMock()
        r1.get_short_id.return_value = "2606.111v1"
        r2 = MagicMock()
        r2.get_short_id.return_value = "2606.222"

        fake_client = MagicMock()
        fake_client.results.return_value = iter([r1, r2])
        search_calls = {}

        fake_arxiv = MagicMock()
        fake_arxiv.Client.return_value = fake_client

        def make_search(id_list):
            search_calls["id_list"] = id_list
            return MagicMock()

        fake_arxiv.Search.side_effect = make_search

        # Reset the module-level cached client so our fake is used.
        arxiv_client._metadata_client = None
        with patch.dict("sys.modules", {"arxiv": fake_arxiv}):
            out = arxiv_client.fetch_metadata(["2606.111", "2606.222"])

        assert search_calls["id_list"] == ["2606.111", "2606.222"]
        fake_client.results.assert_called_once()
        # Version-stripped id maps back to the result.
        assert out["2606.111"] is r1
        assert out["2606.222"] is r2

    def test_missing_id_absent_from_result(self) -> None:
        r1 = MagicMock()
        r1.get_short_id.return_value = "2606.111"
        fake_client = MagicMock()
        fake_client.results.return_value = iter([r1])
        fake_arxiv = MagicMock()
        fake_arxiv.Client.return_value = fake_client
        fake_arxiv.Search.return_value = MagicMock()

        arxiv_client._metadata_client = None
        with patch.dict("sys.modules", {"arxiv": fake_arxiv}):
            out = arxiv_client.fetch_metadata(["2606.111", "2606.999"])

        assert "2606.111" in out
        assert "2606.999" not in out
