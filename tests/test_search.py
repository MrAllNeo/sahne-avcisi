import sqlite3
import tempfile
import unittest
from pathlib import Path

from sahne_avcisi.database import Database
from sahne_avcisi.fingerprint import Fingerprint, to_signed64

BASE = 0x0F0F0F0F0F0F0F0F


def flip(value: int, bits: int) -> int:
    """Return value with `bits` low-order bits inverted."""
    return value ^ ((1 << bits) - 1)


class SearchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.database = Database(Path(self._directory.name) / "search.sqlite3")
        self._frames: dict[int, list] = {}

    def add_source(self, source_id: str, *, adult: bool = False, category: str = "movie-tv") -> None:
        self.database.upsert_source(
            {
                "id": source_id,
                "name": f"Source {source_id}",
                "base_url": f"https://{source_id}.example/",
                "kind": "video-site",
                "category": category,
                "adult": adult,
                "status": "active",
                "priority": 1,
            }
        )

    def add_media(self, source_id: str, title: str, *, adult: bool = False, category: str = "movie-tv") -> int:
        return self.database.create_media(
            source_id=source_id,
            title=title,
            source_url=f"https://{source_id}.example/{title}",
            category=category,
            adult=adult,
            episode=None,
            duration_ms=60_000,
        )

    def add_frame(self, media_id: int, timestamp_ms: int, dhash: int, ahash: int) -> None:
        # replace_frames rewrites a whole media, so keep the running set per media.
        frames = self._frames.setdefault(media_id, [])
        frames.append((timestamp_ms, Fingerprint(dhash=dhash, ahash=ahash, width=480, height=270)))
        self.database.replace_frames(media_id, frames)

    def query(self, dhash: int, ahash: int, **kwargs):
        kwargs.setdefault("allow_adult", False)
        return self.database.search(Fingerprint(dhash=dhash, ahash=ahash, width=480, height=270), **kwargs)


