"""Tests for Arena scraper: parsing logic with mock HTML."""

import pytest

from route_agent.model_registry.arena.scraper import _parse_category_page
from route_agent.model_registry.arena.schemas import ArenaModelEntry


# ---------------------------------------------------------------------------
# _parse_category_page
# ---------------------------------------------------------------------------


def _wrap_html(text: str) -> str:
    """Wrap plain text in minimal HTML for parser input."""
    return f"<html><body>{text}</body></html>"


class TestParseCategoryPage:
    """Test cases for `_parse_category_page`."""

    SAMPLE_HTML = _wrap_html(
        "1 claude-opus-4-6-thinking Anthropic 1506 4,745 "
        "2 claude-opus-4-6 Anthropic 1503 5,540 "
        "3 gemini-3-pro Google 1486 36,354 "
        "4 grok-4.1-thinking xAI 1474 35,833 "
    )

    def test_parses_model_names(self):
        """Test parses model names."""
        entries = _parse_category_page(self.SAMPLE_HTML, "text")
        names = [e.name for e in entries]
        assert "claude-opus-4-6-thinking" in names
        assert "claude-opus-4-6" in names
        assert "gemini-3-pro" in names

    def test_parses_scores(self):
        """Test parses scores."""
        entries = _parse_category_page(self.SAMPLE_HTML, "text")
        by_name = {e.name: e for e in entries}
        assert by_name["claude-opus-4-6-thinking"].arena_score == 1506
        assert by_name["gemini-3-pro"].arena_score == 1486

    def test_parses_votes_with_commas(self):
        """Test parses votes with commas."""
        entries = _parse_category_page(self.SAMPLE_HTML, "text")
        by_name = {e.name: e for e in entries}
        assert by_name["claude-opus-4-6-thinking"].votes == 4745
        assert by_name["gemini-3-pro"].votes == 36354

    def test_assigns_ranks(self):
        """Test assigns ranks."""
        entries = _parse_category_page(self.SAMPLE_HTML, "text")
        assert entries[0].rank == 1
        assert entries[0].arena_score >= entries[1].arena_score

    def test_sets_category(self):
        """Test sets category."""
        entries = _parse_category_page(self.SAMPLE_HTML, "code")
        assert all(e.category == "code" for e in entries)

    def test_sets_total_in_category(self):
        """Test sets total in category."""
        entries = _parse_category_page(self.SAMPLE_HTML, "text")
        total = len(entries)
        assert all(e.total_in_category == total for e in entries)

    def test_empty_html(self):
        """Test empty html."""
        assert _parse_category_page("", "text") == []

    def test_no_matches(self):
        """Test no matches."""
        assert _parse_category_page(_wrap_html("hello world 123"), "text") == []
