from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sahne_avcisi.adapters import AdapterError, AdapterResult, UnsafeUrlError
from sahne_avcisi.database import Database
from sahne_avcisi.hls import HlsMirror
from sahne_avcisi.worker import IndexWorker


SOURCE = {
    "id": "hls-example",
    "name": "HLS Example",
    "base_url": "https://video.example.com/",
    "kind": "video-site",
    "category": "movie-tv",
    "adult": False,
    "status": "active",
}


class FakeHlsClient:
    def __init__(self, playlists: dict[str, str], resources: dict[str, bytes]):
        self.playlists = playlists
        self.resources = resources
        self.downloads: list[str] = []
        self.request_headers: list[dict | None] = []

    def fetch_playlist(self, url: str, *, headers=None):
        self.request_headers.append(headers)
        return self.playlists[url], url

    def download_resource(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        allowed_content_prefixes: tuple[str, ...],
        allowed_content_types: set[str],
        headers=None,
    ):
        del allowed_content_prefixes, allowed_content_types
        self.request_headers.append(headers)
        payload = self.resources[url]
        if len(payload) > max_bytes:
            raise AdapterError("Kaynak izin verilen boyut sınırını aşıyor.")
        destination.write_bytes(payload)
        self.downloads.append(url)
        return url, len(payload)


class FakeRegistry:
    def __init__(self, client: FakeHlsClient, result: AdapterResult):
        self.client = client
        self.result = result

    def resolve(self, source: dict, page_url: str) -> AdapterResult:
        del source, page_url
        return self.result


class HlsMirrorTests(unittest.TestCase):
    def test_selects_target_variant_and_rewrites_every_resource_locally(self) -> None:
        master_url = "https://video.example.com/master.m3u8"
        low_url = "https://video.example.com/360/index.m3u8"
        target_url = "https://video.example.com/480/index.m3u8"
        client = FakeHlsClient(
            {
                master_url: """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480
480/index.m3u8
""",
                low_url: "#EXTM3U\n#EXT-X-ENDLIST\n",
                target_url: """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.0,
part-1.m4s
#EXTINF:2.5,
part-2.m4s
#EXT-X-ENDLIST
""",
            },
            {
                "https://video.example.com/480/init.mp4": b"init",
                "https://video.example.com/480/part-1.m4s": b"one",
                "https://video.example.com/480/part-2.m4s": b"two",
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "sahne_avcisi.hls.validate_public_https_url", side_effect=lambda url: url
        ):
            result = HlsMirror(client).mirror(master_url, Path(temp_dir), max_bytes=100)
            manifest = result.manifest_path.read_text(encoding="utf-8")

        self.assertEqual(result.manifest_url, target_url)
        self.assertEqual(result.segment_count, 3)
        self.assertEqual(result.total_bytes, 10)
        self.assertEqual(result.duration_seconds, 6.5)
        self.assertNotIn("https://", manifest)
        self.assertIn('URI="asset-000001.mp4"', manifest)
        self.assertIn("asset-000002.m4s", manifest)
        self.assertNotIn(low_url, client.downloads)

    def test_rejects_live_encrypted_and_cross_host_playlists(self) -> None:
        cases = {
            "live": (
                "#EXTM3U\n#EXTINF:4,\npart.ts\n",
                AdapterError,
            ),
            "encrypted": (
                '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n',
                AdapterError,
            ),
            "cross-host": (
                "#EXTM3U\n#EXTINF:4,\nhttps://other.example.net/part.ts\n#EXT-X-ENDLIST\n",
                UnsafeUrlError,
            ),
        }
        for name, (playlist, error_type) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir, patch(
                "sahne_avcisi.hls.validate_public_https_url", side_effect=lambda url: url
            ):
                url = "https://video.example.com/index.m3u8"
                client = FakeHlsClient({url: playlist}, {})
                with self.assertRaises(error_type):
                    HlsMirror(client).mirror(url, Path(temp_dir), max_bytes=100)

    def test_media_headers_are_forwarded_to_manifest_and_segments(self) -> None:
        playlist_url = "https://video.example.com/vod/index.m3u8"
        segment_url = "https://video.example.com/vod/part.ts"
        client = FakeHlsClient(
            {playlist_url: "#EXTM3U\n#EXTINF:2,\npart.ts\n#EXT-X-ENDLIST\n"},
            {segment_url: b"segment"},
        )
        headers = {"Referer": "https://video.example.com/watch/1"}
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "sahne_avcisi.hls.validate_public_https_url", side_effect=lambda url: url
        ):
            HlsMirror(client).mirror(
                playlist_url,
                Path(temp_dir),
                max_bytes=100,
                headers=headers,
            )
        self.assertEqual(client.request_headers, [headers, headers])

    def test_worker_completes_an_hls_job_from_local_mirror(self) -> None:
        playlist_url = "https://video.example.com/vod/index.m3u8"
        segment_url = "https://video.example.com/vod/part.ts"
        client = FakeHlsClient(
            {playlist_url: "#EXTM3U\n#EXTINF:2,\npart.ts\n#EXT-X-ENDLIST\n"},
            {segment_url: b"segment"},
        )
        resolved = AdapterResult(
            adapter="direct-hls",
            page_url=playlist_url,
            title="HLS video",
            media_url=playlist_url,
            player_type="hls",
            indexable=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.sqlite3")
            database.upsert_source(SOURCE)
            database.enqueue_index_job(source_id=SOURCE["id"], page_url=playlist_url)

            def fake_index(db: Database, **kwargs):
                manifest = kwargs["media_file"]
                self.assertTrue(manifest.is_file())
                self.assertNotIn("https://", manifest.read_text(encoding="utf-8"))
                media_id = db.create_media(
                    source_id=kwargs["source_id"],
                    title=kwargs["title"],
                    source_url=kwargs["source_url"],
                    category=kwargs["category"],
                    adult=kwargs["adult"],
                )
                return {"media_id": media_id, "frames": 3, "duration_ms": 2000}

            with patch("sahne_avcisi.hls.validate_public_https_url", side_effect=lambda url: url), patch(
                "sahne_avcisi.worker.index_local_video", side_effect=fake_index
            ):
                result = IndexWorker(database, registry=FakeRegistry(client, resolved)).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["frames"], 3)


if __name__ == "__main__":
    unittest.main()
