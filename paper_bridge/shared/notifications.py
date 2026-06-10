"""Unified operational alarm formatting, shared across all Paper Bridge modules.

Every SNS alarm in this project — and in the sibling projects (omnisummary,
tech-digest, scholar-lens) — uses the same shape so a reader scanning a mailbox
can parse any of them at a glance:

    Subject: [<project>] <event> — <STATUS>

    <event> <STATUS>

    Key:   Value
    Key2:  Value2

    — 2026-06-10 04:12:00 UTC

Only failures and warnings are sent; success is not alarm-worthy.
"""

from __future__ import annotations

from datetime import UTC, datetime

PROJECT = "paper-bridge"


def format_alarm(
    *,
    event: str,
    status: str,
    fields: dict[str, str],
    project: str = PROJECT,
    timestamp: datetime | None = None,
) -> tuple[str, str]:
    """Build a ``(subject, message)`` pair in the unified alarm format.

    ``event`` is the human-readable action (e.g. "Summarizer", "Cleaner").
    ``status`` is a short uppercase state — ``FAILED`` or ``ALERT``.
    ``fields`` is an ordered mapping; single-line values render as an aligned
    ``Key: Value`` block, multi-line values render under their own ``Key:`` header.
    Drop a row by omitting it from the dict (don't pass empty values).
    """
    ts = (timestamp or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S")
    subject = f"[{project}] {event} — {status}"

    inline = {k: v for k, v in fields.items() if "\n" not in v}
    block = {k: v for k, v in fields.items() if "\n" in v}

    lines = [f"{event} {status}", ""]
    if inline:
        width = max(len(k) for k in inline)
        lines += [f"{k + ':':<{width + 1}} {v}" for k, v in inline.items()]
    for k, v in block.items():
        lines += ["", f"{k}:", v.strip("\n")]
    lines.append("")
    lines.append(f"— {ts} UTC")

    return subject, "\n".join(lines)
