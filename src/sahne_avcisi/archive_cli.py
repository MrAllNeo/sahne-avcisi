from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import PublicHttpClient
from .archive_org import parse_search_results, search_url
from .database import Database
from .source_registry import load_seed_sources

SOURCE_ID = "archive-org"

# Only items that state a public-domain licence, so the adapter's licence gate
# does not reject most of what we queue.
DEFAULT_QUERY = (
    "collection:(feature_films) AND mediatype:(movies) AND licenseurl:(*publicdomain*)"
)


def import_items(
    database: Database,
    *,
    query: str,
    limit: int,
    page: int = 1,
    client: PublicHttpClient | None = None,
) -> dict:
    client = client or PublicHttpClient()
    payload, _ = client.fetch_json(search_url(query, rows=limit, page=page))
    results = parse_search_results(payload)

    queued, skipped = 0, []
    for item in results:
        page_url = f"https://archive.org/details/{item['identifier']}"
        try:
            database.enqueue_index_job(
                source_id=SOURCE_ID, page_url=page_url, title=item["title"]
            )
            queued += 1
        except ValueError as exc:
            skipped.append({"identifier": item["identifier"], "reason": str(exc)})
    return {"found": len(results), "queued": queued, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive.org'daki kamu malı filmleri indeksleme kuyruğuna ekler."
    )
    parser.add_argument("--database", type=Path, default=Path("data/sahne-avcisi.sqlite3"))
    parser.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Archive.org arama sorgusu")
    parser.add_argument("--limit", type=int, default=25, help="Kuyruğa eklenecek en fazla öğe")
    parser.add_argument("--page", type=int, default=1, help="Arama sonuç sayfası")
    args = parser.parse_args()

    database = Database(args.database)
    load_seed_sources(database, args.sources)
    result = import_items(
        database,
        query=args.query,
        limit=max(1, min(args.limit, 500)),
        page=max(1, args.page),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
