"""Tests for Arena mapper: normalization, fuzzy matching, fill, validation."""

import pytest

from route_agent.model_registry.arena.mapper import (
    batch_fill_arena_capabilities,
    fill_missing_capabilities,
    fuzzy_match_model,
    normalize_score,
    validate_capability_scale,
)
from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.schemas import ModelMetadata


# ---------------------------------------------------------------------------
# normalize_score
# ---------------------------------------------------------------------------


class TestNormalizeScore:
    def test_rank_1_of_1(self):
        assert normalize_score(rank=1, total=1, elo=1500, elo_min=1500, elo_max=1500) == 100

    def test_rank_1_of_100(self):
        score = normalize_score(rank=1, total=100, elo=1550, elo_min=1400, elo_max=1550)
        assert score == 100

    def test_last_rank(self):
        score = normalize_score(rank=100, total=100, elo=1400, elo_min=1400, elo_max=1550)
        assert score == 0

    def test_middle_rank(self):
        score = normalize_score(rank=50, total=100, elo=1475, elo_min=1400, elo_max=1550)
        assert 40 <= score <= 60

    def test_rank_weight_dominates(self):
        # rank=1 but low ELO should still score high due to 70% rank weight
        score = normalize_score(
            rank=1, total=100, elo=1400, elo_min=1400, elo_max=1550,
            rank_weight=0.7,
        )
        assert score >= 70

    def test_clamped_to_0_100(self):
        score = normalize_score(rank=1, total=1, elo=2000, elo_min=1000, elo_max=1500)
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# fuzzy_match_model
# ---------------------------------------------------------------------------


class TestFuzzyMatchModel:
    ARENA_NAMES = [
        "claude-opus-4-6",
        "claude-opus-4-6-thinking",
        "gemini-3-pro",
        "gpt-5.2-high",
    ]

    def test_exact_match(self):
        assert fuzzy_match_model("claude-opus-4-6", self.ARENA_NAMES) == "claude-opus-4-6"

    def test_strip_date_suffix(self):
        assert fuzzy_match_model("claude-opus-4-6-20251101", self.ARENA_NAMES) == "claude-opus-4-6"

    def test_containment(self):
        result = fuzzy_match_model("gemini-3-pro-latest", self.ARENA_NAMES)
        assert result == "gemini-3-pro"

    def test_no_match(self):
        assert fuzzy_match_model("llama-3-70b", self.ARENA_NAMES) is None

    def test_case_insensitive(self):
        assert fuzzy_match_model("Claude-Opus-4-6", self.ARENA_NAMES) == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# fill_missing_capabilities
# ---------------------------------------------------------------------------


class TestFillMissingCapabilities:
    def test_fills_none_fields(self):
        caps = {"text": None, "code": None, "math": None, "vision": None, "search": None,
                "instruction_following": None, "creative_writing": None}
        arena_scores = {"text": 85, "code": 92}
        result = fill_missing_capabilities(caps, arena_scores, fetched_at="2026-02-18")

        assert result["text"] == 85
        assert result["code"] == 92
        assert result["math"] == 85  # from text category
        assert result["instruction_following"] == 85  # from text category
        assert result["vision"] is None  # no arena data
        assert result["search"] is None  # no arena data

    def test_preserves_existing_values(self):
        caps = {"text": 70, "code": None, "math": None, "vision": None, "search": None,
                "instruction_following": None, "creative_writing": None}
        arena_scores = {"text": 85, "code": 92}
        result = fill_missing_capabilities(caps, arena_scores)

        assert result["text"] == 70  # NOT overwritten
        assert result["code"] == 92  # filled

    def test_none_arena_scores(self):
        caps = {"text": None, "code": None}
        result = fill_missing_capabilities(caps, None)
        assert result["text"] is None

    def test_source_metadata(self):
        caps = {"text": None, "code": None, "math": None, "vision": None, "search": None,
                "instruction_following": None, "creative_writing": None}
        result = fill_missing_capabilities(caps, {"text": 85}, fetched_at="2026-02-18")
        assert "_source" in result
        assert result["_source"]["text"] == "arena:2026-02-18"


# ---------------------------------------------------------------------------
# validate_capability_scale
# ---------------------------------------------------------------------------


class TestValidateCapabilityScale:
    def test_valid_scores(self):
        caps = {"text": 85, "code": 92, "math": 78}
        assert validate_capability_scale(caps) == []

    def test_out_of_range(self):
        caps = {"text": 150, "code": -5}
        warnings = validate_capability_scale(caps)
        assert len(warnings) == 2

    def test_scale_mismatch(self):
        caps = {"text": 1, "code": 90}  # 90x ratio
        warnings = validate_capability_scale(caps)
        assert any("scale mismatch" in w for w in warnings)

    def test_skips_source_field(self):
        caps = {"text": 85, "_source": {"text": "arena:2026-02-18"}}
        assert validate_capability_scale(caps) == []

    def test_non_numeric(self):
        caps = {"text": "high"}
        warnings = validate_capability_scale(caps)
        assert any("non-numeric" in w for w in warnings)


# ---------------------------------------------------------------------------
# batch_fill_arena_capabilities
# ---------------------------------------------------------------------------


def _make_model(model_id: str, api_name: str, caps: dict | None = None) -> ModelMetadata:
    return ModelMetadata(
        model_id=model_id,
        display_name=api_name,
        provider=model_id.split(":")[0],
        api_model_name=api_name,
        capabilities=caps or {
            "text": None, "code": None, "search": None, "math": None,
            "instruction_following": None, "creative_writing": None, "vision": None,
        },
    )


def _make_entry(name: str, cat: str, score: int, rank: int, total: int) -> ArenaModelEntry:
    return ArenaModelEntry(
        name=name, organization="test", category=cat,
        arena_score=score, votes=100, rank=rank, total_in_category=total,
    )


class TestBatchFill:
    def test_fills_matched_models(self):
        models = [_make_model("anthropic:claude-opus-4-6", "claude-opus-4-6")]
        lb = ArenaLeaderboard(
            text=(_make_entry("claude-opus-4-6", "text", 1506, 1, 10),),
            code=(_make_entry("claude-opus-4-6", "code", 1561, 1, 10),),
        )
        result = batch_fill_arena_capabilities(models, lb)
        assert result[0].capabilities["text"] is not None
        assert result[0].capabilities["code"] is not None
        assert 0 <= result[0].capabilities["text"] <= 100

    def test_unmatched_models_unchanged(self):
        models = [_make_model("ollama:llama3", "llama3")]
        lb = ArenaLeaderboard(
            text=(_make_entry("claude-opus-4-6", "text", 1506, 1, 10),),
        )
        result = batch_fill_arena_capabilities(models, lb)
        assert result[0].capabilities["text"] is None

    def test_empty_leaderboard(self):
        models = [_make_model("anthropic:claude-opus-4-6", "claude-opus-4-6")]
        lb = ArenaLeaderboard()
        result = batch_fill_arena_capabilities(models, lb)
        assert result[0].capabilities["text"] is None

    def test_preserves_existing_capabilities(self):
        caps = {
            "text": 70, "code": None, "search": None, "math": None,
            "instruction_following": None, "creative_writing": None, "vision": None,
        }
        models = [_make_model("anthropic:claude-opus-4-6", "claude-opus-4-6", caps)]
        lb = ArenaLeaderboard(
            text=(_make_entry("claude-opus-4-6", "text", 1506, 1, 10),),
        )
        result = batch_fill_arena_capabilities(models, lb)
        assert result[0].capabilities["text"] == 70  # preserved