class RankingTests(SearchTestCase):
    def test_empty_index_returns_no_results(self) -> None:
        self.assertEqual(self.query(BASE, BASE), [])

    def test_closest_frame_ranks_first_and_scores_100(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        other = self.add_media("s", "Other")
        self.add_frame(media, 1000, BASE, BASE)
        self.add_frame(other, 2000, flip(BASE, 8), flip(BASE, 8))

        results = self.query(BASE, BASE)
        self.assertEqual(results[0]["timestamp_ms"], 1000)
        self.assertEqual(results[0]["similarity"], 100.0)
        self.assertEqual(results[0]["title"], "Film")
        self.assertGreater(results[0]["similarity"], results[1]["similarity"])

    def test_only_the_strongest_frame_of_a_video_is_returned(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        self.add_frame(media, 1000, flip(BASE, 8), BASE)
        self.add_frame(media, 2000, BASE, BASE)
        self.add_frame(media, 3000, flip(BASE, 4), BASE)

        results = self.query(BASE, BASE)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["timestamp_ms"], 2000)

    def test_results_are_ordered_by_similarity(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        for index, bits in enumerate((12, 4, 8)):
            self.add_frame(media, (index + 1) * 1000, flip(BASE, bits), BASE)

        scores = [item["similarity"] for item in self.query(BASE, BASE)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_limit_caps_the_result_count(self) -> None:
        self.add_source("s")
        for index in range(10):
            media = self.add_media("s", f"Film {index}")
            self.add_frame(media, 1000, BASE, BASE)
        self.assertEqual(len(self.query(BASE, BASE, limit=3)), 3)

    def test_frames_below_the_threshold_are_dropped(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        self.add_frame(media, 1000, flip(BASE, 64), flip(BASE, 64))
        self.assertEqual(self.query(BASE, BASE, minimum_similarity=0.9), [])


class FilterTests(SearchTestCase):
    def test_adult_media_is_hidden_unless_explicitly_allowed(self) -> None:
        self.add_source("s", adult=True, category="adult")
        media = self.add_media("s", "Adult", adult=True, category="adult")
        self.add_frame(media, 1000, BASE, BASE)

        self.assertEqual(self.query(BASE, BASE), [])
        self.assertEqual(len(self.query(BASE, BASE, allow_adult=True)), 1)

    def test_category_filter_excludes_other_categories(self) -> None:
        self.add_source("s")
        movie = self.add_media("s", "Movie", category="movie-tv")
        anime = self.add_media("s", "Anime", category="anime")
        self.add_frame(movie, 1000, BASE, BASE)
        self.add_frame(anime, 1000, BASE, BASE)

        titles = [item["title"] for item in self.query(BASE, BASE, category="anime")]
        self.assertEqual(titles, ["Anime"])
        self.assertEqual(len(self.query(BASE, BASE, category="all")), 2)

    def test_filtering_everything_out_returns_no_results(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Movie", category="movie-tv")
        self.add_frame(media, 1000, BASE, BASE)
        self.assertEqual(self.query(BASE, BASE, category="anime"), [])


class IndexFreshnessTests(SearchTestCase):
    def test_frames_added_after_a_search_are_still_found(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        self.add_frame(media, 1000, flip(BASE, 10), BASE)
        self.assertEqual(len(self.query(BASE, BASE)), 1)

        later = self.add_media("s", "Later Film")
        self.add_frame(later, 2000, BASE, BASE)
        results = self.query(BASE, BASE)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Later Film")


class LegacySchemaMigrationTests(SearchTestCase):
    def _rewrite_hashes_as_text(self, path: Path) -> None:
        """Recreate the pre-0.7 frames table, which stored hex strings."""
        connection = sqlite3.connect(path)
        rows = connection.execute(
            "SELECT media_id, timestamp_ms, dhash, ahash, width, height FROM frames"
        ).fetchall()
        connection.executescript(
            """
            DROP TABLE frames;
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                timestamp_ms INTEGER NOT NULL,
                dhash TEXT NOT NULL,
                ahash TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                UNIQUE(media_id, timestamp_ms)
            );
            """
        )
        connection.executemany(
            "INSERT INTO frames (media_id, timestamp_ms, dhash, ahash, width, height)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(m, t, f"{d & 0xFFFFFFFFFFFFFFFF:016x}", f"{a & 0xFFFFFFFFFFFFFFFF:016x}", w, h)
             for m, t, d, a, w, h in rows],
        )
        connection.commit()
        connection.close()

    def test_text_hashes_are_converted_and_still_match(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        self.add_frame(media, 1000, BASE, BASE)
        self.add_frame(media, 2000, flip(BASE, 40), flip(BASE, 40))
        path = self.database.path

        self._rewrite_hashes_as_text(path)
        with sqlite3.connect(path) as connection:
            types = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(frames)")}
        self.assertEqual(types["dhash"], "TEXT")

        migrated = Database(path)
        with sqlite3.connect(path) as connection:
            types = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(frames)")}
            remaining = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertEqual(types["dhash"], "INTEGER")
        self.assertNotIn("frames_legacy", remaining)

        results = migrated.search(
            Fingerprint(dhash=BASE, ahash=BASE, width=480, height=270), allow_adult=False
        )
        self.assertEqual(results[0]["timestamp_ms"], 1000)
        self.assertEqual(results[0]["similarity"], 100.0)

    def test_high_bit_hashes_survive_the_signed_roundtrip(self) -> None:
        self.add_source("s")
        media = self.add_media("s", "Film")
        top = (1 << 64) - 1
        self.add_frame(media, 1000, top, top)

        with sqlite3.connect(self.database.path) as connection:
            stored = connection.execute("SELECT dhash FROM frames").fetchone()[0]
        self.assertEqual(stored, to_signed64(top))

        results = self.query(top, top)
        self.assertEqual(results[0]["similarity"], 100.0)


if __name__ == "__main__":
    unittest.main()
