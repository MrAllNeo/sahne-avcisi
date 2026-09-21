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
    section TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    discovered_count INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    restored_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS catalog_memberships (
    catalog_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    missing_streak INTEGER NOT NULL DEFAULT 0,
    present INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (catalog_id, source_id)
);

CREATE TABLE IF NOT EXISTS source_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES catalog_sync_runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_catalog_sync_runs_catalog_started
ON catalog_sync_runs(catalog_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_memberships_present
ON catalog_memberships(catalog_id, present);

CREATE INDEX IF NOT EXISTS idx_source_events_created
ON source_events(created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_sources(connection)
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _migrate_sources(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)").fetchall()}
        if "section" not in columns:
            connection.execute("ALTER TABLE sources ADD COLUMN section TEXT NOT NULL DEFAULT ''")
        if "tags_json" not in columns:
            connection.execute("ALTER TABLE sources ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")

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
                INSERT INTO sources (
                    id, name, base_url, kind, category, adult, status, priority, notes,
                    section, tags_json, discovered_from
                )
                VALUES (
                    :id, :name, :base_url, :kind, :category, :adult, :status, :priority, :notes,
                    :section, :tags_json, :discovered_from
                )
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    base_url=excluded.base_url,
                    kind=excluded.kind,
                    category=excluded.category,
                    adult=excluded.adult,
                    status=excluded.status,
                    priority=excluded.priority,
                    notes=excluded.notes,
                    section=excluded.section,
                    tags_json=excluded.tags_json,
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
                    "section": source.get("section", ""),
                    "tags_json": json.dumps(source.get("tags", []), ensure_ascii=False, sort_keys=True),
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
            latest_sync = connection.execute(
                """
                SELECT status, discovered_count, created_count, updated_count, missing_count,
                       restored_count, completed_at, feed_url
                FROM catalog_sync_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "sources": source_count,
            "active_sources": active_count,
            "media": media_count,
            "frames": frame_count,
            "latest_sync": dict(latest_sync) if latest_sync else None,
        }

    def start_catalog_sync(self, catalog_id: str, feed_url: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO catalog_sync_runs (catalog_id, feed_url) VALUES (?, ?)",
                (catalog_id, feed_url),
            )
            return int(cursor.lastrowid)

    def finish_catalog_sync(
        self,
        run_id: int,
        *,
        status: str,
        counts: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        values = counts or {}
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE catalog_sync_runs
                SET status=?, discovered_count=?, created_count=?, updated_count=?,
                    missing_count=?, restored_count=?, error=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    status,
                    int(values.get("discovered", 0)),
                    int(values.get("created", 0)),
                    int(values.get("updated", 0)),
                    int(values.get("missing", 0)),
                    int(values.get("restored", 0)),
                    error,
                    run_id,
                ),
            )

    def apply_catalog_snapshot(self, run_id: int, catalog_id: str, sources: list[dict]) -> dict[str, int]:
        counts = {"discovered": len(sources), "created": 0, "updated": 0, "missing": 0, "restored": 0}
        seen_ids = {source["id"] for source in sources}
        tracked_fields = (
            "name",
            "base_url",
            "kind",
            "category",
            "adult",
            "status",
            "priority",
            "notes",
            "section",
            "tags_json",
        )

        with self.connect() as connection:
            for source in sources:
                normalized = {
                    "id": source["id"],
                    "name": source["name"],
                    "base_url": source["base_url"],
                    "kind": source.get("kind", "unknown"),
                    "category": source.get("category", "mixed"),
                    "adult": int(bool(source.get("adult", False))),
                    "status": source.get("status", "review-required"),
                    "priority": int(source.get("priority", 0)),
                    "notes": source.get("notes", ""),
                    "section": source.get("section", ""),
                    "tags_json": json.dumps(source.get("tags", []), ensure_ascii=False, sort_keys=True),
                    "discovered_from": source.get("discovered_from"),
                }
                existing = connection.execute("SELECT * FROM sources WHERE id=?", (source["id"],)).fetchone()
                changed: dict[str, dict] = {}
                if existing is None:
                    counts["created"] += 1
                    event_type = "discovered"
                else:
                    for field in tracked_fields:
                        before = existing[field]
                        after = normalized[field]
                        if before != after:
                            changed[field] = {"before": before, "after": after}
                    event_type = "updated" if changed else ""
                    if changed:
                        counts["updated"] += 1

                connection.execute(
                    """
                    INSERT INTO sources (
                        id, name, base_url, kind, category, adult, status, priority, notes,
                        section, tags_json, discovered_from
                    ) VALUES (
                        :id, :name, :base_url, :kind, :category, :adult, :status, :priority, :notes,
                        :section, :tags_json, :discovered_from
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, base_url=excluded.base_url, kind=excluded.kind,
                        category=excluded.category, adult=excluded.adult, status=excluded.status,
                        priority=excluded.priority, notes=excluded.notes, section=excluded.section,
                        tags_json=excluded.tags_json,
                        discovered_from=COALESCE(sources.discovered_from, excluded.discovered_from),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    normalized,
                )

                membership = connection.execute(
                    "SELECT present FROM catalog_memberships WHERE catalog_id=? AND source_id=?",
                    (catalog_id, source["id"]),
                ).fetchone()
                restored = membership is not None and not bool(membership["present"])
                connection.execute(
                    """
                    INSERT INTO catalog_memberships (catalog_id, source_id)
                    VALUES (?, ?)
                    ON CONFLICT(catalog_id, source_id) DO UPDATE SET
                        last_seen_at=CURRENT_TIMESTAMP, missing_streak=0, present=1
                    """,
                    (catalog_id, source["id"]),
                )
                if restored:
                    counts["restored"] += 1
                    self._insert_event(connection, run_id, source["id"], "restored", {})
                if event_type:
                    self._insert_event(connection, run_id, source["id"], event_type, changed)

            memberships = connection.execute(
                "SELECT source_id, present, missing_streak FROM catalog_memberships WHERE catalog_id=?",
                (catalog_id,),
            ).fetchall()
            for membership in memberships:
                source_id = membership["source_id"]
                if source_id in seen_ids:
                    continue
                connection.execute(
                    """
                    UPDATE catalog_memberships
                    SET present=0, missing_streak=missing_streak+1
                    WHERE catalog_id=? AND source_id=?
                    """,
                    (catalog_id, source_id),
                )
                if membership["present"]:
                    counts["missing"] += 1
                    self._insert_event(
                        connection,
                        run_id,
                        source_id,
                        "missing",
                        {"missing_streak": int(membership["missing_streak"]) + 1},
                    )
        return counts

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        run_id: int,
        source_id: str,
        event_type: str,
        details: dict,
    ) -> None:
        connection.execute(
            "INSERT INTO source_events (run_id, source_id, event_type, details_json) VALUES (?, ?, ?, ?)",
            (run_id, source_id, event_type, json.dumps(details, ensure_ascii=False, sort_keys=True)),
        )

    def list_catalog_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM catalog_sync_runs ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_source_events(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.event_type, e.details_json, e.created_at,
                       s.id AS source_id, s.name, s.base_url, s.category, s.section
                FROM source_events e
                JOIN sources s ON s.id=e.source_id
                ORDER BY e.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event.pop("details_json"))
            events.append(event)
        return events

    def list_adapter_queue(self, *, include_adult: bool = False, limit: int = 100) -> list[dict]:
        conditions = ["status IN ('review-required', 'adapter-required')"]
        if not include_adult:
            conditions.append("adult=0")
        query = f"""
            SELECT id, name, base_url, kind, category, adult, status, priority,
                   section, tags_json, updated_at
            FROM sources
            WHERE {' AND '.join(conditions)}
            ORDER BY priority DESC, updated_at DESC, name COLLATE NOCASE
            LIMIT ?
        """
        with self.connect() as connection:
            rows = connection.execute(query, (max(1, min(limit, 500)),)).fetchall()
        queue = []
        for row in rows:
            item = dict(row)
            item["adult"] = bool(item["adult"])
            item["tags"] = json.loads(item.pop("tags_json"))
            queue.append(item)
        return queue


def _format_timestamp(timestamp_ms: int) -> str:
    total_seconds = timestamp_ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
