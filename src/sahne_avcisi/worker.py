from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from urllib.parse import urlparse, urlunparse

from .adapters import (
    AdapterError,
    AdapterRegistry,
    UnsafeUrlError,
    UnsupportedMediaError,
)
from .database import Database
from .hls import HlsMirror
from .indexer import StreamingUnsupportedError, index_local_video, index_stream
from .source_registry import load_seed_sources


class HostLimiter:
    """Caps how many transfers run against one host at a time.

    Concurrency is for our own throughput; the catalogue we pull from should
    not notice it. Every job in a bulk import usually targets the same host,
    so without this the limit would be meaningless.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._lock = threading.Lock()
        self._slots: dict[str, threading.Semaphore] = {}

    @contextmanager
    def hold(self, url: str) -> Iterator[None]:
        host = (urlparse(url).hostname or "").lower()
        with self._lock:
            slot = self._slots.setdefault(host, threading.Semaphore(self.limit))
        slot.acquire()
        try:
            yield
        finally:
            slot.release()


class IndexWorker:
    def __init__(
        self,
        database: Database,
        *,
        registry: AdapterRegistry | None = None,
        max_video_bytes: int = 1024 * 1024 * 1024,
        max_hls_duration_seconds: float = 4 * 60 * 60,
        interval_seconds: float = 2.0,
        host_limiter: HostLimiter | None = None,
    ) -> None:
        self.database = database
        self.registry = registry or AdapterRegistry(max_item_bytes=max_video_bytes)
        self.max_video_bytes = max_video_bytes
        self.max_hls_duration_seconds = max_hls_duration_seconds
        self.interval_seconds = interval_seconds
        self.host_limiter = host_limiter or HostLimiter(1)

    def run_once(self) -> dict | None:
        job = self.database.claim_index_job()
        if job is None:
            return None
        source = self.database.get_source(job["source_id"])
        if source is None:
            self.database.stop_index_job(job["id"], status="failed", error="Kaynak kaydı bulunamadı.")
            return {"job_id": job["id"], "status": "failed"}

        try:
            with self.host_limiter.hold(job["page_url"]):
                return self._process(job, source)
        except (UnsafeUrlError, UnsupportedMediaError) as exc:
            self.database.stop_index_job(job["id"], status="blocked", error=str(exc))
            return {"job_id": job["id"], "status": "blocked", "error": str(exc)}
        except (AdapterError, OSError, ValueError, subprocess.SubprocessError) as exc:
            self.database.stop_index_job(job["id"], status="failed", error=str(exc))
            return {"job_id": job["id"], "status": "failed", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - never leave a job stuck in "running"
            self.database.stop_index_job(job["id"], status="failed", error=f"Beklenmeyen hata: {exc}")
            return {"job_id": job["id"], "status": "failed", "error": str(exc)}

    def _process(self, job: dict, source: dict) -> dict:
        resolved = self.registry.resolve(source, job["page_url"])
        if not resolved.indexable or not resolved.media_url:
            self.database.stop_index_job(
                job["id"],
                status="blocked",
                error=resolved.reason or "Kaynak indekslenebilir doğrudan video sunmadı.",
                adapter=resolved.adapter,
                player_type=resolved.player_type,
                media_url=_redact_url(resolved.media_url),
            )
            return {"job_id": job["id"], "status": "blocked", "adapter": resolved.adapter}

        suffix = Path(urlparse(resolved.media_url).path).suffix.lower()
        is_hls = resolved.player_type == "hls" or suffix in {".m3u8", ".m3u"}
        if not is_hls:
            indexed = self._index_streamed(job, source, resolved)
            if indexed is not None:
                return self._finish(job, resolved, indexed)

        with TemporaryDirectory(prefix="sahne-video-") as temp_dir:
            if is_hls:
                mirrored = HlsMirror(
                    self.registry.client,
                    max_duration_seconds=self.max_hls_duration_seconds,
                ).mirror(
                    resolved.media_url,
                    Path(temp_dir) / "hls",
                    max_bytes=self.max_video_bytes,
                )
                media_path = mirrored.manifest_path
            else:
                media_path = Path(temp_dir) / f"source{suffix or '.video'}"
                self.registry.client.download_video(
                    resolved.media_url,
                    media_path,
                    max_bytes=self.max_video_bytes,
                )
            indexed = index_local_video(
                self.database,
                media_file=media_path,
                source_id=source["id"],
                source_url=resolved.page_url,
                title=job.get("title_override") or resolved.title,
                category=source["category"],
                adult=bool(source["adult"]),
                episode=None,
                interval_seconds=self.interval_seconds,
            )

        return self._finish(job, resolved, indexed)

    def _index_streamed(self, job: dict, source: dict, resolved) -> dict | None:  # noqa: ANN001
        """Index straight off the wire, or return None to use the file path."""
        try:
            with self.registry.client.stream_video(
                resolved.media_url, max_bytes=self.max_video_bytes
            ) as chunks:
                return index_stream(
                    self.database,
                    chunks=chunks,
                    source_id=source["id"],
                    source_url=resolved.page_url,
                    title=job.get("title_override") or resolved.title,
                    category=source["category"],
                    adult=bool(source["adult"]),
                    episode=None,
                    interval_seconds=self.interval_seconds,
                    duration_ms=resolved.duration_ms,
                )
        except StreamingUnsupportedError:
            # Some containers keep their index at the end of the file and need
            # a seekable copy; fall back to downloading it.
            return None

    def _finish(self, job: dict, resolved, indexed: dict) -> dict:  # noqa: ANN001
        self.database.complete_index_job(
            job["id"],
            adapter=resolved.adapter,
            player_type=resolved.player_type,
            media_url=_redact_url(resolved.media_url),
            media_id=indexed["media_id"],
            frame_count=indexed["frames"],
        )
        return {"job_id": job["id"], "status": "completed", **indexed}


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="İzinli kaynak indeksleme kuyruğunu işler.")
    parser.add_argument("--database", type=Path, default=Path("data/sahne-avcisi.sqlite3"))
    parser.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--once", action="store_true", help="Tek işi işleyip çıkar.")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--max-video-mb", type=int, default=1024)
    parser.add_argument("--max-hls-hours", type=float, default=4.0)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Aynı anda işlenecek iş sayısı (öntanımlı 1, yani sıralı).",
    )
    parser.add_argument(
        "--per-host",
        type=int,
        default=2,
        help="Tek bir kaynak alan adına aynı anda açılacak en fazla aktarım.",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=60,
        help="Bu süreden uzun süredir 'running' kalan işleri başlangıçta yeniden kuyruğa al.",
    )
    args = parser.parse_args()

    database = Database(args.database)
    load_seed_sources(database, args.sources)
    requeued = database.requeue_stale_jobs(args.stale_minutes)
    if requeued:
        print(f"{requeued} takılı kalmış iş yeniden kuyruğa alındı.", flush=True)
    limiter = HostLimiter(args.per_host)
    concurrency = max(1, min(args.concurrency, 16))

    def build_worker() -> IndexWorker:
        return IndexWorker(
            database,
            max_video_bytes=max(1, args.max_video_mb) * 1024 * 1024,
            max_hls_duration_seconds=max(0.1, args.max_hls_hours) * 60 * 60,
            interval_seconds=max(0.5, args.interval),
            host_limiter=limiter,
        )

    if args.once:
        print(json.dumps(build_worker().run_once() or {"status": "idle"}, ensure_ascii=False))
        return

    stop = threading.Event()
    output_lock = threading.Lock()

    def loop() -> None:
        worker = build_worker()
        while not stop.is_set():
            try:
                result = worker.run_once()
            except Exception as exc:  # noqa: BLE001 - one bad job must not end the run
                with output_lock:
                    print(json.dumps({"status": "worker_error", "error": str(exc)}), flush=True)
                stop.wait(max(0.5, args.poll_seconds))
                continue
            if result:
                with output_lock:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
            else:
                stop.wait(max(0.5, args.poll_seconds))

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(concurrency)]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()
        for thread in threads:
            thread.join(timeout=30)


if __name__ == "__main__":
    main()
