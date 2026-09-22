from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sahne_avcisi.database import Database
from sahne_avcisi.fingerprint import Fingerprint


SOURCE = {
    "id": "local-source",
    "name": "Local Source",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


def fp(dhash: str, ahash: str) -> Fingerprint:
    """Build a fingerprint from hex text; hashes are stored as 64-bit integers."""
    return Fingerprint(dhash=int(dhash, 16), ahash=int(ahash, 16), width=320, height=180)


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.database.upsert_source(SOURCE)

    def test_foreign_keys_are_enforced_on_every_connection(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO media (source_id, title, category, adult, source_url) "
                    "VALUES ('missing-source', 't', 'movie-tv', 0, 'https://x/y')"
                )

    def test_search_cache_refreshes_when_frame_count_changes(self) -> None:
        media_id = self.database.create_media(
            source_id=SOURCE["id"],
            title="Film",
            source_url="https://video.example.com/watch/1",
            category="movie-tv",
            adult=False,
        )
        self.database.replace_frames(media_id, [(0, fp("0" * 16, "0" * 16))])
        query = fp("0" * 16, "0" * 16)
        first_results = self.database.search(query, allow_adult=False)
        self.assertEqual(len(first_results), 1)

        # Cache should reflect newly added frames once frame count changes.
        self.database.replace_frames(
            media_id,
            [(0, fp("0" * 16, "0" * 16)), (2000, fp("0" * 16, "0" * 16))],
        )
        second_results = self.database.search(query, allow_adult=False)
        self.assertEqual(len(second_results), 2)

    def test_frame_index_is_reused_between_searches(self) -> None:
        media_id = self.database.create_media(
            source_id=SOURCE["id"],
            title="Film",
            source_url="https://video.example.com/watch/1",
            category="movie-tv",
            adult=False,
        )
        self.database.replace_frames(media_id, [(0, fp("0" * 16, "0" * 16))])
        query = fp("0" * 16, "0" * 16)
        self.database.search(query, allow_adult=False)
        index_before = self.database._frame_index_cache
        self.database.search(query, allow_adult=False)
        self.assertIs(self.database._frame_index_cache, index_before)

    def test_replace_frames_deletes_old_frames_on_reindex(self) -> None:
        media_id = self.database.create_media(
            source_id=SOURCE["id"],
            title="Film",
            source_url="https://video.example.com/watch/1",
            category="movie-tv",
            adult=False,
        )
        self.database.replace_frames(
            media_id,
            [(0, fp("0" * 16, "0" * 16)), (2000, fp("f" * 16, "f" * 16))],
        )
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE media_id=?", (media_id,)
            ).fetchone()[0]
        self.assertEqual(count, 2)

        # Reindexing with fewer frames must remove the stale ones, not just add new ones.
        self.database.replace_frames(media_id, [(0, fp("1" * 16, "1" * 16))])
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT timestamp_ms, dhash FROM frames WHERE media_id=?", (media_id,)
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dhash"], int("1" * 16, 16))

    def test_replace_frames_is_single_transaction_bulk_write(self) -> None:
        media_id = self.database.create_media(
            source_id=SOURCE["id"],
            title="Film",
            source_url="https://video.example.com/watch/1",
            category="movie-tv",
            adult=False,
        )
        frames = [(index * 1000, fp(f"{index:016x}", f"{index:016x}")) for index in range(50)]
        self.database.replace_frames(media_id, frames)
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE media_id=?", (media_id,)
            ).fetchone()[0]
        self.assertEqual(count, 50)

    def test_requeue_stale_jobs_resets_long_running_jobs(self) -> None:
        self.database.enqueue_index_job(source_id=SOURCE["id"], page_url="https://video.example.com/watch/1")
        job = self.database.claim_index_job()
        self.assertIsNotNone(job)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE index_jobs SET started_at=datetime('now', '-90 minutes') WHERE id=?",
                (job["id"],),
            )
        requeued = self.database.requeue_stale_jobs(stale_minutes=60)
        self.assertEqual(requeued, 1)
        refreshed = self.database.list_index_jobs()[0]
        self.assertEqual(refreshed["status"], "queued")
        self.assertIsNone(refreshed["started_at"])

    def test_requeue_stale_jobs_leaves_recent_running_jobs_alone(self) -> None:
        self.database.enqueue_index_job(source_id=SOURCE["id"], page_url="https://video.example.com/watch/1")
        self.database.claim_index_job()
        requeued = self.database.requeue_stale_jobs(stale_minutes=60)
        self.assertEqual(requeued, 0)
        refreshed = self.database.list_index_jobs()[0]
        self.assertEqual(refreshed["status"], "running")


if __name__ == "__main__":
    unittest.main()
