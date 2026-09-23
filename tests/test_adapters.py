from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sahne_avcisi.adapters import (
    AdapterError,
    AdapterRegistry,
    AdapterResult,
    PublicHttpClient,
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


def profiled_source(kind: str, base_url: str) -> dict:
    return {
        "id": kind,
        "name": kind,
        "base_url": base_url,
        "kind": kind,
        "category": "adult",
        "adult": True,
        "status": "active",
    }


class FakePageClient:
    def __init__(self, html: str):
        self.html = html

    def fetch_text(self, url: str):
        return self.html, url


class MappingPageClient:
    def __init__(self, pages: dict[str, str], json_documents: dict[str, str] | None = None):
        self.pages = pages
        self.json_documents = json_documents or {}

    def fetch_text(self, url: str):
        return self.pages[url], url

    def fetch_json(self, url: str, *, headers=None):
        del headers
        return self.json_documents[url], url


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


class HeaderCaptureClient:
    def __init__(self) -> None:
        self.headers = None

    @contextmanager
    def stream_video(self, url: str, *, max_bytes: int, headers=None):
        del url, max_bytes
        self.headers = headers
        yield iter([b"fake-video"])


class AdapterTests(unittest.TestCase):
    def test_private_and_non_https_urls_are_rejected(self) -> None:
        for url in ("https://127.0.0.1/video.mp4", "https://10.1.2.3/video.mp4", "http://example.com/a"):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_public_https_url(url)

    def test_media_headers_are_strictly_allowlisted(self) -> None:
        self.assertEqual(
            PublicHttpClient._safe_request_headers({"Referer": "https://video.example.com/watch/1"}),
            {"Referer": "https://video.example.com/watch/1"},
        )
        with self.assertRaises(AdapterError):
            PublicHttpClient._safe_request_headers({"Cookie": "session=secret"})
        with self.assertRaises(AdapterError):
            PublicHttpClient._safe_request_headers({"Referer": "https://example.com/\r\nX-Test: 1"})

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

    def test_json_ld_content_url_is_resolved(self) -> None:
        html = """
        <script type="application/ld+json">
          {"@type":"VideoObject","name":"Örnek","contentUrl":"https://cdn.example.com/film-720p.mp4"}
        </script>
        """
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(SOURCE, "https://video.example.com/watch/42")
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://cdn.example.com/film-720p.mp4")

    def test_public_iframe_chain_is_followed_with_a_depth_limit(self) -> None:
        root = "https://video.example.com/watch/42"
        embed = "https://player.example.net/embed/42"
        pages = {
            root: f'<title>Ana başlık</title><iframe src="{embed}"></iframe>',
            embed: '<script>const file="https:\\/\\/cdn.example.net\\/film-1080p.mp4";</script>',
        }
        registry = AdapterRegistry(client=MappingPageClient(pages))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(SOURCE, root)
        self.assertTrue(result.indexable)
        self.assertEqual(result.adapter, "iframe-public-media")
        self.assertEqual(result.media_url, "https://cdn.example.net/film-1080p.mp4")
        self.assertEqual(result.embed_url, embed)
        self.assertEqual(result.title, "Ana başlık")

    def test_access_control_blocks_even_when_a_media_literal_exists(self) -> None:
        html = """
        <div class="g-recaptcha"></div>
        <script>const file="https://cdn.example.com/film.mp4";</script>
        """
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(SOURCE, "https://video.example.com/watch/42")
        self.assertFalse(result.indexable)
        self.assertIn("CAPTCHA", result.reason or "")

    def test_adult_source_title_with_uncertain_age_is_blocked(self) -> None:
        html = """
        <meta property="og:title" content="Teen example">
        <video src="https://cdn.example.com/film.mp4"></video>
        """
        source = {**SOURCE, "adult": True, "category": "adult"}
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, "https://video.example.com/watch/42")
        self.assertFalse(result.indexable)
        self.assertIn("yaş güvenliği", result.reason or "")

    def test_adult_iframe_title_with_uncertain_age_is_blocked(self) -> None:
        root = "https://video.example.com/watch/42"
        embed = "https://player.example.net/embed/42"
        pages = {
            root: f'<title>Ana başlık</title><iframe src="{embed}"></iframe>',
            embed: '<title>Schoolgirl example</title><video src="https://cdn.example.net/film.mp4"></video>',
        }
        source = {**SOURCE, "adult": True, "category": "adult"}
        registry = AdapterRegistry(client=MappingPageClient(pages))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, root)
        self.assertFalse(result.indexable)
        self.assertIn("yaş güvenliği", result.reason or "")

    def test_xvideos_profile_prefers_public_high_quality_url(self) -> None:
        html = """
        <script>
          html5player.setVideoTitle('Örnek Klip');
          html5player.setVideoUrlLow('https://cdn.example.com/clip-240p.mp4');
          html5player.setVideoHLS('https://cdn.example.com/master.m3u8');
          html5player.setVideoUrlHigh('https://cdn.example.com/clip-720p.mp4');
        </script>
        """
        source = profiled_source("xvideos", "https://www.xvideos.com/")
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, "https://www.xvideos.com/videoabc123/example")
        self.assertTrue(result.indexable)
        self.assertEqual(result.adapter, "xvideos-public-page")
        self.assertEqual(result.media_url, "https://cdn.example.com/clip-720p.mp4")
        self.assertEqual(result.title, "Örnek Klip")
        self.assertEqual(dict(result.request_headers)["Referer"], result.page_url)

    def test_pornhub_profile_reads_public_media_definitions(self) -> None:
        html = """
        <script>var flashvars_42 = {
          "video_title":"Örnek Klip",
          "mediaDefinitions":[
            {"quality":"480","videoUrl":"https://cdn.example.com/clip-480p.mp4"},
            {"quality":"1080","videoUrl":"https://cdn.example.com/clip-1080p.mp4"}
          ]
        };</script>
        """
        source = profiled_source("pornhub", "https://www.pornhub.com/")
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, "https://www.pornhub.com/view_video.php?viewkey=abc123")
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://cdn.example.com/clip-1080p.mp4")
        self.assertEqual(result.title, "Örnek Klip")
        self.assertEqual(dict(result.request_headers)["Origin"], "https://www.pornhub.com")

    def test_pornhub_profile_resolves_public_json_media_endpoint(self) -> None:
        page = "https://www.pornhub.com/view_video.php?viewkey=abc123"
        endpoint = "https://www.pornhub.com/video/get_media?id=42"
        html = f'<script>var flashvars_42 = {{"mediaDefinitions":[{{"videoUrl":"{endpoint}"}}]}};</script>'
        documents = {endpoint: '[{"videoUrl":"https://cdn.example.com/clip-720p.mp4"}]'}
        source = profiled_source("pornhub", "https://www.pornhub.com/")
        registry = AdapterRegistry(client=MappingPageClient({page: html}, documents))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, page)
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://cdn.example.com/clip-720p.mp4")

    def test_xhamster_profile_reads_public_initial_state(self) -> None:
        html = """
        <script>window.initials = {
          "videoModel":{"title":"Örnek Klip","sources":{"mp4":{"720p":"https://cdn.example.com/clip-720p.mp4"}}},
          "xplayerSettings":{"sources":{"hls":{"url":"https://cdn.example.com/master.m3u8"}}}
        };</script>
        """
        source = profiled_source("xhamster", "https://xhamster.com/")
        registry = AdapterRegistry(client=FakePageClient(html))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            result = registry.resolve(source, "https://xhamster.com/videos/example-abc123")
        self.assertTrue(result.indexable)
        self.assertEqual(result.media_url, "https://cdn.example.com/clip-720p.mp4")
        self.assertEqual(result.title, "Örnek Klip")

    def test_profiled_source_rejects_non_video_page(self) -> None:
        source = profiled_source("xvideos", "https://www.xvideos.com/")
        registry = AdapterRegistry(client=FakePageClient("<html></html>"))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            with self.assertRaises(UnsupportedMediaError):
                registry.resolve(source, "https://www.xvideos.com/profiles/example")

    def test_pornhub_profile_requires_a_video_key(self) -> None:
        source = profiled_source("pornhub", "https://www.pornhub.com/")
        registry = AdapterRegistry(client=FakePageClient("<html></html>"))
        with patch("sahne_avcisi.adapters.validate_public_https_url", side_effect=lambda url: url):
            with self.assertRaises(UnsupportedMediaError):
                registry.resolve(source, "https://www.pornhub.com/view_video.php")

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

    def test_worker_forwards_only_adapter_media_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.sqlite3")
            database.upsert_source(SOURCE)
            job = database.enqueue_index_job(
                source_id=SOURCE["id"],
                page_url="https://video.example.com/watch/42",
            )
            resolved = AdapterResult(
                adapter="profiled-public-page",
                page_url=job["page_url"],
                title="Başlık",
                media_url="https://cdn.example.com/film.mp4",
                player_type="direct",
                indexable=True,
                request_headers=(("Referer", job["page_url"]),),
            )
            client = HeaderCaptureClient()
            registry = FakeRegistry(resolved)
            registry.client = client

            def fake_index(db: Database, **kwargs):
                media_id = db.create_media(
                    source_id=kwargs["source_id"],
                    title=kwargs["title"],
                    source_url=kwargs["source_url"],
                    category=kwargs["category"],
                    adult=kwargs["adult"],
                )
                return {"media_id": media_id, "frames": 3, "duration_ms": 1000}

            with patch(
                "sahne_avcisi.worker.index_stream",
                side_effect=fake_index,
            ):
                result = IndexWorker(database, registry=registry).run_once()

            self.assertEqual(result["status"], "completed")
            self.assertEqual(client.headers, {"Referer": job["page_url"]})


if __name__ == "__main__":
    unittest.main()
