from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from sahne_avcisi.database import Database
from sahne_avcisi.indexer import extract_frames, index_local_video, probe_duration_ms


SOURCE = {
    "id": "known-source",
    "name": "Known Source",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


def make_frame_file(directory: Path, name: str, fill: int) -> Path:
    image = Image.new("RGB", (16, 16), (fill, fill, fill))
    path = directory / name
    image.save(path, format="JPEG")
    return path


class IndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.media_file = Path(self.temp_dir.name) / "video.mp4"
        self.media_file.write_bytes(b"fake-video-bytes")

    def test_index_local_video_rejects_unknown_source_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "Kaynak bulunamadı"):
            index_local_video(
                self.database,
                media_file=self.media_file,
                source_id="does-not-exist",
                source_url="https://video.example.com/watch/1",
                title="Film",
                category="movie-tv",
                adult=False,
                episode=None,
                interval_seconds=2.0,
            )

    def test_probe_duration_ms_raises_filenotfounderror_when_ffprobe_missing(self) -> None:
        with patch("sahne_avcisi.indexer.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                probe_duration_ms(self.media_file)

    def test_extract_frames_raises_filenotfounderror_when_ffmpeg_missing(self) -> None:
        with patch("sahne_avcisi.indexer.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                extract_frames(self.media_file, Path(self.temp_dir.name), 2.0)

    def test_index_local_video_replaces_frames_on_reindex(self) -> None:
        self.database.upsert_source(SOURCE)
        frame_dir_1 = Path(tempfile.mkdtemp(dir=self.temp_dir.name))
        frames_1 = [make_frame_file(frame_dir_1, f"frame-{i}.jpg", i * 10) for i in range(3)]
        frame_dir_2 = Path(tempfile.mkdtemp(dir=self.temp_dir.name))
        frames_2 = [make_frame_file(frame_dir_2, f"frame-{i}.jpg", i * 20) for i in range(1)]

        with patch("sahne_avcisi.indexer.probe_duration_ms", return_value=6000), patch(
            "sahne_avcisi.indexer.extract_frames", return_value=frames_1
        ):
            first = index_local_video(
                self.database,
                media_file=self.media_file,
                source_id=SOURCE["id"],
                source_url="https://video.example.com/watch/1",
                title="Film",
                category="movie-tv",
                adult=False,
                episode=None,
                interval_seconds=2.0,
            )
        self.assertEqual(first["frames"], 3)
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE media_id=?", (first["media_id"],)
            ).fetchone()[0]
        self.assertEqual(count, 3)

        with patch("sahne_avcisi.indexer.probe_duration_ms", return_value=6000), patch(
            "sahne_avcisi.indexer.extract_frames", return_value=frames_2
        ):
            second = index_local_video(
                self.database,
                media_file=self.media_file,
                source_id=SOURCE["id"],
                source_url="https://video.example.com/watch/1",
                title="Film",
                category="movie-tv",
                adult=False,
                episode=None,
                interval_seconds=2.0,
            )
        self.assertEqual(second["media_id"], first["media_id"])
        self.assertEqual(second["frames"], 1)
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE media_id=?", (second["media_id"],)
            ).fetchone()[0]
        self.assertEqual(count, 1, "stale frames from the first index must be removed on reindex")


if __name__ == "__main__":
    unittest.main()
