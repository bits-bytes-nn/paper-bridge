"""Rate-limit-resilient arXiv access shared by the indexer and summarizer.

The recurring HTTP 429 came from hitting the rate-limited arXiv *API*
(export.arxiv.org/api/query) once per paper, fanned out across worker threads
(which defeats the arxiv library's built-in 3s throttle), and re-fetching the
PDF URL via the API even though it is derivable.

This module separates the two needs:

- `download_pdf` pulls bytes straight from the static, un-rate-limited
  ``arxiv.org/pdf/{id}`` host over httpx, honoring ``Retry-After`` — no API
  call at all.
- `fetch_metadata` uses a single, process-wide, serialized ``arxiv.Client`` and
  batches every id into one ``Search(id_list=[...])`` call, so metadata costs
  O(1) API requests per run instead of O(papers), and never runs concurrently.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .constants import URLs

logger = logging.getLogger(__name__)

_PDF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}
_PDF_DOWNLOAD_TIMEOUT = 60
_PDF_MAX_RETRIES = 4
_PDF_DEFAULT_BACKOFF = 5  # seconds, when no Retry-After header is present


def _pdf_url(arxiv_id: str) -> str:
    return f"{URLs.ARXIV_PDF.url}/{arxiv_id}"


def download_pdf(arxiv_id: str, dest: Path, sleep=time.sleep) -> Path | None:
    """Download a paper's PDF from the static arxiv.org/pdf host.

    Retries on 429/5xx honoring ``Retry-After``. Returns the written path, or
    ``None`` if the download ultimately failed. ``sleep`` is injectable for
    tests. This never touches the rate-limited arXiv API.
    """
    url = _pdf_url(arxiv_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(_PDF_MAX_RETRIES):
        try:
            with httpx.Client(
                timeout=_PDF_DOWNLOAD_TIMEOUT,
                headers=_PDF_HEADERS,
                follow_redirects=True,
            ) as client:
                response = client.get(url)

            if response.status_code == 200:
                # arxiv.org/pdf occasionally returns 200 with an HTML "not yet
                # available" interstitial for very new ids; only accept a real
                # PDF (magic bytes) so a bad body isn't handed to the parser.
                if not response.content.startswith(b"%PDF"):
                    logger.error(
                        "arXiv PDF '%s' returned non-PDF body (%d bytes); skipping",
                        arxiv_id,
                        len(response.content),
                    )
                    return None
                dest.write_bytes(response.content)
                logger.info("Downloaded arXiv PDF '%s' -> %s", arxiv_id, dest)
                return dest

            if response.status_code in (429, 500, 502, 503, 504):
                wait = _retry_after_seconds(response, attempt)
                logger.warning(
                    "arXiv PDF '%s' HTTP %s (attempt %d/%d); retrying in %ss",
                    arxiv_id,
                    response.status_code,
                    attempt + 1,
                    _PDF_MAX_RETRIES,
                    wait,
                )
                sleep(wait)
                continue

            logger.error(
                "arXiv PDF '%s' failed: HTTP %s", arxiv_id, response.status_code
            )
            return None
        except httpx.HTTPError as e:
            wait = _PDF_DEFAULT_BACKOFF * (attempt + 1)
            logger.warning(
                "arXiv PDF '%s' error (attempt %d/%d): %s; retrying in %ss",
                arxiv_id,
                attempt + 1,
                _PDF_MAX_RETRIES,
                e,
                wait,
            )
            sleep(wait)

    logger.error("arXiv PDF '%s' failed after %d attempts", arxiv_id, _PDF_MAX_RETRIES)
    return None


def _retry_after_seconds(response: httpx.Response, attempt: int) -> int:
    """Seconds to wait, preferring the Retry-After header, else exp backoff."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(1, int(header))
        except ValueError:
            pass
    return _PDF_DEFAULT_BACKOFF * (attempt + 1)


# A single arxiv.Client shared process-wide, guarded by a lock so metadata
# lookups never run concurrently (concurrent clients each ignore the others'
# 3s delay budget, which is what triggered the 429s).
_metadata_lock = threading.Lock()
_metadata_client: Any | None = None


def _get_metadata_client() -> Any:
    global _metadata_client
    if _metadata_client is None:
        import arxiv

        # delay_seconds=3 is the arXiv-requested floor; num_retries gives the
        # library its own 429 backoff on top of our serialization.
        _metadata_client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=5)
    return _metadata_client


def fetch_metadata(arxiv_ids: list[str]) -> dict[str, Any]:
    """Fetch arXiv metadata for many ids in ONE batched, serialized API call.

    Returns a dict keyed by the requested id. Missing ids are simply absent.
    Matching is done on the result's short id so callers get back exactly the
    ids they asked for.
    """
    if not arxiv_ids:
        return {}

    import arxiv

    results: dict[str, Any] = {}
    with _metadata_lock:
        client = _get_metadata_client()
        search = arxiv.Search(id_list=list(arxiv_ids))
        try:
            for result in client.results(search):
                short_id = result.get_short_id()
                # get_short_id() can include a version suffix (e.g. 2606.01v2);
                # map both the exact and version-stripped id back to the result.
                results[short_id] = result
                results[short_id.split("v")[0]] = result
        except Exception as e:
            logger.error("arXiv metadata batch fetch failed: %s", e)

    return {aid: results[aid] for aid in arxiv_ids if aid in results}
