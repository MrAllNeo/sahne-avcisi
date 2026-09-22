"""Internet Archive adapter.

The Archive hosts a large, openly licensed film collection and documents a
public metadata API, so items can be resolved to a direct video file without
scraping a player page. Only items carrying an explicit public-domain or
Creative Commons licence are accepted for indexing.
"""

from __future__ import annotations

import json
from urllib.parse import quote, unquote, urlparse

METADATA_ENDPOINT = "https://archive.org/metadata/{identifier}"
DOWNLOAD_ENDPOINT = "https://archive.org/download/{identifier}/{filename}"
SEARCH_ENDPOINT = "https://archive.org/advancedsearch.php"

INDEXABLE_MEDIATYPES = {"movies"}

# Formats the Archive derives for video items, best first. Anything outside
# this list (torrents, metadata sidecars, thumbnails) is ignored.
PREFERRED_FORMATS = (
    "h.264",
    "mpeg4",
    "h.264 ia",
    "matroska",
    "ogg video",
    "512kb mpeg4",
)
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mkv", ".ogv", ".webm"}

LICENCE_MARKERS = ("creativecommons.org", "publicdomain", "public domain", "cc0")


class ArchiveOrgError(RuntimeError):
    pass


class LicenceNotClearError(ArchiveOrgError):
    """The item does not advertise a licence that permits indexing."""


def parse_identifier(page_url: str) -> str | None:
    """Extract the Archive item identifier from a details or download URL."""
    parsed = urlparse(page_url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "archive.org" and not host.endswith(".archive.org"):
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"details", "download", "embed", "stream"}:
        identifier = parts[1].strip()
        # Identifiers are path segments; reject anything that could traverse.
        if identifier and "/" not in identifier and identifier not in {".", ".."}:
            return identifier
    return None


def _as_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)


def licence_is_clear(metadata: dict) -> bool:
    """True when the item states a public-domain or Creative Commons licence.

    Many genuinely old films on the Archive carry no licence field at all. We
    refuse those rather than guess: a missing licence is not a permission.
    """
    haystack = " ".join(
        _as_text(metadata.get(key)) for key in ("licenseurl", "license", "rights")
    ).casefold()
    return any(marker in haystack for marker in LICENCE_MARKERS)


def _file_sort_key(entry: dict) -> tuple:
    fmt = _as_text(entry.get("format")).casefold()
    rank = PREFERRED_FORMATS.index(fmt) if fmt in PREFERRED_FORMATS else len(PREFERRED_FORMATS)
    try:
        size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    # Prefer a known-good format, then the largest rendition of it.
    return (rank, -size)


def _is_video_file(entry: dict) -> bool:
    name = _as_text(entry.get("name"))
    if not name or name.startswith("_") or "/" in name:
        return False
    fmt = _as_text(entry.get("format")).casefold()
    if fmt in PREFERRED_FORMATS:
        return True
    return any(name.casefold().endswith(extension) for extension in VIDEO_EXTENSIONS)


def select_video_file(files: list[dict], *, max_bytes: int | None = None) -> dict | None:
    """Pick the best indexable video rendition, honouring a size ceiling."""
    candidates = [entry for entry in files if _is_video_file(entry)]
    if max_bytes is not None:
        sized = []
        for entry in candidates:
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            # Keep entries with an unknown size: the download guard re-checks.
            if size == 0 or size <= max_bytes:
                sized.append(entry)
        candidates = sized
    if not candidates:
        return None
    return sorted(candidates, key=_file_sort_key)[0]


def download_url(identifier: str, filename: str) -> str:
    return DOWNLOAD_ENDPOINT.format(
        identifier=quote(identifier, safe=""), filename=quote(filename, safe="")
    )


def metadata_url(identifier: str) -> str:
    return METADATA_ENDPOINT.format(identifier=quote(identifier, safe=""))


def duration_seconds(entry: dict) -> float | None:
    raw = _as_text(entry.get("length")).strip()
    if not raw:
        return None
    try:
        if ":" in raw:
            total = 0.0
            for part in raw.split(":"):
                total = total * 60 + float(part)
            return total
        return float(raw)
    except ValueError:
        return None


def parse_metadata(payload: str) -> dict:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ArchiveOrgError("Archive.org geçerli JSON döndürmedi.") from exc
    if not isinstance(document, dict) or not document.get("metadata"):
        raise ArchiveOrgError("Archive.org öğesi bulunamadı.")
    return document


def plan_item(document: dict, *, max_bytes: int | None = None) -> dict:
    """Turn a metadata document into an indexing plan, or explain the refusal."""
    metadata = document.get("metadata") or {}
    mediatype = _as_text(metadata.get("mediatype")).casefold()
    if mediatype not in INDEXABLE_MEDIATYPES:
        raise ArchiveOrgError(f"Öğe video değil (mediatype={mediatype or 'bilinmiyor'}).")
    if not licence_is_clear(metadata):
        raise LicenceNotClearError(
            "Öğe kamu malı veya Creative Commons lisansı belirtmiyor; indekslenmedi."
        )
    entry = select_video_file(document.get("files") or [], max_bytes=max_bytes)
    if entry is None:
        raise ArchiveOrgError("Öğede uygun boyutta indekslenebilir video dosyası yok.")

    identifier = _as_text(metadata.get("identifier")).strip()
    if not identifier:
        raise ArchiveOrgError("Öğe kimliği okunamadı.")
    return {
        "identifier": identifier,
        "title": _as_text(metadata.get("title")).strip() or identifier,
        "media_url": download_url(identifier, _as_text(entry.get("name"))),
        "filename": _as_text(entry.get("name")),
        "format": _as_text(entry.get("format")),
        "licence": _as_text(metadata.get("licenseurl")) or _as_text(metadata.get("rights")),
        "duration_seconds": duration_seconds(entry),
    }


def search_url(query: str, *, rows: int, page: int = 1) -> str:
    from urllib.parse import urlencode

    params = [
        ("q", query),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "licenseurl"),
        ("rows", str(rows)),
        ("page", str(page)),
        ("output", "json"),
    ]
    return f"{SEARCH_ENDPOINT}?{urlencode(params)}"


def parse_search_results(payload: str) -> list[dict]:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ArchiveOrgError("Archive.org araması geçerli JSON döndürmedi.") from exc
    docs = (document.get("response") or {}).get("docs")
    if not isinstance(docs, list):
        raise ArchiveOrgError("Archive.org araması beklenen biçimde yanıt vermedi.")
    results = []
    for item in docs:
        identifier = _as_text(item.get("identifier")).strip()
        if identifier:
            results.append(
                {
                    "identifier": identifier,
                    "title": _as_text(item.get("title")).strip() or identifier,
                    "licenseurl": _as_text(item.get("licenseurl")).strip(),
                }
            )
    return results
