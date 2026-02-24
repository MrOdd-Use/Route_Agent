"""Legacy task analysis fallback helpers."""

from __future__ import annotations

from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult

# Characters beyond which a task is considered maximum-length for complexity scoring.
_COMPLEXITY_LENGTH_CEILING = 800


def detect_task_type(task: str) -> str:
    """Execute `detect_task_type`."""
    text = task.lower()
    keyword_map = {
        "coding": [
            "code",
            "coding",
            "python",
            "bug",
            "debug",
            "function",
            "script",
            "sql",
            "programming",
        ],
        "translation": ["translate", "translation", "localize"],
        "scrape": ["scrape", "crawler", "crawl", "xpath", "selector", "html"],
        "extraction": ["extract", "extraction", "entity", "schema", "structured", "json"],
        "summarization": ["summarize", "summary", "tldr", "tl;dr", "brief"],
        "classification": ["classify", "classification", "label", "sentiment", "categorize"],
        "rewrite": ["rewrite", "paraphrase", "polish", "rephrase"],
        "review": ["review", "audit", "critique", "evaluate", "assessment"],
        "reasoning": ["reason", "prove", "analyze", "analysis", "deduce"],
        "math": ["math", "equation", "calculate", "formula", "algebra"],
    }
    for task_type, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            return task_type
    return "qa"


def estimate_complexity(task: str, task_type: str) -> float:
    """Execute `estimate_complexity`."""
    length_score = min(len(task) / _COMPLEXITY_LENGTH_CEILING, 1.0)
    base = 0.2 + 0.5 * length_score
    if task_type in {"coding", "review", "reasoning", "math"}:
        base += 0.2
    return max(0.0, min(base, 1.0))


def _score(complexity: float, delta: int = 0) -> int:
    """Execute `_score`."""
    normalized = max(0.0, min(complexity, 1.0))
    score = int(round(normalized * 9.0)) + 1 + delta
    return max(1, min(score, 10))


def build_legacy_analysis(task_type: str, complexity: float) -> TaskAnalysisResult:
    """Execute `build_legacy_analysis`."""
    dimensions_by_task = {
        "coding": ("code", "math", "instruction_following"),
        "translation": ("text", "instruction_following"),
        "scrape": ("text", "search", "instruction_following"),
        "extraction": ("text", "instruction_following"),
        "summarization": ("text", "instruction_following"),
        "classification": ("text", "instruction_following"),
        "rewrite": ("text", "creative_writing", "instruction_following"),
        "review": ("code", "text", "instruction_following"),
        "reasoning": ("math", "text"),
        "math": ("math", "text"),
        "qa": ("text", "instruction_following"),
    }
    descriptions = {
        "coding": "Software implementation and debugging tasks.",
        "translation": "Natural language translation tasks.",
        "scrape": "Web scraping and page extraction tasks.",
        "extraction": "Structured information extraction tasks.",
        "summarization": "Condensing long-form text into concise summaries.",
        "classification": "Text or data classification tasks.",
        "rewrite": "Text rewriting and style adaptation tasks.",
        "review": "Code or content review and critique tasks.",
        "reasoning": "Multi-step reasoning and analysis tasks.",
        "math": "Mathematical problem solving tasks.",
        "qa": "General question answering tasks.",
    }

    selected_dimensions = dimensions_by_task.get(task_type, ("text",))
    scores: list[int] = [_score(complexity)]
    for index in range(1, len(selected_dimensions)):
        scores.append(_score(complexity, delta=-index))

    relevant_dimensions = tuple(
        DimensionScore(
            dimension=dimension,
            score=scores[index],
            reasoning=(
                "Legacy fallback heuristic based on keyword task type "
                "and prompt-length complexity estimate."
            ),
        )
        for index, dimension in enumerate(selected_dimensions)
    )

    return TaskAnalysisResult(
        domain=task_type,
        domain_description=descriptions.get(task_type, descriptions["qa"]),
        relevant_dimensions=relevant_dimensions,
    )
