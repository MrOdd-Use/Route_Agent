"""向量画像分析器：用本地 Ollama embedding 将任务与类池描述做相似度匹配。

命中后从 CLASS_DIMENSION_PROFILES 取出预定义维度分数，组装 TaskAnalysisResult。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from route_agent.router_engine.constants import CLASS_DESCRIPTIONS, CLASS_DIMENSION_PROFILES
from route_agent.task_analyzer.config import (
    PROFILE_EMBEDDING_BASE_URL,
    PROFILE_EMBEDDING_MODEL,
    PROFILE_MATCH_THRESHOLD,
    TASK_CLASS_DESCRIPTIONS,
)
from route_agent.task_analyzer.profile_storage import ProfileEmbeddingStorage
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult

logger = logging.getLogger(__name__)

# nomic-embed-text 上下文 8192 tokens；中文约 2 token/字符，留余量截断到 4000 字符
_MAX_EMBED_CHARS = 4000


@dataclass(frozen=True)
class ProfileMatchResult:
    """向量画像匹配结果。"""

    class_name: str
    similarity: float
    all_scores: dict[str, float] = field(default_factory=dict)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Execute `_cosine_similarity`."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class ProfileAnalyzer:
    """基于向量相似度的任务分析器。

    将任务 input 的 embedding 与各类池描述的 embedding 做余弦相似度。
    命中则返回 TaskAnalysisResult，全部低于阈值则返回 None。
    """

    def __init__(
        self,
        storage: ProfileEmbeddingStorage | None = None,
        threshold: float = PROFILE_MATCH_THRESHOLD,
    ) -> None:
        """Initialize the instance."""
        self._storage = storage or ProfileEmbeddingStorage()
        self._threshold = threshold
        self._embeddings_model = None
        self._class_embeddings: dict[str, list[float]] = {}
        self._initialized = False

    def _get_embeddings_model(self):
        """懒加载 Ollama embedding 模型。"""
        if self._embeddings_model is None:
            from langchain_ollama import OllamaEmbeddings

            self._embeddings_model = OllamaEmbeddings(
                model=PROFILE_EMBEDDING_MODEL,
                base_url=PROFILE_EMBEDDING_BASE_URL,
            )
        return self._embeddings_model

    def _ensure_class_embeddings(self) -> None:
        """确保所有类池描述的 embedding 已加载（缓存优先，缺失则计算并持久化）。"""
        if self._initialized:
            return

        descriptions = CLASS_DESCRIPTIONS
        cached = self._storage.get_all_embeddings(descriptions)

        missing: dict[str, str] = {}
        for class_name, embedding in cached.items():
            if embedding is not None:
                self._class_embeddings[class_name] = embedding
            else:
                missing[class_name] = descriptions[class_name]

        if missing:
            model = self._get_embeddings_model()
            texts = list(missing.values())
            names = list(missing.keys())
            try:
                vectors = model.embed_documents(texts)
            except Exception as exc:
                logger.error("Ollama embedding 调用失败: %s", exc)
                raise

            for name, text, vec in zip(names, texts, vectors):
                self._class_embeddings[name] = vec
                self._storage.save_embedding(name, text, vec)

        self._initialized = True
        logger.info(
            "CLASS_EMBEDDINGS_INIT | total=%d cached=%d computed=%d",
            len(descriptions), len(descriptions) - len(missing), len(missing),
        )

    def match(self, task_prompt: str) -> ProfileMatchResult | None:
        """计算任务与所有类池的相似度，返回最佳匹配或 None。"""
        self._ensure_class_embeddings()

        logger.debug(
            "PROFILE_MATCH | task_len=%d preview=%.100s",
            len(task_prompt), task_prompt,
        )

        model = self._get_embeddings_model()
        truncated = task_prompt[:_MAX_EMBED_CHARS]
        if len(task_prompt) > _MAX_EMBED_CHARS:
            logger.warning(
                "PROFILE_MATCH | task_prompt 超长截断: original=%d truncated=%d",
                len(task_prompt), len(truncated),
            )
        try:
            task_embedding = model.embed_query(truncated)
        except Exception as exc:
            logger.error("任务 embedding 计算失败: %s", exc)
            return None

        scores: dict[str, float] = {}
        for class_name, class_emb in self._class_embeddings.items():
            scores[class_name] = _cosine_similarity(task_embedding, class_emb)

        if not scores:
            return None

        best_class = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_class]

        if best_score < self._threshold:
            logger.info(
                "向量画像匹配：全部低于阈值 %.2f，最高 %s=%.4f",
                self._threshold,
                best_class,
                best_score,
            )
            return None

        return ProfileMatchResult(
            class_name=best_class,
            similarity=best_score,
            all_scores=scores,
        )

    def analyze(self, task_prompt: str) -> TaskAnalysisResult | None:
        """完整分析：向量匹配 + 维度画像组装。命中返回 TaskAnalysisResult，否则 None。"""
        hit = self.match(task_prompt)
        if hit is None:
            return None

        profile = CLASS_DIMENSION_PROFILES.get(hit.class_name, {})
        description = TASK_CLASS_DESCRIPTIONS.get(
            hit.class_name,
            CLASS_DESCRIPTIONS.get(hit.class_name, hit.class_name),
        )

        relevant_dimensions = tuple(
            DimensionScore(
                dimension=dim,
                score=score,
                reasoning=f"向量画像匹配命中类池 '{hit.class_name}'（相似度 {hit.similarity:.4f}），使用预定义维度分数",
            )
            for dim, score in profile.items()
            if score > 0
        )

        return TaskAnalysisResult(
            domain=hit.class_name,
            domain_description=description,
            relevant_dimensions=relevant_dimensions,
            task_class=hit.class_name,
        )
