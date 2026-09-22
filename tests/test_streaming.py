from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sahne_avcisi.database import Database
from sahne_avcisi.indexer import (
    StreamingUnsupportedError,
    extract_frames_from_stream,
    index_stream,
)

SOURCE = {
    "id": "streamed",
    "name": "Streamed Source",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


def make_video(path: Path, seconds: int = 6) -> bytes:
    """Render a short clip whose picture changes over time."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(path), "-y",
        ],
        check=True,
    )
    return path.read_bytes()


def chunked(data: bytes, size: int = 4096):
    for start in range(0, len(data), size):
        yield data[start : start + size]


class ExtractFromStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)

    def test_frames_are_sampled_without_touching_disk(self) -> None:
        data = make_video(self.root / "clip.mp4", seconds=6)
        output = self.root / "frames"
        output.mkdir()

        frames = extract_frames_from_stream(chunked(data), output, interval_seconds=2.0)

        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(frame.stat().st_size > 0 for frame in frames))

    def test_undecodable_stream_reports_that_a_file_is_needed(self) -> None:
        output = self.root / "frames"
        output.mkdir()
        with self.assertRaises(StreamingUnsupportedError):
            extract_frames_from_stream(chunked(b"not a container"), output, interval_seconds=2.0)

    def test_a_failed_stream_leaves_no_ffmpeg_process_behind(self) -> None:
        output = self.root / "frames"
        output.mkdir()
        with self.assertRaises(StreamingUnsupportedError):
            extract_frames_from_stream(chunked(b"still not a container"), output, interval_seconds=2.0)
        # A leaked child would keep the temporary directory busy on cleanup.
        self.assertEqual(list(output.glob("frame-*.jpg")), [])


class IndexStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.database = Database(self.root / "stream.sqlite3")
        self.database.upsert_source(SOURCE)

    def test_media_and_frames_are_written_from_a_stream(self) -> None:
        data = make_video(self.root / "clip.mp4", seconds=6)

        result = index_stream(
            self.database,
            chunks=chunked(data),
            source_id=SOURCE["id"],
            source_url="https://video.example.com/watch/1",
            title="Streamed Film",
            category="movie-tv",
            adult=False,
            episode=None,
            interval_seconds=2.0,
            duration_ms=6000,
        )

        self.assertGreaterEqual(result["frames"], 3)
        self.assertEqual(result["duration_ms"], 6000)
        with self.database.connect() as connection:
            frames = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE media_id=?", (result["media_id"],)
            ).fetchone()[0]
            duration = connection.execute(
                "SELECT duration_ms FROM media WHERE id=?", (result["media_id"],)
            ).fetchone()[0]
        self.assertEqual(frames, result["frames"])
        self.assertEqual(duration, 6000)

    def test_no_media_row_is_created_when_the_stream_cannot_be_decoded(self) -> None:
        with self.assertRaises(StreamingUnsupportedError):
            index_stream(
                self.database,
                chunks=chunked(b"garbage"),
                source_id=SOURCE["id"],
                source_url="https://video.example.com/watch/2",
                title="Broken",
                category="movie-tv",
                adult=False,
                episode=None,
                interval_seconds=2.0,
            )
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        self.assertEqual(count, 0)


class WorkerFallbackTests(unittest.TestCase):
    """The worker must still index containers that a pipe cannot decode."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.database = Database(self.root / "fallback.sqlite3")
        self.database.upsert_source(SOURCE)

    def test_streaming_failure_falls_back_to_downloading_the_file(self) -> None:
        from sahne_avcisi.adapters import AdapterResult
        from sahne_avcisi.worker import IndexWorker

        video = make_video(self.root / "clip.mp4", seconds=6)

        class Client:
            def __init__(self) -> None:
                self.downloaded = False

            @contextmanager
            def stream_video(self, url: str, *, max_bytes: int):
                yield iter([b"undecodable"])

            def download_video(self, url: str, destination: Path, *, max_bytes: int) -> str:
                self.downloaded = True
                destination.write_bytes(video)
                return url

        class Registry:
            def __init__(self, client: Client) -> None:
                self.client = client

            def resolve(self, source: dict, page_url: str) -> AdapterResult:
                return AdapterResult(
                    adapter="direct-video",
                    page_url=page_url,
                    title="Fallback Film",
                    media_url="https://video.example.com/clip.mp4",
                    player_type="direct",
                    indexable=True,
                )

        client = Client()
        self.database.enqueue_index_job(
            source_id=SOURCE["id"], page_url="https://video.example.com/watch/1"
        )
        result = IndexWorker(self.database, registry=Registry(client)).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertTrue(client.downloaded)
        self.assertGreaterEqual(result["frames"], 3)


if __name__ == "__main__":
    unittest.main()
