"""Data structures for Arena leaderboard entries."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ArenaModelEntry:
    """One model's ranking in a specific Arena category."""

    name: str
    organization: str
    category: str  # "text" | "code" | "vision" | "search"
    arena_score: int
    votes: int
    rank: int
    total_in_category: int


@dataclass(frozen=True, slots=True)
class ArenaLeaderboard:
    """Aggregated leaderboard data across all categories."""

    text: tuple[ArenaModelEntry, ...] = ()
    code: tuple[ArenaModelEntry, ...] = ()
    vision: tuple[ArenaModelEntry, ...] = ()
    search: tuple[ArenaModelEntry, ...] = ()
    fetched_at: str = ""

    @property
    def categories(self) -> dict[str, tuple[ArenaModelEntry, ...]]:
        return {
            "text": self.text,
            "code": self.code,
            "vision": self.vision,
            "search": self.search,
        }

    @property
    def is_empty(self) -> bool:
        return not any(self.categories.values())

    def all_names(self) -> set[str]:
        """Return all unique model names across categories."""
        names: set[str] = set()
        for entries in self.categories.values():
            for entry in entries:
                names.add(entry.name)
        return names
