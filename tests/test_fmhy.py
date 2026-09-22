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

    def test_starred_entries_are_detected_from_the_rendered_list_item_class(self) -> None:
        # FMHY renders its star as a CSS class, not a literal emoji.
        html = """
        <h2>Streaming Sites</h2>
        <h3>Multi-Server</h3>
        <ul>
          <li class="starred"><span class="i-twemoji-glowing-star"></span>
              <strong><a href="https://starred.example/">Starred Site</a></strong> - Movies / TV</li>
          <li><a href="https://plain.example/">Plain Site</a> - Movies / TV</li>
        </ul>
        """
        sources = {source["name"]: source for source in discover_links(html, "https://fmhy.net/video")}
        self.assertIn("fmhy-starred", sources["Starred Site"]["tags"])
        self.assertNotIn("fmhy-starred", sources["Plain Site"]["tags"])
        self.assertGreater(sources["Starred Site"]["priority"], sources["Plain Site"]["priority"])

    def test_reference_databases_are_kept_as_metadata_not_video_sources(self) -> None:
        html = """
        <h2>Tracking / Databases</h2>
        <ul><li><a href="https://tracker.example/">Tracker</a> - Movies / TV</li></ul>
        """
        sources = discover_links(html, "https://fmhy.net/video")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["kind"], "metadata")

    def test_non_video_sections_are_skipped(self) -> None:
        html = """
        <h2>Smart TV</h2>
        <ul><li><a href="https://tvapp.example/">TV App</a></li></ul>
        <h2>Streaming Apps</h2>
        <ul><li><a href="https://app.example/">An App</a></li></ul>
        <h2>Helpful Sites / Tools</h2>
        <ul><li><a href="https://tool.example/">A Tool</a></li></ul>
        <h2>Base64 Encoded Link</h2>
        <ul><li><a href="https://b64.example/">Encoded</a></li></ul>
        """
        self.assertEqual(discover_links(html, "https://fmhy.net/video"), [])

    def test_sync_does_not_reset_an_operator_activated_source(self) -> None:
        source = {
            "id": "fmhy-site-example",
            "name": "Example",
            "base_url": "https://example.com/",
            "kind": "stream-aggregator",
            "category": "movie-tv",
            "adult": False,
            "status": "review-required",
            "priority": 62,
            "section": "Streaming Sites",
            "tags": [],
            "discovered_from": "https://fmhy.net/video",
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "curation.sqlite3")

            first_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            database.apply_catalog_snapshot(first_run, "fmhy-video", [source])

            database.upsert_source({**source, "status": "active"})
            self.assertEqual(len(database.list_sources()), 1)

            second_run = database.start_catalog_sync("fmhy-video", "https://fmhy.net/video")
            counts = database.apply_catalog_snapshot(second_run, "fmhy-video", [source])

            self.assertEqual(counts["updated"], 0)
            self.assertEqual(database.list_sources()[0]["status"], "active")

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
