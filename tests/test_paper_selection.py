"""Tests for the principled paper-selection scorer (rubric 3.1)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from paper_bridge.shared.paper_selection import (
    PaperScorer,
    ScoredPaper,
    SelectionConfig,
)


@dataclass
class FakePaper:
    arxiv_id: str
    title: str
    upvotes: int
    published_at: datetime


def _p(arxiv_id: str, upvotes: int, days_old: int = 0, title: str = "") -> FakePaper:
    ref = datetime(2025, 3, 28, tzinfo=UTC)
    return FakePaper(
        arxiv_id=arxiv_id,
        title=title or f"Paper {arxiv_id}",
        upvotes=upvotes,
        published_at=ref - timedelta(days=days_old),
    )


REF = datetime(2025, 3, 28, tzinfo=UTC)


@pytest.mark.unit
class TestSelectionConfigValidation:
    def test_defaults_valid(self) -> None:
        cfg = SelectionConfig()
        assert cfg.popularity_weight > 0
        assert cfg.recency_weight > 0

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            SelectionConfig(popularity_weight=-1)

    def test_zero_total_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            SelectionConfig(popularity_weight=0, recency_weight=0)

    def test_nonpositive_half_life_rejected(self) -> None:
        with pytest.raises(ValueError):
            SelectionConfig(recency_half_life_days=0)


@pytest.mark.unit
class TestSelectBasics:
    def test_empty_input(self) -> None:
        assert PaperScorer().select([], limit=5, reference_date=REF) == []

    def test_zero_limit(self) -> None:
        assert PaperScorer().select([_p("a", 10)], limit=0, reference_date=REF) == []

    def test_limit_truncates(self) -> None:
        papers = [_p(str(i), upvotes=i) for i in range(10)]
        out = PaperScorer().select(papers, limit=3, reference_date=REF)
        assert len(out) == 3

    def test_returns_input_objects(self) -> None:
        p = _p("a", 10)
        out = PaperScorer().select([p], limit=1, reference_date=REF)
        assert out[0] is p


@pytest.mark.unit
class TestRanking:
    def test_higher_upvotes_ranks_first_same_age(self) -> None:
        low = _p("low", upvotes=5, days_old=0)
        high = _p("high", upvotes=500, days_old=0)
        out = PaperScorer().select([low, high], limit=2, reference_date=REF)
        assert [p.arxiv_id for p in out] == ["high", "low"]

    def test_fresher_ranks_first_same_votes(self) -> None:
        old = _p("old", upvotes=50, days_old=30)
        fresh = _p("fresh", upvotes=50, days_old=0)
        out = PaperScorer().select([old, fresh], limit=2, reference_date=REF)
        assert [p.arxiv_id for p in out] == ["fresh", "old"]

    def test_recency_can_outweigh_modest_vote_gap(self) -> None:
        # A fresh paper with slightly fewer votes beats a stale viral one when
        # recency is weighted heavily.
        cfg = SelectionConfig(popularity_weight=0.2, recency_weight=0.8)
        stale_viral = _p("stale", upvotes=400, days_old=60)
        fresh_ok = _p("fresh", upvotes=300, days_old=0)
        out = PaperScorer(cfg).select(
            [stale_viral, fresh_ok], limit=1, reference_date=REF
        )
        assert out[0].arxiv_id == "fresh"

    def test_log_normalization_prevents_single_paper_domination(self) -> None:
        # With pure-popularity weighting, log1p compresses a viral outlier so
        # ordering still reflects the runner-up rather than collapsing.
        cfg = SelectionConfig(popularity_weight=1.0, recency_weight=0.0)
        viral = _p("viral", upvotes=100_000, days_old=0)
        mid = _p("mid", upvotes=100, days_old=0)
        low = _p("low", upvotes=10, days_old=0)
        out = PaperScorer(cfg).select([low, mid, viral], limit=3, reference_date=REF)
        assert [p.arxiv_id for p in out] == ["viral", "mid", "low"]


@pytest.mark.unit
class TestDedup:
    def test_cross_day_dedup_keeps_highest_upvotes(self) -> None:
        # Same paper seen on two days with different vote counts → one entry,
        # the higher-upvote instance.
        day1 = _p("dup", upvotes=10, days_old=1)
        day2 = _p("dup", upvotes=40, days_old=0)
        out = PaperScorer().select([day1, day2], limit=5, reference_date=REF)
        assert len(out) == 1
        assert out[0].upvotes == 40

    def test_dedup_across_many(self) -> None:
        papers = [_p("a", 10), _p("b", 20), _p("a", 30), _p("c", 5), _p("b", 15)]
        out = PaperScorer().select(papers, limit=10, reference_date=REF)
        assert sorted(p.arxiv_id for p in out) == ["a", "b", "c"]
        by_id = {p.arxiv_id: p.upvotes for p in out}
        assert by_id["a"] == 30 and by_id["b"] == 20


@pytest.mark.unit
class TestMinUpvotesFloor:
    def test_floor_filters_below_threshold(self) -> None:
        cfg = SelectionConfig(min_upvotes=10)
        papers = [_p("a", 5), _p("b", 10), _p("c", 50)]
        out = PaperScorer(cfg).select(papers, limit=10, reference_date=REF)
        assert sorted(p.arxiv_id for p in out) == ["b", "c"]

    def test_no_floor_keeps_all(self) -> None:
        cfg = SelectionConfig(min_upvotes=None)
        papers = [_p("a", 0), _p("b", 1)]
        out = PaperScorer(cfg).select(papers, limit=10, reference_date=REF)
        assert len(out) == 2

    def test_all_filtered_returns_empty(self) -> None:
        cfg = SelectionConfig(min_upvotes=1000)
        out = PaperScorer(cfg).select([_p("a", 5)], limit=5, reference_date=REF)
        assert out == []


@pytest.mark.unit
class TestScoreAll:
    def test_score_breakdown_components_in_range(self) -> None:
        scored = PaperScorer().score_all(
            [_p("a", 100, days_old=0), _p("b", 1, days_old=14)], reference_date=REF
        )
        assert all(isinstance(s, ScoredPaper) for s in scored)
        for s in scored:
            assert 0.0 <= s.popularity <= 1.0
            assert 0.0 < s.recency <= 1.0

    def test_zero_age_recency_is_one(self) -> None:
        scored = PaperScorer().score_all([_p("a", 10, days_old=0)], reference_date=REF)
        assert scored[0].recency == pytest.approx(1.0)

    def test_half_life_halves_recency(self) -> None:
        cfg = SelectionConfig(recency_half_life_days=7.0)
        scored = PaperScorer(cfg).score_all(
            [_p("a", 10, days_old=7)], reference_date=REF
        )
        assert scored[0].recency == pytest.approx(0.5, abs=1e-6)


@pytest.mark.unit
class TestNaiveDatetimeHandling:
    def test_naive_published_at_does_not_raise(self) -> None:
        naive = FakePaper("a", "t", 10, datetime(2025, 3, 27))  # no tzinfo
        out = PaperScorer().select([naive], limit=1, reference_date=REF)
        assert len(out) == 1

    def test_naive_reference_date(self) -> None:
        out = PaperScorer().select(
            [_p("a", 10)], limit=1, reference_date=datetime(2025, 3, 28)
        )
        assert len(out) == 1
