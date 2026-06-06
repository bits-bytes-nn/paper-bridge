"""Principled, configurable paper selection / ranking.

The original selection was ``sorted(papers, key=(-upvotes, title))[:N]`` applied
per day, with two weaknesses:

1. **No cross-day deduplication.** A paper that appears on several HuggingFace
   "daily papers" pages (papers resurface for days) was processed once per day —
   wasted compute and duplicate output.
2. **Raw upvote sort overfits to virality.** A single highly upvoted paper
   dominates, and a brand-new high-quality paper with few votes yet is buried.
   There is no notion of recency or diminishing returns on votes.

This module replaces that with a small, transparent, **configurable** scorer:

    score = w_pop * popularity(upvotes) + w_rec * recency(age_days)

- ``popularity`` uses ``log1p(upvotes)`` normalized to ``[0, 1]`` across the
  candidate set, so vote counts have diminishing returns and one viral paper
  cannot crowd everything out.
- ``recency`` applies exponential decay with a configurable half-life, so fresh
  papers are favored without hard-coding "yesterday only".

All weights/parameters live in :class:`SelectionConfig` (fed from app config),
so behavior is tunable rather than baked into magic constants. The scorer is
pure and operates on a structural :class:`PaperLike` protocol, so it has no
dependency on the heavy fetcher modules and is trivially unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, TypeVar, runtime_checkable


@runtime_checkable
class PaperLike(Protocol):
    """Structural type for anything rankable as a paper."""

    arxiv_id: str
    title: str
    upvotes: int
    published_at: datetime


P = TypeVar("P", bound=PaperLike)


@dataclass(frozen=True)
class SelectionConfig:
    """Tunable parameters for :class:`PaperScorer`.

    Defaults are chosen to be sensible, not overfit: popularity and recency are
    weighted equally, with a one-week recency half-life that suits a daily feed
    where papers resurface for several days.
    """

    popularity_weight: float = 0.6
    recency_weight: float = 0.4
    # Days after which the recency contribution halves.
    recency_half_life_days: float = 7.0
    # Papers with fewer upvotes than this are dropped before scoring.
    # ``None`` disables the floor.
    min_upvotes: int | None = None

    def __post_init__(self) -> None:
        if self.popularity_weight < 0 or self.recency_weight < 0:
            raise ValueError("weights must be non-negative")
        if self.popularity_weight + self.recency_weight == 0:
            raise ValueError("at least one weight must be positive")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")


@dataclass
class ScoredPaper:
    """A paper paired with its computed score and component breakdown."""

    paper: PaperLike
    score: float
    popularity: float
    recency: float


@dataclass
class PaperScorer:
    """Rank papers by a configurable popularity + recency score.

    Usage::

        scorer = PaperScorer(SelectionConfig(...))
        top = scorer.select(papers, limit=5, reference_date=target)
    """

    config: SelectionConfig = field(default_factory=SelectionConfig)

    def select(
        self,
        papers: list[P],
        limit: int,
        reference_date: datetime | None = None,
    ) -> list[P]:
        """Return the top-``limit`` papers, de-duplicated by arxiv_id.

        Args:
            papers: candidate papers (may span multiple days, may contain dups).
            limit: maximum number to return (``>= 0``).
            reference_date: "now" for recency; defaults to current UTC.

        De-duplication keeps, for each ``arxiv_id``, the instance with the most
        upvotes (the most up-to-date vote count), then ranks the survivors.
        """
        if limit <= 0 or not papers:
            return []

        deduped = self._dedupe(papers)
        eligible = [p for p in deduped if self._meets_floor(p.upvotes)]
        if not eligible:
            return []

        ref = reference_date or datetime.now(UTC)
        scored = self._score_all(eligible, ref)
        scored.sort(key=lambda s: (-s.score, -s.paper.upvotes, s.paper.title))
        return [s.paper for s in scored[:limit]]  # type: ignore[misc]

    def score_all(
        self, papers: list[P], reference_date: datetime | None = None
    ) -> list[ScoredPaper]:
        """Score (without truncating) the de-duplicated, eligible papers.

        Exposed for observability/eval — lets callers inspect the score
        breakdown rather than only the final ordering.
        """
        deduped = self._dedupe(papers)
        eligible = [p for p in deduped if self._meets_floor(p.upvotes)]
        ref = reference_date or datetime.now(UTC)
        scored = self._score_all(eligible, ref)
        scored.sort(key=lambda s: (-s.score, -s.paper.upvotes, s.paper.title))
        return scored

    def _meets_floor(self, upvotes: int) -> bool:
        return self.config.min_upvotes is None or upvotes >= self.config.min_upvotes

    @staticmethod
    def _dedupe(papers: list[P]) -> list[P]:
        """Keep one instance per arxiv_id — the one with the most upvotes.

        Ties keep first-seen order, so the result is deterministic.
        """
        best: dict[str, P] = {}
        for paper in papers:
            existing = best.get(paper.arxiv_id)
            if existing is None or paper.upvotes > existing.upvotes:
                best[paper.arxiv_id] = paper
        return list(best.values())

    def _score_all(
        self, papers: list[P], reference_date: datetime
    ) -> list[ScoredPaper]:
        max_log_votes = max(math.log1p(max(p.upvotes, 0)) for p in papers)
        results: list[ScoredPaper] = []
        for paper in papers:
            popularity = self._popularity(paper.upvotes, max_log_votes)
            recency = self._recency(paper.published_at, reference_date)
            score = (
                self.config.popularity_weight * popularity
                + self.config.recency_weight * recency
            )
            results.append(ScoredPaper(paper, score, popularity, recency))
        return results

    @staticmethod
    def _popularity(upvotes: int, max_log_votes: float) -> float:
        """Log-normalized popularity in ``[0, 1]`` (diminishing returns)."""
        if max_log_votes <= 0:
            return 0.0
        return math.log1p(max(upvotes, 0)) / max_log_votes

    def _recency(self, published_at: datetime, reference_date: datetime) -> float:
        """Exponential time decay in ``(0, 1]``; 1.0 at age 0."""
        published = _ensure_utc(published_at)
        reference = _ensure_utc(reference_date)
        age_days = max((reference - published).total_seconds() / 86400.0, 0.0)
        return 0.5 ** (age_days / self.config.recency_half_life_days)


def _ensure_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC so arithmetic never raises."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
