from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sahne_avcisi.adapters import (
    AdapterRegistry,
    AdapterResult,
    UnsupportedMediaError,
    UnsafeUrlError,
    validate_public_https_url,
)
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

RULE34VIDEO_SOURCE = {
    "id": "rule34video",
    "name": "Rule34Video",
    "base_url": "https://rule34video.com/",
    "kind": "rule34video",
    "category": "adult-animation",
    "adult": True,
    "status": "active",
}


class FakePageClient:
    def __init__(self, html: str):
        self.html = html

    def fetch_text(self, url: str):
        return self.html, url


class FakeDownloadClient:
    def download_video(self, url: str, destination: Path, *, max_bytes: int):
        destination.write_bytes(b"fake-video")
        return url

    @contextmanager
    def stream_video(self, url: str, *, max_bytes: int):
        # The bytes are not a real container, so FFmpeg produces no frames and
        # the worker falls back to the download path — which is the behaviour
        # these tests cover.
        yield iter([b"fake-video"])


class FakeRegistry:
    def __init__(self, result: AdapterResult):
        self.result = result
        self.client = FakeDownloadClient()

    def resolve(self, source: dict, page_url: str) -> AdapterResult:
        return self.result


class AdapterTests(unittest.TestCase):
    def test_private_and_non_https_urls_are_rejected(self) -> None:
        for url in ("https://127.0.0.1/video.mp4", "https://10.1.2.3/video.mp4", "http://example.com/a"):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_public_https_url(url)

    def test_html5_video_and_title_are_resolved(self) -> None:
        html = """
        <html><head><meta property="og:title" content="Örnek Film"></head>
        <body><video class="videojs"><source src="/media/film.mp4" type="video/mp4"></video></body></html>
        """
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(SOURCE, "https://video.example.com/watch/42")
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://video.example.com/media/film.mp4")
        self.assertEqual(result.title, "Örnek Film")
        self.assertEqual(result.player_type, "videojs")

    def test_hls_is_detected_and_marked_indexable(self) -> None:
        registry = AdapterRegistry(client=FakePageClient('<video src="https://cdn.example.com/a.m3u8"></video>'))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(SOURCE, "https://video.example.com/watch/42")
        self.assertTrue(result.indexable)
        self.assertEqual(result.player_type, "hls")
        self.assertEqual(result.media_url, "https://cdn.example.com/a.m3u8")

    def test_rule34video_uses_highest_quality_public_download_link(self) -> None:
        html = """
        <html><head><meta property="og:title" content="Örnek Animasyon"></head><body>
          <a href="/get/42-480.mp4?download=true">MP4 480p</a>
          <a href="https://cdn.example.com/get/42-1080.mp4?token=x&amp;download=true">
            MP4 <strong>1080p</strong>
          </a>
        </body></html>
        """
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(RULE34VIDEO_SOURCE, "https://rule34video.com/video/42/example")
        self.assertTrue(result.indexable)
        self.assertEqual(result.adapter, "rule34video-public-download")
        self.assertEqual(
            result.media_url,
            "https://cdn.example.com/get/42-1080.mp4?token=x&download=true",
        )
        self.assertEqual(result.title, "Örnek Animasyon")
        self.assertEqual(result.player_type, "direct")

    def test_rule34video_accepts_plural_video_path(self) -> None:
        html = '<a href="/media/42.mp4?download=true">Download</a>'
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(RULE34VIDEO_SOURCE, "https://rule34video.com/videos/42/example")
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://rule34video.com/media/42.mp4?download=true")

    def test_rule34video_blocks_pages_without_public_download_link(self) -> None:
        html = "<html><title>Kontrollü oynatıcı</title><body>CAPTCHA</body></html>"
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(RULE34VIDEO_SOURCE, "https://rule34video.com/video/42/example")
        self.assertFalse(result.indexable)
        self.assertIsNone(result.media_url)
        self.assertIn("CAPTCHA", result.reason or "")

    def test_rule34video_rejects_non_video_page(self) -> None:
        registry = AdapterRegistry(client=FakePageClient("<html></html>"))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            with self.assertRaises(UnsupportedMediaError):
                registry.resolve(RULE34VIDEO_SOURCE, "https://rule34video.com/latest-updates/")

    def test_worker_completes_a_direct_video_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.sqlite3")
            database.upsert_source(SOURCE)
            job = database.enqueue_index_job(
                source_id=SOURCE["id"],
                page_url="https://video.example.com/watch/42",
                title="Başlık",
            )
            resolved = AdapterResult(
                adapter="html5-media",
                page_url=job["page_url"],
                title="Sayfa başlığı",
                media_url="https://cdn.example.com/film.mp4?token=secret",
                player_type="html5",
                indexable=True,
            )
            registry = FakeRegistry(resolved)

            def fake_index(db: Database, **kwargs):
                media_id = db.create_media(
                    source_id=kwargs["source_id"],
                    title=kwargs["title"],
                    source_url=kwargs["source_url"],
                    category=kwargs["category"],
                    adult=kwargs["adult"],
                )
                return {"media_id": media_id, "frames": 12, "duration_ms": 24000}

            with patch("sahne_avcisi.worker.index_local_video", side_effect=fake_index):
                result = IndexWorker(database, registry=registry).run_once()

            self.assertEqual(result["status"], "completed")
            stored = database.list_index_jobs()[0]
            self.assertEqual(stored["status"], "completed")
            self.assertEqual(stored["frame_count"], 12)
            self.assertEqual(stored["media_url"], "https://cdn.example.com/film.mp4")


if __name__ == "__main__":
    unittest.main()
