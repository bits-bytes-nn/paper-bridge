"""Shared text helpers used across output handlers.

These were previously duplicated (and had drifted) between the Slack and GitHub
output handlers. Centralizing them here keeps a single, tested implementation.
"""

from __future__ import annotations

import re

_MARKDOWN_LINK = re.compile(r"\[([^]]+)]\(([^)]+)\)")


def convert_markdown_to_slack_links(text: str) -> str:
    """Convert Markdown links ``[text](url)`` to Slack mrkdwn ``<url|text>``."""
    return _MARKDOWN_LINK.sub(r"<\2|\1>", text)


def extract_unique_urls(urls_str: str) -> list[str]:
    """Extract unique entries from a comma-separated URL string.

    Entries may be plain URLs or Markdown links (``[text](url)``). When an entry
    is a Markdown link, deduplication keys on the underlying URL (the part after
    ``](``); otherwise the whole entry is the key. Order is preserved.
    """
    if not urls_str or not urls_str.strip():
        return []

    cleaned = [u.strip() for u in urls_str.split(",") if u.strip()]
    unique: list[str] = []
    seen: set[str] = set()

    for entry in cleaned:
        link_start = entry.rfind("](")
        if link_start != -1:
            key = entry[link_start + 2 : -1]
        else:
            key = entry
        if key and key not in seen:
            seen.add(key)
            unique.append(entry)

    return unique
