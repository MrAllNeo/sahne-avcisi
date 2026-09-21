from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .adapters import source_allows_url
from .database import Database
from .fingerprint import InvalidImageError, fingerprint_bytes
from .fmhy import sync_fmhy
from .source_registry import load_seed_sources
from .trace_moe import TraceMoeClient, TraceMoeError


PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"
PROJECT_DIR = PACKAGE_DIR.parents[1]


class Application:
    def __init__(self) -> None:
        data_dir = Path(os.environ.get("SAHNE_DATA_DIR", PROJECT_DIR / "data"))
        self.database = Database(data_dir / "sahne-avcisi.sqlite3")
        configured_source_file = os.environ.get("SAHNE_SOURCES_FILE")
        source_file = Path(configured_source_file) if configured_source_file else PROJECT_DIR / "config" / "sources.json"
        if not source_file.is_file():
            source_file = Path.cwd() / "config" / "sources.json"
        load_seed_sources(self.database, source_file)
        self.trace_moe = TraceMoeClient(api_key=os.environ.get("TRACE_MOE_API_KEY"))


APP = Application()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "SahneAvcisi/0.4"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "service": "sahne-avcisi", "version": "0.4.0"})
            return
        if parsed.path == "/api/stats":
            self.send_json(APP.database.stats())
            return
        if parsed.path == "/api/sources":
            query = parse_qs(parsed.query)
            include_adult = query.get("adult", ["false"])[0].lower() == "true"
            self.send_json({"items": APP.database.list_sources(include_adult=include_adult)})
            return
        if parsed.path in {
            "/api/catalog/runs",
            "/api/catalog/events",
            "/api/adapters/queue",
            "/api/index/jobs",
        }:
            if not self.is_admin():
                self.send_json({"error": "Yönetici anahtarı gerekli."}, HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query)
            limit = self.parse_limit(query, default=50)
            if parsed.path == "/api/catalog/runs":
                self.send_json({"items": APP.database.list_catalog_runs(limit)})
            elif parsed.path == "/api/catalog/events":
                self.send_json({"items": APP.database.list_source_events(limit)})
            elif parsed.path == "/api/index/jobs":
                self.send_json({"items": APP.database.list_index_jobs(limit)})
            else:
                include_adult = query.get("adult", ["false"])[0].lower() == "true"
                self.send_json(
                    {"items": APP.database.list_adapter_queue(include_adult=include_adult, limit=limit)}
                )
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            self.handle_search()
            return
        if parsed.path == "/api/sources/sync-fmhy":
            self.handle_fmhy_sync()
            return
        if parsed.path == "/api/index/jobs":
            self.handle_index_job()
            return
        self.send_json({"error": "Rota bulunamadı."}, HTTPStatus.NOT_FOUND)

    def handle_search(self) -> None:
        try:
            payload = self.read_json(max_bytes=20 * 1024 * 1024)
            encoded = str(payload.get("image_base64", ""))
            content_type = self.image_content_type(encoded)
            if "," in encoded:
                encoded = encoded.split(",", 1)[1]
            image_bytes = base64.b64decode(encoded, validate=True)
            fingerprint = fingerprint_bytes(image_bytes)
            category = str(payload.get("category", "all"))
            allow_adult = bool(payload.get("allow_adult", False))
            limit = max(1, min(int(payload.get("limit", 8)), 25))
            results = APP.database.search(
                fingerprint,
                allow_adult=allow_adult,
                category=category,
                limit=limit,
            )
            trace_status = {"requested": False, "searched_frames": 0}
            external_error = None
            if payload.get("use_trace_moe") is True and category in {"all", "anime"}:
                trace_status["requested"] = True
                try:
                    trace_result = APP.trace_moe.search(
                        image_bytes,
                        content_type=content_type,
                        allow_adult=allow_adult,
                        limit=limit,
                    )
                    results.extend(trace_result["results"])
                    trace_status.update(
                        {
                            "searched_frames": trace_result["frame_count"],
                            "quota": trace_result["quota"],
                            "quota_used": trace_result["quota_used"],
                        }
                    )
                except TraceMoeError as exc:
                    external_error = str(exc)
            results.sort(key=lambda item: float(item.get("similarity", 0)), reverse=True)
            self.send_json(
                {
                    "query": {"width": fingerprint.width, "height": fingerprint.height},
                    "results": results[:limit],
                    "indexed_frames": APP.database.stats()["frames"],
                    "providers": {"trace_moe": trace_status},
                    "external_error": external_error,
                }
            )
        except (ValueError, InvalidImageError, binascii.Error) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_fmhy_sync(self) -> None:
        if not self.is_admin():
            self.send_json({"error": "Yönetici anahtarı gerekli."}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = self.read_json(max_bytes=64 * 1024, allow_empty=True)
            feeds = payload.get("feeds") if payload else None
            self.send_json(sync_fmhy(APP.database, feeds))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_index_job(self) -> None:
        if not self.is_admin():
            self.send_json({"error": "Yönetici anahtarı gerekli."}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = self.read_json(max_bytes=32 * 1024)
            source_id = str(payload.get("source_id", "")).strip()
            page_url = str(payload.get("page_url", "")).strip()
            title = str(payload.get("title", "")).strip() or None
            if not source_id or not page_url or len(page_url) > 2048:
                raise ValueError("source_id ve geçerli page_url zorunludur.")
            source = APP.database.get_source(source_id)
            if source is None:
                raise ValueError("Kaynak bulunamadı.")
            if urlparse(page_url).scheme.lower() != "https" or not source_allows_url(source, page_url):
                raise ValueError("Adres seçilen kaynağın HTTPS alan adına ait olmalı.")
            job = APP.database.enqueue_index_job(source_id=source_id, page_url=page_url, title=title)
            self.send_json({"job": job}, HTTPStatus.ACCEPTED)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def is_admin(self) -> bool:
        expected = os.environ.get("SAHNE_ADMIN_TOKEN")
        return bool(expected) and self.headers.get("X-Admin-Token") == expected

    @staticmethod
    def parse_limit(query: dict[str, list[str]], default: int) -> int:
        try:
            return max(1, min(int(query.get("limit", [str(default)])[0]), 500))
        except ValueError:
            return default

    @staticmethod
    def image_content_type(data_url: str) -> str:
        if not data_url.startswith("data:") or "," not in data_url:
            return "application/octet-stream"
        header = data_url[5:].split(",", 1)[0].lower()
        media_type = header.split(";", 1)[0]
        return media_type if media_type in {"image/jpeg", "image/png", "image/webp"} else "application/octet-stream"

    def read_json(self, max_bytes: int, allow_empty: bool = False) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0 and allow_empty:
            return {}
        if length <= 0 or length > max_bytes:
            raise ValueError("İstek boyutu geçersiz.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Geçersiz JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON nesnesi bekleniyor.")
        return payload

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        requested = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in requested.parents and requested != WEB_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not requested.is_file():
            requested = WEB_DIR / "index.html"
        content = requested.read_bytes()
        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store" if requested.name == "index.html" else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    host = os.environ.get("SAHNE_HOST", "127.0.0.1")
    port = int(os.environ.get("SAHNE_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"Sahne Avcısı http://{host}:{port} adresinde çalışıyor")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
