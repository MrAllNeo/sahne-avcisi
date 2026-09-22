from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from sahne_avcisi import server as server_module
from sahne_avcisi.ratelimit import RateLimiter

TOKEN = "servis-anahtari"


@contextmanager
def running_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.RequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def call(base: str, path: str, *, token: str | None = None, method: str = "GET"):
    body = json.dumps({"image_base64": ""}).encode() if method == "POST" else None
    request = urllib.request.Request(base + path, data=body, method=method)
    if token is not None:
        request.add_header("X-Sahne-Internal-Token", token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        headers = dict(error.headers)
        error.close()
        return error.code, headers


class TokenGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = os.environ.get("SAHNE_INTERNAL_TOKEN")
        self._limiter = server_module.APP.rate_limiter
        server_module.APP.rate_limiter = RateLimiter(limit=0, window_seconds=60)

    def tearDown(self) -> None:
        server_module.APP.rate_limiter = self._limiter
        if self._original is None:
            os.environ.pop("SAHNE_INTERNAL_TOKEN", None)
        else:
            os.environ["SAHNE_INTERNAL_TOKEN"] = self._original

    def test_without_a_configured_token_the_api_stays_open(self) -> None:
        os.environ.pop("SAHNE_INTERNAL_TOKEN", None)
        with running_server() as base:
            status, _ = call(base, "/api/stats")
        self.assertEqual(status, 200)

    def test_a_configured_token_is_required(self) -> None:
        os.environ["SAHNE_INTERNAL_TOKEN"] = TOKEN
        with running_server() as base:
            self.assertEqual(call(base, "/api/stats")[0], 401)
            self.assertEqual(call(base, "/api/stats", token="yanlis")[0], 401)
            self.assertEqual(call(base, "/api/stats", token=TOKEN)[0], 200)

    def test_search_is_refused_without_the_token(self) -> None:
        os.environ["SAHNE_INTERNAL_TOKEN"] = TOKEN
        with running_server() as base:
            self.assertEqual(call(base, "/api/search", method="POST")[0], 401)

    def test_health_stays_public_for_platform_probes(self) -> None:
        os.environ["SAHNE_INTERNAL_TOKEN"] = TOKEN
        with running_server() as base:
            self.assertEqual(call(base, "/api/health")[0], 200)


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = os.environ.get("SAHNE_INTERNAL_TOKEN")
        os.environ.pop("SAHNE_INTERNAL_TOKEN", None)
        self._limiter = server_module.APP.rate_limiter
        server_module.APP.rate_limiter = RateLimiter(limit=2, window_seconds=600)

    def tearDown(self) -> None:
        server_module.APP.rate_limiter = self._limiter
        if self._original is not None:
            os.environ["SAHNE_INTERNAL_TOKEN"] = self._original

    def test_search_is_throttled_and_advertises_retry_after(self) -> None:
        with running_server() as base:
            first, _ = call(base, "/api/search", method="POST")
            second, _ = call(base, "/api/search", method="POST")
            third, headers = call(base, "/api/search", method="POST")

        # An empty image is rejected as a bad request, which still spends budget.
        self.assertEqual(first, 400)
        self.assertEqual(second, 400)
        self.assertEqual(third, 429)
        self.assertIn("Retry-After", headers)
        self.assertGreater(int(headers["Retry-After"]), 0)

    def test_the_budget_does_not_apply_to_health(self) -> None:
        with running_server() as base:
            for _ in range(5):
                self.assertEqual(call(base, "/api/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
