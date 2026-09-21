from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from sahne_avcisi.server import admin_token_matches, should_query_trace_moe


class PureFunctionTests(unittest.TestCase):
    def test_admin_token_matches_requires_exact_match(self) -> None:
        self.assertTrue(admin_token_matches("secret-token", "secret-token"))
        self.assertFalse(admin_token_matches("secret-token", "wrong-token"))
        self.assertFalse(admin_token_matches("secret-token", "secret-toke"))
        self.assertFalse(admin_token_matches("secret-token", "secret-token-extra"))

    def test_admin_token_matches_rejects_missing_values(self) -> None:
        self.assertFalse(admin_token_matches(None, "anything"))
        self.assertFalse(admin_token_matches("expected", None))
        self.assertFalse(admin_token_matches("", "anything"))
        self.assertFalse(admin_token_matches("expected", ""))

    def test_admin_token_matches_uses_constant_time_compare(self) -> None:
        with patch("sahne_avcisi.server.hmac.compare_digest", return_value=True) as mocked:
            self.assertTrue(admin_token_matches("a", "b"))
            mocked.assert_called_once_with(b"b", b"a")

    def test_should_query_trace_moe_requires_enabled_and_requested(self) -> None:
        self.assertFalse(
            should_query_trace_moe(enabled=False, requested=True, category="anime", best_local_similarity=0.0)
        )
        self.assertFalse(
            should_query_trace_moe(enabled=True, requested=False, category="anime", best_local_similarity=0.0)
        )

    def test_should_query_trace_moe_requires_matching_category(self) -> None:
        self.assertTrue(
            should_query_trace_moe(enabled=True, requested=True, category="anime", best_local_similarity=0.0)
        )
        self.assertTrue(
            should_query_trace_moe(enabled=True, requested=True, category="all", best_local_similarity=0.0)
        )
        self.assertFalse(
            should_query_trace_moe(enabled=True, requested=True, category="movie-tv", best_local_similarity=0.0)
        )

    def test_should_query_trace_moe_skips_when_local_match_is_strong(self) -> None:
        self.assertFalse(
            should_query_trace_moe(enabled=True, requested=True, category="anime", best_local_similarity=95.0)
        )
        self.assertFalse(
            should_query_trace_moe(enabled=True, requested=True, category="anime", best_local_similarity=90.0)
        )
        self.assertTrue(
            should_query_trace_moe(enabled=True, requested=True, category="anime", best_local_similarity=89.9)
        )


@contextmanager
def running_server(env_overrides: dict[str, str]):
    """Boots a fresh sahne_avcisi.server module (and Application) with the given env vars."""
    temp_dir = tempfile.TemporaryDirectory()
    try:
        sources_path = Path(temp_dir.name) / "sources.json"
        sources_path.write_text("[]", encoding="utf-8")
        env = {
            "SAHNE_DATA_DIR": temp_dir.name,
            "SAHNE_SOURCES_FILE": str(sources_path),
            "SAHNE_HOST": "127.0.0.1",
            "SAHNE_PORT": "0",
            **env_overrides,
        }
        with patch.dict(os.environ, env, clear=False):
            import sahne_avcisi.server as server_module

            importlib.reload(server_module)
            httpd = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.RequestHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                yield server_module, httpd.server_address[1]
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
    finally:
        temp_dir.cleanup()


class ServerIntegrationTests(unittest.TestCase):
    def test_health_endpoint_is_public(self) -> None:
        with running_server({}) as (_module, port):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(body["ok"])

    def test_admin_endpoint_rejects_missing_and_wrong_token(self) -> None:
        with running_server({"SAHNE_ADMIN_TOKEN": "correct-horse"}) as (_module, port):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/api/index/jobs")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 401)

            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/api/index/jobs", headers={"X-Admin-Token": "wrong"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 401)

    def test_admin_endpoint_accepts_correct_token(self) -> None:
        with running_server({"SAHNE_ADMIN_TOKEN": "correct-horse"}) as (_module, port):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/api/index/jobs", headers={"X-Admin-Token": "correct-horse"})
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(body["items"], [])

    def test_trace_moe_kill_switch_prevents_external_call_even_if_requested(self) -> None:
        with running_server({"SAHNE_TRACE_MOE": "0"}) as (module, port):
            with patch.object(module.APP.trace_moe, "search", side_effect=AssertionError("must not be called")):
                image_base64 = _tiny_png_base64()
                connection = HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/search",
                    body=json.dumps(
                        {"image_base64": image_base64, "category": "anime", "use_trace_moe": True}
                    ),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertFalse(body["providers"]["trace_moe"]["requested"])
                self.assertFalse(body["providers"]["trace_moe"]["enabled"])


def _tiny_png_base64() -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (32, 32), (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


if __name__ == "__main__":
    unittest.main()
