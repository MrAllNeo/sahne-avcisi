from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .fingerprint import Fingerprint, similarity


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    adult INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    discovered_from TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    external_id TEXT,
    title TEXT NOT NULL,
    episode TEXT,
    category TEXT NOT NULL,
    adult INTEGER NOT NULL DEFAULT 0,
    source_url TEXT NOT NULL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, source_url)
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    dhash TEXT NOT NULL,
    ahash TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    UNIQUE(media_id, timestamp_ms)
);

CREATE INDEX IF NOT EXISTS idx_frames_media ON frames(media_id);
CREATE INDEX IF NOT EXISTS idx_media_adult ON media(adult);
CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_source(self, source: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sources (id, name, base_url, kind, category, adult, status, priority, notes, discovered_from)
                VALUES (:id, :name, :base_url, :kind, :category, :adult, :status, :priority, :notes, :discovered_from)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    base_url=excluded.base_url,
                    kind=excluded.kind,
                    category=excluded.category,
                    adult=excluded.adult,
                    status=excluded.status,
                    priority=excluded.priority,
                    notes=excluded.notes,
                    discovered_from=excluded.discovered_from,
                    updated_at=CURRENT_TIMESTAMP
                """,
                {
                    "id": source["id"],
                    "name": source["name"],
                    "base_url": source["base_url"],
                    "kind": source.get("kind", "unknown"),
                    "category": source.get("category", "mixed"),
                    "adult": int(bool(source.get("adult", False))),
                    "status": source.get("status", "review-required"),
                    "priority": int(source.get("priority", 0)),
                    "notes": source.get("notes", ""),
                    "discovered_from": source.get("discovered_from"),
                },
            )

    def list_sources(self, include_adult: bool = False) -> list[dict]:
        query = "SELECT * FROM sources"
        params: tuple = ()
        if not include_adult:
            query += " WHERE adult=0"
        query += " ORDER BY priority DESC, name COLLATE NOCASE"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def create_media(
        self,
        *,
        source_id: str,
        title: str,
        source_url: str,
        category: str,
        adult: bool,
        episode: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO media (source_id, title, episode, category, adult, source_url, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_url) DO UPDATE SET
                    title=excluded.title,
                    episode=excluded.episode,
                    category=excluded.category,
                    adult=excluded.adult,
                    duration_ms=excluded.duration_ms
                """,
                (source_id, title, episode, category, int(adult), source_url, duration_ms),
            )
            row = connection.execute(
                "SELECT id FROM media WHERE source_id=? AND source_url=?", (source_id, source_url)
            ).fetchone()
            return int(row["id"])

    def add_frame(self, media_id: int, timestamp_ms: int, fingerprint: Fingerprint) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO frames (media_id, timestamp_ms, dhash, ahash, width, height)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id, timestamp_ms) DO UPDATE SET
                    dhash=excluded.dhash,
                    ahash=excluded.ahash,
                    width=excluded.width,
                    height=excluded.height
                """,
                (
                    media_id,
                    timestamp_ms,
                    fingerprint.dhash,
                    fingerprint.ahash,
                    fingerprint.width,
                    fingerprint.height,
                ),
            )

    def search(
        self,
        fingerprint: Fingerprint,
        *,
        allow_adult: bool,
        category: str = "all",
        limit: int = 8,
        minimum_similarity: float = 0.55,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[str] = []
        if not allow_adult:
            conditions.append("m.adult=0")
        if category in {"movie-tv", "anime", "adult", "adult-animation"}:
            conditions.append("m.category=?")
            params.append(category)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"""
            SELECT f.timestamp_ms, f.dhash, f.ahash, m.title, m.episode, m.category,
                   m.adult, m.source_url, s.name AS source_name
            FROM frames f
            JOIN media m ON m.id=f.media_id
            JOIN sources s ON s.id=m.source_id
            {where}
        """
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()

        scored = []
        for row in rows:
            item = dict(row)
            raw_similarity = similarity(fingerprint, item.pop("dhash"), item.pop("ahash"))
            if raw_similarity < minimum_similarity:
                continue
            item["similarity"] = round(raw_similarity * 100, 2)
            item["timestamp"] = _format_timestamp(int(item["timestamp_ms"]))
            item["adult"] = bool(item["adult"])
            scored.append(item)
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[: max(1, min(limit, 25))]

    def stats(self) -> dict:
        with self.connect() as connection:
            source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            media_count = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            frame_count = connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
            active_count = connection.execute("SELECT COUNT(*) FROM sources WHERE status='active'").fetchone()[0]
        return {
            "sources": source_count,
            "active_sources": active_count,
            "media": media_count,
            "frames": frame_count,
        }


def _format_timestamp(timestamp_ms: int) -> str:
    total_seconds = timestamp_ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
