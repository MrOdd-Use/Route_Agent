"""SQLite 持久化层：缓存类池描述的 embedding 向量。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from route_agent.task_analyzer.config import PROFILE_STORAGE_DB_PATH

_DEFAULT_DB_NAME = "profile_embeddings.db"


def _default_db_path() -> Path:
    """Execute `_default_db_path`."""
    if PROFILE_STORAGE_DB_PATH:
        return Path(PROFILE_STORAGE_DB_PATH)
    return Path(__file__).resolve().parents[2] / "data" / _DEFAULT_DB_NAME


_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS class_profile_embeddings (
    class_name       TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    embedding        TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (class_name)
);
"""


def _hash_text(text: str) -> str:
    """Execute `_hash_text`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ProfileEmbeddingStorage:
    """类池描述 embedding 的 SQLite 缓存。"""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the instance."""
        self._db_path = db_path or _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Execute `_get_conn`."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        """Execute `_init_schema`."""
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES_SQL)

    def get_embedding(self, class_name: str, description: str) -> list[float] | None:
        """如果描述未变则返回缓存的 embedding，否则返回 None。"""
        desc_hash = _hash_text(description)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT embedding, description_hash FROM class_profile_embeddings WHERE class_name = ?",
            (class_name,),
        ).fetchone()
        if row is None or row["description_hash"] != desc_hash:
            return None
        return json.loads(row["embedding"])

    def save_embedding(
        self,
        class_name: str,
        description: str,
        embedding: list[float],
    ) -> None:
        """存储或更新类池描述 embedding。"""
        desc_hash = _hash_text(description)
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO class_profile_embeddings (class_name, description_hash, embedding)
            VALUES (?, ?, ?)
            ON CONFLICT(class_name) DO UPDATE SET
                description_hash = excluded.description_hash,
                embedding = excluded.embedding,
                created_at = datetime('now')
            """,
            (class_name, desc_hash, json.dumps(embedding)),
        )
        conn.commit()

    def get_all_embeddings(
        self,
        descriptions: dict[str, str],
    ) -> dict[str, list[float] | None]:
        """批量获取：对每个类池返回缓存的 embedding 或 None（需要重算）。"""
        result: dict[str, list[float] | None] = {}
        for class_name, desc in descriptions.items():
            result[class_name] = self.get_embedding(class_name, desc)
        return result
