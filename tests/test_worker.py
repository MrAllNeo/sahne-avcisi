from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sahne_avcisi import worker as worker_module
from sahne_avcisi.database import Database
from sahne_avcisi.worker import IndexWorker


SOURCE = {
    "id": "example-video",
    "name": "Example Video",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


class ExplodingRegistry:
    """A registry whose resolve() raises an error type run_once() does not special-case."""

    def resolve(self, source: dict, page_url: str):  # noqa: ANN001
        raise RuntimeError("beklenmeyen adaptör çökmesi")


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.database.upsert_source(SOURCE)

    def test_unexpected_error_marks_job_failed_instead_of_crashing(self) -> None:
        self.database.enqueue_index_job(
            source_id=SOURCE["id"], page_url="https://video.example.com/watch/1"
        )
        worker = IndexWorker(self.database, registry=ExplodingRegistry())

        result = worker.run_once()

        self.assertEqual(result["status"], "failed")
        stored = self.database.list_index_jobs()[0]
        self.assertEqual(stored["status"], "failed")
        self.assertIn("beklenmeyen adaptör çökmesi", stored["error"])

    def test_main_requeues_stale_running_jobs_before_processing(self) -> None:
        self.database.enqueue_index_job(
            source_id=SOURCE["id"], page_url="https://video.example.com/watch/1"
        )
        job = self.database.claim_index_job()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE index_jobs SET started_at=datetime('now', '-90 minutes') WHERE id=?",
                (job["id"],),
            )

        database_path = Path(self.temp_dir.name) / "test.sqlite3"
        sources_path = Path(self.temp_dir.name) / "sources.json"
        sources_path.write_text(json.dumps([SOURCE]), encoding="utf-8")

        argv = [
            "sahne-worker",
            "--database",
            str(database_path),
            "--sources",
            str(sources_path),
            "--once",
            "--stale-minutes",
            "60",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            worker_module.IndexWorker, "run_once", return_value=None
        ):
            worker_module.main()

        reloaded = Database(database_path)
        refreshed = reloaded.list_index_jobs()[0]
        self.assertEqual(refreshed["status"], "queued")

    def test_main_leaves_recently_started_jobs_running(self) -> None:
        self.database.enqueue_index_job(
            source_id=SOURCE["id"], page_url="https://video.example.com/watch/1"
        )
        self.database.claim_index_job()

        database_path = Path(self.temp_dir.name) / "test.sqlite3"
        sources_path = Path(self.temp_dir.name) / "sources.json"
        sources_path.write_text(json.dumps([SOURCE]), encoding="utf-8")

        argv = [
            "sahne-worker",
            "--database",
            str(database_path),
            "--sources",
            str(sources_path),
            "--once",
            "--stale-minutes",
            "60",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            worker_module.IndexWorker, "run_once", return_value=None
        ):
            worker_module.main()

        reloaded = Database(database_path)
        refreshed = reloaded.list_index_jobs()[0]
        self.assertEqual(refreshed["status"], "running")


if __name__ == "__main__":
    unittest.main()
