import tempfile
import unittest
from pathlib import Path

from sahne_avcisi.database import Database
from sahne_avcisi.fmhy import discover_links


class FmhyDiscoveryTests(unittest.TestCase):
    def test_external_links_are_discovered_and_deduplicated(self) -> None:
        html = """
        <a href="https://example.org/watch">Example One</a>
        <a href="https://example.org/other">Example Duplicate</a>
        <a href="/internal">Internal</a>
        """
        sources = discover_links(html, "https://fmhy.net/videopiracyguide")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["base_url"], "https://example.org/")
        self.assertEqual(sources[0]["status"], "review-required")

    def test_database_hides_adult_sources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_source(
                {
                    "id": "adult",
                    "name": "Adult",
                    "base_url": "https://example.com/",
                    "kind": "video-site",
                    "category": "adult",
                    "adult": True,
                    "status": "review-required",
                    "priority": 1,
                }
            )
            self.assertEqual(database.list_sources(), [])
            self.assertEqual(len(database.list_sources(include_adult=True)), 1)

    def test_sections_are_classified_and_auxiliary_links_are_ignored(self) -> None:
        html = """
        <h2>Streaming Sites</h2>
        <h3>Stream Aggregators</h3>
        <ul>
          <li>🌟 <a href="https://cinema.example/watch">Cinema Test</a>
              - Movies / TV / Anime / Auto-Next / 4K /
              <a href="https://rentry.co/status">Status</a></li>
        </ul>
        <h2>Download Sites</h2>
        <h3>Movies</h3>
        <ul><li><a href="https://downloads.example/">Skip Me</a> - Movies</li></ul>
        """
        sources = discover_links(html, "https://fmhy.net/video")
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source["kind"], "stream-aggregator")
        self.assertEqual(source["category"], "mixed")
        self.assertEqual(source["priority"], 80)
        self.assertIn("fmhy-starred", source["tags"])
        self.assertIn("auto-next", source["tags"])
        self.assertIn("4k", source["tags"])

    def test_catalog_snapshot_tracks_created_missing_and_restored_sources(self) -> None:
        source = {
            "id": "fmhy-site-example",
            "name": "Example",
            "base_url": "https://example.com/",
            "kind": "stream-aggregator",
            "category": "movie-tv",
            "adult": False,
            "status": "review-required",
            "priority": 60,
            "section": "Streaming Sites",
            "tags": [],
            "discovered_from": "https://fmhy.net/video",
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "tracker.sqlite3")

            first_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            first = database.apply_catalog_snapshot(first_run, "fmhy-video", [source])
            database.finish_catalog_sync(first_run, status="completed", counts=first)
            self.assertEqual(first["created"], 1)

            second_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            second = database.apply_catalog_snapshot(second_run, "fmhy-video", [source])
            database.finish_catalog_sync(second_run, status="completed", counts=second)
            self.assertEqual(second["updated"], 0)

            missing_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            missing = database.apply_catalog_snapshot(missing_run, "fmhy-video", [])
            database.finish_catalog_sync(missing_run, status="completed", counts=missing)
            self.assertEqual(missing["missing"], 1)

            restored_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            restored = database.apply_catalog_snapshot(restored_run, "fmhy-video", [source])
            database.finish_catalog_sync(restored_run, status="completed", counts=restored)
            self.assertEqual(restored["restored"], 1)

            event_types = [event["event_type"] for event in database.list_source_events()]
            self.assertEqual(event_types, ["restored", "missing", "discovered"])


if __name__ == "__main__":
    unittest.main()
