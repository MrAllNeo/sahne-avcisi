from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .database import Database


DEFAULT_FEEDS = (
    "https://fmhy.net/videopiracyguide",
    "https://fmhy.net/non-english",
)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def discover_links(html: str, feed_url: str) -> list[dict]:
    parser = LinkParser()
    parser.feed(html)
    feed_host = urlparse(feed_url).netloc.lower()
    discovered: dict[str, dict] = {}

    for href, label in parser.links:
        absolute = urljoin(feed_url, href)
        parsed = urlparse(absolute)
        host = parsed.netloc.lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if host == feed_host.removeprefix("www.") or host.endswith("github.com"):
            continue
        source_id = "fmhy-" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:12]
        name = re.sub(r"\s+", " ", label).strip(" -|•") or host
        discovered[host] = {
            "id": source_id,
            "name": name[:120],
            "base_url": f"{parsed.scheme}://{parsed.netloc}/",
            "kind": "discovered",
            "category": "mixed",
            "adult": False,
            "status": "review-required",
            "priority": 40,
            "notes": "FMHY kataloğundan otomatik keşfedildi; etkinleştirmeden önce inceleme gerekir.",
            "discovered_from": feed_url,
        }
    return list(discovered.values())


def fetch_feed(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "SahneAvcisi/0.1 (+public-source-registry)"})
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read(5_000_000).decode(content_type, errors="replace")


def sync_fmhy(database: Database, feeds: list[str] | None = None) -> dict:
    feed_urls = feeds or list(DEFAULT_FEEDS)
    total = 0
    errors: list[dict] = []
    for feed_url in feed_urls:
        try:
            html = fetch_feed(feed_url)
            sources = discover_links(html, feed_url)
            for source in sources:
                database.upsert_source(source)
            total += len(sources)
        except Exception as exc:  # surfaced to the admin response without stopping other feeds
            errors.append({"feed": feed_url, "error": str(exc)})
    return {"discovered": total, "feeds": len(feed_urls), "errors": errors}

