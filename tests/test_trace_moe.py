from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from sahne_avcisi.trace_moe import TraceMoeClient, TraceMoeError


class FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode()
        self.closed = False

    def read(self, limit: int) -> bytes:
        return self.body[:limit]

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests = []

    def open(self, request, timeout: float):  # noqa: ANN001
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)


def result(*, adult: bool, similarity: float, title: str, image: str = "/image/abc") -> dict:
    return {
        "anilist": {
            "id": 42 if not adult else 99,
            "idMal": None,
            "isAdult": adult,
            "synonyms": [],
            "title": {"native": None, "romaji": title, "english": None},
        },
        "filename": f"{title}.mkv",
        "episode": 3,
        "from": 65.2,
        "to": 67.0,
        "at": 66.1,
        "duration": 1440,
        "similarity": similarity,
        "video": "/video/abc",
        "image": image,
    }


class TraceMoeTests(unittest.TestCase):
    def test_search_posts_image_and_normalizes_anilist_result(self) -> None:
        opener = FakeOpener(
            {
                "frameCount": 123456,
                "error": "",
                "quota": 100,
                "quotaUsed": 4,
                "result": [
                    result(adult=False, similarity=0.93, title="Örnek Anime"),
                    result(adult=True, similarity=0.99, title="18+ Anime"),
                ],
            }
        )
        client = TraceMoeClient(api_key="secret", opener=opener)
        response = client.search(b"image-bytes", content_type="image/png", allow_adult=False)

        self.assertEqual(response["frame_count"], 123456)
        self.assertEqual(len(response["results"]), 1)
        match = response["results"][0]
        self.assertEqual(match["title"], "Örnek Anime")
        self.assertEqual(match["episode"], "3")
        self.assertEqual(match["timestamp"], "01:06")
        self.assertEqual(match["similarity"], 93.0)
        self.assertEqual(match["preview_video"], "https://api.trace.moe/video/abc")
        request, timeout = opener.requests[0]
        self.assertEqual(request.data, b"image-bytes")
        self.assertIn("anilistInfo", request.full_url)
        self.assertEqual(request.get_header("Content-type"), "image/png")
        self.assertEqual(request.get_header("X-trace-key"), "secret")
        self.assertEqual(timeout, 25.0)

    def test_adult_results_require_permission_and_external_previews_are_filtered(self) -> None:
        raw = [
            result(adult=True, similarity=0.99, title="18+ Anime"),
            result(
                adult=False,
                similarity=0.91,
                title="Normal Anime",
                image="https://tracker.example/image.jpg",
            ),
        ]
        hidden = TraceMoeClient._normalize_results(raw, allow_adult=False, limit=5)
        visible = TraceMoeClient._normalize_results(raw, allow_adult=True, limit=5)
        self.assertEqual([item["title"] for item in hidden], ["Normal Anime"])
        self.assertEqual(len(visible), 2)
        self.assertIsNone(hidden[0]["preview_image"])

    def test_api_error_is_reported(self) -> None:
        client = TraceMoeClient(opener=FakeOpener({"frameCount": 0, "error": "bad image", "result": []}))
        with self.assertRaisesRegex(TraceMoeError, "bad image"):
            client.search(b"broken", content_type="image/png", allow_adult=False)

    def test_429_returns_turkish_rate_limit_warning(self) -> None:
        client = TraceMoeClient(opener=FakeHttpErrorOpener(429))
        with self.assertRaisesRegex(TraceMoeError, "istek sınırına ulaşıldı"):
            client.search(b"image", content_type="image/png", allow_adult=False)

    def test_402_returns_distinct_turkish_quota_warning(self) -> None:
        client = TraceMoeClient(opener=FakeHttpErrorOpener(402))
        with self.assertRaisesRegex(TraceMoeError, "kotanız doldu"):
            client.search(b"image", content_type="image/png", allow_adult=False)

    def test_402_and_429_messages_are_distinct(self) -> None:
        rate_limited = TraceMoeClient(opener=FakeHttpErrorOpener(429))
        quota_exceeded = TraceMoeClient(opener=FakeHttpErrorOpener(402))
        with self.assertRaises(TraceMoeError) as rate_limit_ctx:
            rate_limited.search(b"image", content_type="image/png", allow_adult=False)
        with self.assertRaises(TraceMoeError) as quota_ctx:
            quota_exceeded.search(b"image", content_type="image/png", allow_adult=False)
        self.assertNotEqual(str(rate_limit_ctx.exception), str(quota_ctx.exception))

    def test_503_returns_generic_unavailable_warning(self) -> None:
        client = TraceMoeClient(opener=FakeHttpErrorOpener(503))
        with self.assertRaisesRegex(TraceMoeError, "yoğun veya geçici"):
            client.search(b"image", content_type="image/png", allow_adult=False)


class FakeHttpErrorOpener:
    def __init__(self, code: int):
        self.code = code

    def open(self, request, timeout: float):  # noqa: ANN001
        raise HTTPError(request.full_url, self.code, "error", {}, None)


if __name__ == "__main__":
    unittest.main()
