"""Tests for profile_analyzer (向量画像分析器)。"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from route_agent.router_engine.constants import CLASS_DESCRIPTIONS
from route_agent.task_analyzer.profile_analyzer import (
    ProfileAnalyzer,
    ProfileMatchResult,
    _cosine_similarity,
)
from route_agent.task_analyzer.profile_storage import ProfileEmbeddingStorage


# ---------------------------------------------------------------------------
# _cosine_similarity 单元测试
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical() -> None:
    """相同向量相似度为 1.0。"""
    vec = [1.0, 2.0, 3.0]
    assert abs(_cosine_similarity(vec, vec) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal() -> None:
    """正交向量相似度为 0.0。"""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine_similarity(a, b)) < 1e-9


def test_cosine_similarity_zero_vector() -> None:
    """零向量返回 0.0。"""
    assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ---------------------------------------------------------------------------
# ProfileAnalyzer.match 测试（mock embedding 模型）
# ---------------------------------------------------------------------------


def _make_analyzer(
    class_embeddings: dict[str, list[float]],
    threshold: float = 0.5,
) -> ProfileAnalyzer:
    """构造一个带预设 embedding 的 ProfileAnalyzer（跳过 Ollama 调用）。"""
    storage = MagicMock(spec=ProfileEmbeddingStorage)
    storage.get_all_embeddings.return_value = {k: v for k, v in class_embeddings.items()}

    analyzer = ProfileAnalyzer(storage=storage, threshold=threshold)
    # 直接设置内部状态，跳过 Ollama 初始化
    analyzer._class_embeddings = dict(class_embeddings)
    analyzer._initialized = True
    return analyzer


def test_match_returns_best_class() -> None:
    """命中最高相似度的类池。"""
    # review 向量：code 方向；translation 向量：text 方向
    class_emb = {
        "review": [0.9, 0.1, 0.0],
        "translation": [0.1, 0.9, 0.0],
    }
    analyzer = _make_analyzer(class_emb, threshold=0.5)

    # 模拟 task embedding 更接近 review
    task_emb = [0.85, 0.15, 0.0]
    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = task_emb
        result = analyzer.match("审查代码质量")

    assert result is not None
    assert result.class_name == "review"
    assert result.similarity > 0.5


def test_match_returns_none_below_threshold() -> None:
    """所有相似度低于阈值时返回 None。"""
    # 类池向量都在一个方向，任务向量在完全不同方向
    class_emb = {
        "review": [1.0, 0.0, 0.0],
        "translation": [0.0, 1.0, 0.0],
    }
    analyzer = _make_analyzer(class_emb, threshold=0.9)

    # 任务向量与两个类池都有一定角度
    task_emb = [0.5, 0.5, 0.7]
    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = task_emb
        result = analyzer.match("一个模糊的任务描述")

    assert result is None


def test_match_logs_full_prompt_and_similarity_details_on_threshold_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """阈值未命中时应记录完整输入和各类相似度明细。"""
    class_emb = {
        "review": [1.0, 0.0, 0.0],
        "translation": [0.0, 1.0, 0.0],
    }
    analyzer = _make_analyzer(class_emb, threshold=0.9)
    task_prompt = "这是一个需要完整保留的路由输入，不能被截断。"
    task_emb = [0.5, 0.5, 0.7]
    review_score = round(_cosine_similarity(task_emb, class_emb["review"]), 6)
    translation_score = round(_cosine_similarity(task_emb, class_emb["translation"]), 6)

    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = task_emb
        with caplog.at_level(logging.INFO, logger="route_agent.task_analyzer.profile_analyzer"):
            result = analyzer.match(task_prompt)

    assert result is None
    miss_messages = [record.getMessage() for record in caplog.records if "PROFILE_MATCH_MISS" in record.getMessage()]
    assert len(miss_messages) == 1
    miss_message = miss_messages[0]
    assert task_prompt in miss_message
    assert '"class_name": "review"' in miss_message
    assert json.dumps(CLASS_DESCRIPTIONS["review"], ensure_ascii=False) in miss_message
    assert f'"similarity": {review_score}' in miss_message
    assert '"class_name": "translation"' in miss_message
    assert json.dumps(CLASS_DESCRIPTIONS["translation"], ensure_ascii=False) in miss_message
    assert f'"similarity": {translation_score}' in miss_message


def test_match_embedding_error_returns_none() -> None:
    """embedding 计算失败时返回 None 而非抛异常。"""
    class_emb = {"review": [1.0, 0.0]}
    analyzer = _make_analyzer(class_emb)

    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.side_effect = RuntimeError("ollama down")
        result = analyzer.match("任何任务")

    assert result is None


def test_match_logs_full_prompt_on_embedding_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """embedding 计算异常时应记录完整输入。"""
    class_emb = {"review": [1.0, 0.0]}
    analyzer = _make_analyzer(class_emb)
    task_prompt = "embedding 失败时也要把这段原始路由输入完整记下来。"

    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.side_effect = RuntimeError("ollama down")
        with caplog.at_level(logging.ERROR, logger="route_agent.task_analyzer.profile_analyzer"):
            result = analyzer.match(task_prompt)

    assert result is None
    error_messages = [record.getMessage() for record in caplog.records if "PROFILE_MATCH_EMBED_ERROR" in record.getMessage()]
    assert len(error_messages) == 1
    error_message = error_messages[0]
    assert json.dumps(task_prompt, ensure_ascii=False) in error_message
    assert "class_similarity_details=unavailable" in error_message
    assert "ollama down" in error_message


def test_match_empty_dimensions_returns_none() -> None:
    """无类池 embedding 时返回 None。"""
    analyzer = _make_analyzer({})

    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = [1.0]
        result = analyzer.match("任何任务")

    assert result is None


# ---------------------------------------------------------------------------
# ProfileAnalyzer.analyze 测试
# ---------------------------------------------------------------------------


def test_analyze_returns_task_analysis_result() -> None:
    """命中时 analyze 返回带维度画像的 TaskAnalysisResult。"""
    class_emb = {
        "review": [0.9, 0.1],
        "translation": [0.1, 0.9],
    }
    analyzer = _make_analyzer(class_emb, threshold=0.5)

    task_emb = [0.95, 0.05]
    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = task_emb
        result = analyzer.analyze("审查这段代码的安全性")

    assert result is not None
    assert result.task_class == "review"
    assert result.domain == "review"
    dim_names = {d.dimension for d in result.relevant_dimensions}
    # review 画像: code=8, text=5, math=4
    assert "code" in dim_names


def test_analyze_returns_none_when_no_match() -> None:
    """未命中时 analyze 返回 None。"""
    class_emb = {"review": [1.0, 0.0]}
    analyzer = _make_analyzer(class_emb, threshold=0.99)

    task_emb = [0.5, 0.87]
    with patch.object(analyzer, "_get_embeddings_model") as mock_model:
        mock_model.return_value.embed_query.return_value = task_emb
        result = analyzer.analyze("完全不匹配的任务")

    assert result is None
