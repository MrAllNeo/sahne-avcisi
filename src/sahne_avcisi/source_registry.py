from __future__ import annotations

import json
from pathlib import Path

from .database import Database


def load_seed_sources(database: Database, source_file: Path) -> int:
    sources = json.loads(source_file.read_text(encoding="utf-8"))
    if not isinstance(sources, list):
        raise ValueError("Kaynak yapılandırması bir JSON listesi olmalı.")
    for source in sources:
        database.upsert_source(source)
    return len(sources)

