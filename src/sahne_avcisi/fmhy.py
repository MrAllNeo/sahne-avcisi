from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .database import Database


DEFAULT_FEEDS = (
    "https://fmhy.net/video",
    "https://fmhy.net/non-english",
)

AUXILIARY_HOSTS = {
    "discord.gg",
    "discord.com",
    "github.com",
    "reddit.com",
    "www.reddit.com",
    "rentry.co",
    "rentry.org",
    "greasyfork.org",
    "fmhy-grading.pages.dev",
}

AUXILIARY_LABELS = {
    "status",
    "docs",
    "documentation",
    "invite",
    "guide",
    "grading",
    "mirrors",
    "mirror",
    "proxy",
    "clones",
    "downloader",
    "available countries",
}


@dataclass
class CatalogLink:
    href: str
    label: str
    section: str
    subsection: str
    context: str = ""


@dataclass
class _ListItem:
    text: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


class CatalogParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[CatalogLink] = []
        self.section = ""
        self.subsection = ""
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._list_item: _ListItem | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"h2", "h3"}:
            self._heading_tag = tag
            self._heading_text = []
        elif tag == "li":
            self._list_item = _ListItem()
        elif tag == "a":
            self._anchor_href = dict(attrs).get("href")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._heading_tag:
            self._heading_text.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        if self._list_item is not None:
            self._list_item.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == self._heading_tag:
            heading = _clean_text(" ".join(self._heading_text))
            if tag == "h2":
                self.section = heading
                self.subsection = ""
            else:
                self.subsection = heading
            self._heading_tag = None
            self._heading_text = []
        elif tag == "a" and self._anchor_href is not None:
            label = _clean_text(" ".join(self._anchor_text))
            if self._list_item is not None:
                self._list_item.links.append((self._anchor_href, label))
            else:
                self.links.append(CatalogLink(self._anchor_href, label, self.section, self.subsection))
            self._anchor_href = None
            self._anchor_text = []
        elif tag == "li" and self._list_item is not None:
            context = _clean_text(" ".join(self._list_item.text))
            for href, label in self._list_item.links:
                self.links.append(CatalogLink(href, label, self.section, self.subsection, context))
            self._list_item = None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -|•\u200b")


def _catalog_id(feed_url: str) -> str:
    return "fmhy-" + hashlib.sha256(feed_url.encode("utf-8")).hexdigest()[:12]


def _is_relevant_section(section: str) -> bool:
    normalized = section.casefold()
    if not normalized:
        return True
    blocked = ("download", "torrent", "subtitle", "live tv", "live sports", "sports streaming")
    return not any(token in normalized for token in blocked)


def _classify(link: CatalogLink) -> tuple[str, str, bool, list[str], int]:
    context = " ".join((link.section, link.subsection, link.context, link.label)).casefold()
    adult = any(token in context for token in ("nsfw", "adult", "porn", "hentai", "rule34"))

    has_movie = any(token in context for token in ("movie", "film", "tv", "series"))
    has_anime = "anime" in context
    if adult and has_anime:
        category = "adult-animation"
    elif adult:
        category = "adult"
    elif has_movie and has_anime:
        category = "mixed"
    elif has_anime:
        category = "anime"
    elif has_movie:
        category = "movie-tv"
    else:
        category = "mixed"

    subsection = link.subsection.casefold()
    if "free w/ ads" in subsection or "free with ads" in subsection:
        kind = "official-stream"
    elif "multi-server" in subsection:
        kind = "multi-server"
    elif "dedicated-server" in subsection:
        kind = "dedicated-server"
    elif "aggregator" in subsection:
        kind = "stream-aggregator"
    elif "anime" in subsection:
        kind = "anime-stream"
    else:
        kind = "streaming-site"

    tags = []
    for token, tag in (
        ("auto-next", "auto-next"),
        ("4k", "4k"),
        ("requires sign-up", "signup-required"),
        ("3rd party host", "third-party-host"),
        ("multi-site search", "search-engine"),
    ):
        if token in context:
            tags.append(tag)
    if "🌟" in link.context:
        tags.append("fmhy-starred")

    priority = 62 + (18 if "fmhy-starred" in tags else 0)
    return kind, category, adult, tags, priority


def discover_links(html: str, feed_url: str) -> list[dict]:
    parser = CatalogParser()
    parser.feed(html)
    feed_host = urlparse(feed_url).netloc.lower().removeprefix("www.")
    discovered: dict[str, dict] = {}

    for link in parser.links:
        if not _is_relevant_section(link.section):
            continue
        absolute = urljoin(feed_url, link.href)
        parsed = urlparse(absolute)
        host = parsed.netloc.lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if host == feed_host or host in AUXILIARY_HOSTS:
            continue
        label_key = link.label.casefold().strip()
        if label_key in AUXILIARY_LABELS:
            continue

        kind, category, adult, tags, priority = _classify(link)
        source_id = "fmhy-site-" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:12]
        name = link.label
        if not name or name.isdigit() or len(name) <= 2:
            name = host
        source = {
            "id": source_id,
            "name": name[:120],
            "base_url": f"{parsed.scheme}://{parsed.netloc}/",
            "kind": kind,
            "category": category,
            "adult": adult,
            "status": "review-required",
            "priority": priority,
            "notes": "FMHY kataloğundan keşfedildi; etkinleştirmeden önce teknik ve hukuki inceleme gerekir.",
            "section": " / ".join(filter(None, (link.section, link.subsection))),
            "tags": tags,
            "discovered_from": feed_url,
        }
        existing = discovered.get(host)
        if existing is None or source["priority"] > existing["priority"]:
            discovered[host] = source
    return sorted(discovered.values(), key=lambda item: (-item["priority"], item["name"].casefold()))


def fetch_feed(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "SahneAvcisi/0.2 (+public-source-registry)"})
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read(8_000_000).decode(content_type, errors="replace")


def sync_fmhy(database: Database, feeds: list[str] | None = None) -> dict:
    feed_urls = feeds or list(DEFAULT_FEEDS)
    totals = {"discovered": 0, "created": 0, "updated": 0, "missing": 0, "restored": 0}
    errors: list[dict] = []
    runs: list[dict] = []

    for feed_url in feed_urls:
        catalog_id = _catalog_id(feed_url)
        run_id = database.start_catalog_sync(catalog_id, feed_url)
        try:
            html = fetch_feed(feed_url)
            sources = discover_links(html, feed_url)
            counts = database.apply_catalog_snapshot(run_id, catalog_id, sources)
            database.finish_catalog_sync(run_id, status="completed", counts=counts)
            for key in totals:
                totals[key] += counts[key]
            runs.append({"run_id": run_id, "feed": feed_url, **counts})
        except Exception as exc:  # one broken feed must not stop the others
            message = str(exc)[:500]
            database.finish_catalog_sync(run_id, status="failed", error=message)
            errors.append({"feed": feed_url, "error": message})
            runs.append({"run_id": run_id, "feed": feed_url, "error": message})

    return {**totals, "feeds": len(feed_urls), "errors": errors, "runs": runs}

