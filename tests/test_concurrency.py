from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

from sahne_avcisi.database import BUSY_TIMEOUT_MS, Database
from sahne_avcisi.worker import HostLimiter

SOURCE = {
    "id": "bulk",
    "name": "Bulk Source",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


class JobClaimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.database = Database(Path(self._directory.name) / "jobs.sqlite3")
        self.database.upsert_source(SOURCE)

    def test_connections_wait_for_a_lock_instead_of_failing(self) -> None:
        with self.database.connect() as connection:
            timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout, BUSY_TIMEOUT_MS)

    def test_a_job_is_never_handed_to_two_workers(self) -> None:
        job_count = 40
        for index in range(job_count):
            self.database.enqueue_index_job(
                source_id=SOURCE["id"], page_url=f"https://video.example.com/watch/{index}"
            )

        claimed: list[int] = []
        claimed_lock = threading.Lock()
        start = threading.Event()

        def claim_all() -> None:
            start.wait()
            while True:
                job = self.database.claim_index_job()
                if job is None:
                    return
                with claimed_lock:
                    claimed.append(job["id"])

        threads = [threading.Thread(target=claim_all) for _ in range(8)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(timeout=60)

        counts = Counter(claimed)
        self.assertEqual(len(claimed), job_count)
        self.assertEqual(set(counts.values()), {1}, "bir iş birden çok kez kapılmış")

    def test_concurrent_writers_do_not_hit_a_locked_database(self) -> None:
        errors: list[Exception] = []

        def write(index: int) -> None:
            try:
                for step in range(15):
                    self.database.enqueue_index_job(
                        source_id=SOURCE["id"],
                        page_url=f"https://video.example.com/w/{index}-{step}",
                    )
            except sqlite3.OperationalError as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.database.list_index_jobs(limit=500)), 90)


class HostLimiterTests(unittest.TestCase):
    def test_transfers_to_one_host_are_capped(self) -> None:
        limiter = HostLimiter(2)
        active = 0
        peak = 0
        lock = threading.Lock()

        def hit() -> None:
            nonlocal active, peak
            with limiter.hold("https://archive.org/details/x"):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with lock:
                    active -= 1

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertLessEqual(peak, 2)

    def test_a_busy_host_does_not_block_a_different_one(self) -> None:
        limiter = HostLimiter(1)
        released = threading.Event()
        other_finished = threading.Event()

        def hold_first() -> None:
            with limiter.hold("https://slow.example/a"):
                released.wait(timeout=5)

        def hit_second() -> None:
            with limiter.hold("https://fast.example/b"):
                other_finished.set()

        first = threading.Thread(target=hold_first)
        first.start()
        second = threading.Thread(target=hit_second)
        second.start()

        self.assertTrue(other_finished.wait(timeout=5), "ayrı alan adı beklemek zorunda kaldı")
        released.set()
        first.join(timeout=10)
        second.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
