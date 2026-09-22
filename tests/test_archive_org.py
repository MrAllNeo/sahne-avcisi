import json
import unittest

from sahne_avcisi.archive_org import (
    ArchiveOrgError,
    LicenceNotClearError,
    licence_is_clear,
    parse_identifier,
    parse_metadata,
    parse_search_results,
    plan_item,
    select_video_file,
)

# Shaped after a real https://archive.org/metadata/<id> response.
ITEM = {
    "metadata": {
        "identifier": "the-timid-toreador-1940",
        "title": "The Timid Toreador (1940)",
        "mediatype": "movies",
        "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
        "collection": ["feature_films", "moviesandfilms"],
    },
    "files": [
        {"name": "The Timid Toreador (1940).mp4", "format": "MPEG4", "size": "25000000", "length": "378.51"},
        {"name": "the-timid-toreador-1940_archive.torrent", "format": "Archive BitTorrent", "size": "20319"},
        {"name": "the-timid-toreador-1940_meta.xml", "format": "Metadata", "size": "1686"},
    ],
}


class ParseIdentifierTests(unittest.TestCase):
    def test_accepts_details_download_and_embed_paths(self) -> None:
        for url in (
            "https://archive.org/details/some-film",
            "https://archive.org/download/some-film/file.mp4",
            "https://archive.org/embed/some-film",
            "https://www.archive.org/details/some-film",
        ):
            self.assertEqual(parse_identifier(url), "some-film", url)

    def test_rejects_foreign_hosts_and_unrelated_paths(self) -> None:
        for url in (
            "https://evil.example/details/some-film",
            "https://archive.org/",
            "https://archive.org/search?query=x",
        ):
            self.assertIsNone(parse_identifier(url), url)

    def test_rejects_traversal_segments(self) -> None:
        self.assertIsNone(parse_identifier("https://archive.org/details/.."))


class LicenceTests(unittest.TestCase):
    def test_public_domain_and_creative_commons_are_accepted(self) -> None:
        self.assertTrue(licence_is_clear({"licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"}))
        self.assertTrue(licence_is_clear({"rights": "Public Domain"}))

    def test_missing_licence_is_not_permission(self) -> None:
        self.assertFalse(licence_is_clear({}))
        self.assertFalse(licence_is_clear({"licenseurl": None, "rights": "All rights reserved"}))

    def test_plan_refuses_an_item_without_a_licence(self) -> None:
        document = {**ITEM, "metadata": {**ITEM["metadata"], "licenseurl": None}}
        with self.assertRaises(LicenceNotClearError):
            plan_item(document)

    def test_plan_refuses_a_non_video_item(self) -> None:
        document = {**ITEM, "metadata": {**ITEM["metadata"], "mediatype": "texts"}}
        with self.assertRaises(ArchiveOrgError):
            plan_item(document)


class FileSelectionTests(unittest.TestCase):
    def test_metadata_and_torrent_files_are_never_selected(self) -> None:
        entry = select_video_file(ITEM["files"])
        self.assertEqual(entry["name"], "The Timid Toreador (1940).mp4")

    def test_preferred_format_wins_over_a_larger_fallback(self) -> None:
        files = [
            {"name": "low.ogv", "format": "Ogg Video", "size": "900000000"},
            {"name": "good.mp4", "format": "h.264", "size": "10000000"},
        ]
        self.assertEqual(select_video_file(files)["name"], "good.mp4")

    def test_files_above_the_size_ceiling_are_excluded(self) -> None:
        files = [{"name": "huge.mp4", "format": "MPEG4", "size": "2000000000"}]
        self.assertIsNone(select_video_file(files, max_bytes=1024 * 1024 * 1024))

    def test_an_unknown_size_is_kept_for_the_download_guard(self) -> None:
        files = [{"name": "unknown.mp4", "format": "MPEG4"}]
        self.assertIsNotNone(select_video_file(files, max_bytes=1024))


class PlanTests(unittest.TestCase):
    def test_plan_builds_an_encoded_download_url(self) -> None:
        plan = plan_item(ITEM)
        self.assertEqual(plan["title"], "The Timid Toreador (1940)")
        self.assertEqual(
            plan["media_url"],
            "https://archive.org/download/the-timid-toreador-1940/"
            "The%20Timid%20Toreador%20%281940%29.mp4",
        )
        self.assertAlmostEqual(plan["duration_seconds"], 378.51)

    def test_missing_item_is_reported(self) -> None:
        with self.assertRaises(ArchiveOrgError):
            parse_metadata("{}")


class SearchTests(unittest.TestCase):
    def test_results_are_extracted_and_blank_identifiers_dropped(self) -> None:
        payload = json.dumps(
            {"response": {"docs": [{"identifier": "a", "title": "A"}, {"identifier": ""}]}}
        )
        results = parse_search_results(payload)
        self.assertEqual([item["identifier"] for item in results], ["a"])

    def test_unexpected_shape_is_rejected(self) -> None:
        with self.assertRaises(ArchiveOrgError):
            parse_search_results(json.dumps({"response": {}}))


if __name__ == "__main__":
    unittest.main()
