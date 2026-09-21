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


if __name__ == "__main__":
    unittest.main()

