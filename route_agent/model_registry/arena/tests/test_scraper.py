"""Tests for Arena scraper: parsing logic with mock HTML."""

import pytest

from route_agent.model_registry.arena.scraper import (
    _extract_next_data,
    _parse_entries_from_text,
    _enrich_organizations,
)
from route_agent.model_registry.arena.schemas import ArenaModelEntry


# ---------------------------------------------------------------------------
# _parse_entries_from_text
# ---------------------------------------------------------------------------


class TestParseEntriesFromText:
    SAMPLE_TEXT = (
        "1 claude-opus-4-6-thinking Anthropic 1506 4,745 "
        "2 claude-opus-4-6 Anthropic 1503 5,540 "
        "3 gemini-3-pro Google 1486 36,354 "
        "4 grok-4.1-thinking xAI 1474 35,833 "
    )

    def test_parses_model_names(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "text")
        names = [e.name for e in entries]
        assert "claude-opus-4-6-thinking" in names
        assert "claude-opus-4-6" in names
        assert "gemini-3-pro" in names

    def test_parses_scores(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "text")
        by_name = {e.name: e for e in entries}
        assert by_name["claude-opus-4-6-thinking"].arena_score == 1506
        assert by_name["gemini-3-pro"].arena_score == 1486

    def test_parses_votes_with_commas(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "text")
        by_name = {e.name: e for e in entries}
        assert by_name["claude-opus-4-6-thinking"].votes == 4745
        assert by_name["gemini-3-pro"].votes == 36354

    def test_assigns_ranks(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "text")
        assert entries[0].rank == 1
        assert entries[0].arena_score >= entries[1].arena_score

    def test_sets_category(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "code")
        assert all(e.category == "code" for e in entries)

    def test_sets_total_in_category(self):
        entries = _parse_entries_from_text(self.SAMPLE_TEXT, "text")
        total = len(entries)
        assert all(e.total_in_category == total for e in entries)

    def test_empty_text(self):
        assert _parse_entries_from_text("", "text") == []

    def test_no_matches(self):
        assert _parse_entries_from_text("hello world 123", "text") == []


# ---------------------------------------------------------------------------
# _extract_next_data
# ---------------------------------------------------------------------------


class TestExtractNextData:
    def test_extracts_json_objects(self):
        html = '''<script>self.__next_f.push([1, "{\\"publicName\\":\\"claude-opus-4-6\\",\\"organization\\":\\"anthropic\\",\\"rank\\":1}"])</script>'''
        results = _extract_next_data(html)
        assert len(results) >= 1
        assert results[0]["publicName"] == "claude-opus-4-6"

    def test_handles_no_matches(self):
        assert _extract_next_data("<html><body>no data</body></html>") == []

    def test_handles_malformed_json(self):
        html = '''<script>self.__next_f.push([1, "{not valid json at all}"])</script>'''
        results = _extract_next_data(html)
        assert results == []


# ---------------------------------------------------------------------------
# _enrich_organizations
# ---------------------------------------------------------------------------


class TestEnrichOrganizations:
    def test_fills_organization(self):
        entries = [
            ArenaModelEntry(
                name="claude-opus-4-6", organization="",
                category="text", arena_score=1506, votes=100,
                rank=1, total_in_category=1,
            ),
        ]
        next_data = [
            {"publicName": "claude-opus-4-6", "organization": "anthropic"},
        ]
        result = _enrich_organizations(entries, next_data)
        assert result[0].organization == "anthropic"

    def test_preserves_existing_org(self):
        entries = [
            ArenaModelEntry(
                name="unknown-model", organization="",
                category="text", arena_score=1400, votes=50,
                rank=1, total_in_category=1,
            ),
        ]
        result = _enrich_organizations(entries, [])
        assert result[0].organization == ""
