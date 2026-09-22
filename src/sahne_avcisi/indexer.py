from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .database import Database
from .fingerprint import fingerprint_bytes
from .source_registry import load_seed_sources


def _input_options(media_file: Path) -> list[str]:
    if media_file.suffix.lower() not in {".m3u8", ".m3u"}:
        return []
    return [
        "-protocol_whitelist",
        "file,data",
        "-allowed_extensions",
        "ALL",
    ]


def probe_duration_ms(media_file: Path) -> int | None:
    if not shutil.which("ffprobe"):
        raise FileNotFoundError("FFprobe bulunamadı. Lütfen FFmpeg paketini (ffprobe dahil) kurun.")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        *_input_options(media_file),
        str(media_file),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    duration = json.loads(result.stdout).get("format", {}).get("duration")
    return int(float(duration) * 1000) if duration else None


def extract_frames(media_file: Path, output_dir: Path, interval_seconds: float) -> list[Path]:
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("FFmpeg bulunamadı. Lütfen FFmpeg paketini kurun.")
    pattern = output_dir / "frame-%08d.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *_input_options(media_file),
        "-i",
        str(media_file),
        "-vf",
        f"fps=1/{interval_seconds},scale=480:-2",
        "-q:v",
        "4",
        str(pattern),
    ]
    subprocess.run(command, check=True)
    return sorted(output_dir.glob("frame-*.jpg"))


def index_local_video(
    database: Database,
    *,
    media_file: Path,
    source_id: str,
    source_url: str,
    title: str,
    category: str,
    adult: bool,
    episode: str | None,
    interval_seconds: float,
) -> dict:
    if database.get_source(source_id) is None:
        raise ValueError(f"Kaynak bulunamadı: {source_id!r}. Önce kaynağı config/sources.json içinde tanımlayın.")
    if not media_file.is_file():
        raise FileNotFoundError(media_file)
    duration_ms = probe_duration_ms(media_file)
    media_id = database.create_media(
        source_id=source_id,
        title=title,
        source_url=source_url,
        category=category,
        adult=adult,
        episode=episode,
        duration_ms=duration_ms,
    )
    with tempfile.TemporaryDirectory(prefix="sahne-frames-") as temp_dir:
        frames = extract_frames(media_file, Path(temp_dir), interval_seconds)
        fingerprints = [
            (int(index * interval_seconds * 1000), fingerprint_bytes(frame.read_bytes()))
            for index, frame in enumerate(frames)
        ]
        database.replace_frames(media_id, fingerprints)
    return {"media_id": media_id, "frames": len(frames), "duration_ms": duration_ms}


def main() -> None:
    parser = argparse.ArgumentParser(description="Yerel ve izinli bir videoyu Sahne Avcısı indeksine ekler.")
    parser.add_argument("media_file", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/sahne-avcisi.sqlite3"))
    parser.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--episode")
    parser.add_argument("--category", default="movie-tv")
    parser.add_argument("--adult", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    database = Database(args.database)
    load_seed_sources(database, args.sources)
    result = index_local_video(
        database,
        media_file=args.media_file,
        source_id=args.source_id,
        source_url=args.source_url,
        title=args.title,
        category=args.category,
        adult=args.adult,
        episode=args.episode,
        interval_seconds=max(0.5, args.interval),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
