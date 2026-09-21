from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import Database
from .fmhy import sync_fmhy
from .source_registry import load_seed_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="FMHY kataloglarını eşitler ve değişiklik geçmişini kaydeder.")
    parser.add_argument("--database", type=Path, default=Path("data/sahne-avcisi.sqlite3"))
    parser.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--feed", action="append", dest="feeds", help="Varsayılanlar yerine eşitlenecek FMHY sayfası")
    args = parser.parse_args()

    database = Database(args.database)
    load_seed_sources(database, args.sources)
    result = sync_fmhy(database, args.feeds)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
