from __future__ import annotations

import html as html_module
import ipaddress
import json
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import archive_org


USER_AGENT = "SahneAvcisi/0.12 (+https://github.com/MrAllNeo/sahne-avcisi)"
DIRECT_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm"}
HLS_EXTENSIONS = {".m3u8", ".m3u"}
RULE34VIDEO_PAGE_PATH = re.compile(r"^/videos?/\d+(?:/|$)")
PROFILED_PAGE_PATTERNS = {
    "pornhub": re.compile(r"^/(?:view_video\.php|video/show|embed/)", re.IGNORECASE),
    "xvideos": re.compile(r"^/(?:video\.?[0-9a-z]+|embedframe/[0-9a-z]+)", re.IGNORECASE),
    "xhamster": re.compile(
        r"^/(?:videos/[^/]+-[0-9a-z]+|movies/[0-9a-z]+/[^/]*\.html|xembed\.php)",
        re.IGNORECASE,
    ),
}
MAX_IFRAME_DEPTH = 2
MAX_IFRAME_CANDIDATES = 3
ACCESS_CONTROL_MARKERS = (
    re.compile(r"\b(?:widevine|playready|fairplay)\b", re.IGNORECASE),
    re.compile(r"(?:class|id)=[\"'][^\"']*(?:g-recaptcha|h-captcha|cf-chl-captcha)", re.IGNORECASE),
    re.compile(r"\bid=[\"']lockedPlayer", re.IGNORECASE),
    re.compile(r"\b(?:sign in to watch|login required|premium only)\b", re.IGNORECASE),
)
AGE_UNSAFE_TITLE_MARKER = re.compile(
    r"\b(?:minor|underage|preteen|teen(?:age[rd]?)?|schoolgirl|schoolboy|loli|lolita|shota|young-looking)\b"
    r"|(?<!\d)(?:[1-9]|1[0-7])\s*(?:yo|y/o|years?\s+old)\b",
    re.IGNORECASE,
)


def _profile_url_is_video(kind: str, url: str) -> bool:
    parsed = urlparse(url)
    pattern = PROFILED_PAGE_PATTERNS.get(kind)
    if not pattern or not pattern.match(parsed.path):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if kind == "pornhub" and parsed.path.lower() in {"/view_video.php", "/video/show"}:
        return bool(query.get("viewkey", [""])[-1])
    if kind == "pornhub" and parsed.path.lower().startswith("/embed/"):
        return bool(parsed.path.rstrip("/").split("/")[-1])
    if kind == "xhamster" and parsed.path.lower() == "/xembed.php":
        return bool(query.get("video", [""])[-1])
    return True


class AdapterError(RuntimeError):
    pass


class UnsafeUrlError(AdapterError):
    pass


class UnsupportedMediaError(AdapterError):
    pass


@dataclass(frozen=True)
class AdapterResult:
    adapter: str
    page_url: str
    title: str
    media_url: str | None
    player_type: str
    indexable: bool
    reason: str | None = None
    embed_url: str | None = None
    request_headers: tuple[tuple[str, str], ...] = ()
    # Known ahead of time for catalogue sources; a streamed pipe cannot be
    # probed for it the way a downloaded file can.
    duration_ms: int | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_public_https_url(
    url: str,
    *,
    resolver: Callable = socket.getaddrinfo,
) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise UnsafeUrlError("Yalnızca HTTPS adresleri kabul edilir.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrlError("URL ana makinesi geçersiz.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("URL portu geçersiz.") from exc
    if port not in {None, 443}:
        raise UnsafeUrlError("Yalnızca standart HTTPS portu kullanılabilir.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeUrlError("Yerel ağ adreslerine erişilemez.")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            answers = resolver(hostname, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeUrlError("Alan adı çözümlenemedi.") from exc
        addresses = []
        for answer in answers:
            try:
                addresses.append(ipaddress.ip_address(answer[4][0]))
            except (ValueError, IndexError):
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeUrlError("Özel, yerel veya ayrılmış IP adreslerine erişilemez.")
    return url


def source_allows_url(source: dict, page_url: str) -> bool:
    source_host = (urlparse(str(source.get("base_url", ""))).hostname or "").lower()
    page_host = (urlparse(page_url).hostname or "").lower()
    source_host = source_host.removeprefix("www.")
    page_host = page_host.removeprefix("www.")
    return bool(source_host and page_host) and (
        page_host == source_host or page_host.endswith(f".{source_host}")
    )


class PublicHttpClient:
    def __init__(self, *, timeout: float = 20.0, max_redirects: int = 5):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.opener = build_opener(_NoRedirect())

    def fetch_text(
        self,
        url: str,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        response, final_url = self._open_response(url, headers=headers)
        try:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise UnsupportedMediaError("Sayfa HTML içeriği döndürmedi.")
            self._check_content_length(response, max_bytes)
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AdapterError("Kaynak izin verilen boyut sınırını aşıyor.")
            charset = "utf-8"
            for part in content_type.split(";")[1:]:
                if "charset=" in part.lower():
                    charset = part.split("=", 1)[1].strip().strip('"') or "utf-8"
            return body.decode(charset, errors="replace"), final_url
        finally:
            response.close()

    def fetch_json(
        self,
        url: str,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        response, final_url = self._open_response(url, headers=headers)
        try:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            normalized = content_type.split(";", 1)[0].strip().lower()
            if normalized not in {"application/json", "text/json", "application/javascript"}:
                raise UnsupportedMediaError("Adres JSON döndürmedi.")
            self._check_content_length(response, max_bytes)
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AdapterError("JSON yanıtı boyut sınırını aşıyor.")
            return body.decode("utf-8", errors="replace"), final_url
        finally:
            response.close()

    def fetch_playlist(
        self,
        url: str,
        *,
        max_bytes: int = 1024 * 1024,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        response, final_url = self._open_response(url, headers=headers)
        try:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            normalized = content_type.split(";", 1)[0].strip().lower()
            allowed = {
                "application/vnd.apple.mpegurl",
                "application/x-mpegurl",
                "audio/mpegurl",
                "audio/x-mpegurl",
                "text/plain",
                "application/octet-stream",
            }
            if normalized not in allowed:
                raise UnsupportedMediaError("Adres HLS manifesti döndürmedi.")
            self._check_content_length(response, max_bytes)
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AdapterError("HLS manifesti boyut sınırını aşıyor.")
            text = body.decode("utf-8-sig", errors="strict")
            if not text.lstrip().startswith("#EXTM3U"):
                raise UnsupportedMediaError("Adres geçerli bir HLS manifesti döndürmedi.")
            return text, final_url
        except UnicodeDecodeError as exc:
            raise UnsupportedMediaError("HLS manifesti UTF-8 değil.") from exc
        finally:
            response.close()

    def download_video(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        final_url, _ = self.download_resource(
            url,
            destination,
            max_bytes=max_bytes,
            allowed_content_prefixes=("video/",),
            allowed_content_types={"application/octet-stream", "binary/octet-stream"},
            headers=headers,
        )
        return final_url

    @contextmanager
    def stream_video(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[Iterator[bytes]]:
        """Yield a validated video body in chunks, without staging it on disk.

        The caller feeds these chunks to FFmpeg's stdin. Keeping the transfer
        on our own client is what preserves the HTTPS, redirect, private-IP and
        size guards: FFmpeg never opens a socket of its own.
        """
        response, _ = self._open_response(url, headers=headers)
        try:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            normalized = content_type.split(";", 1)[0].strip().lower()
            if not normalized.startswith("video/") and normalized not in {
                "application/octet-stream",
                "binary/octet-stream",
            }:
                raise UnsupportedMediaError("Kaynak beklenen medya türünü döndürmedi.")
            self._check_content_length(response, max_bytes)

            def chunks() -> Iterator[bytes]:
                read = 0
                while chunk := response.read(1024 * 1024):
                    read += len(chunk)
                    if read > max_bytes:
                        raise AdapterError("Kaynak izin verilen boyut sınırını aşıyor.")
                    yield chunk

            yield chunks()
        finally:
            response.close()

    def download_resource(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        allowed_content_prefixes: tuple[str, ...],
        allowed_content_types: set[str],
        headers: Mapping[str, str] | None = None,
    ) -> tuple[str, int]:
        response, final_url = self._open_response(url, headers=headers)
        try:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            normalized = content_type.split(";", 1)[0].strip().lower()
            if normalized not in allowed_content_types and not any(
                normalized.startswith(prefix) for prefix in allowed_content_prefixes
            ):
                raise UnsupportedMediaError("Kaynak beklenen medya türünü döndürmedi.")
            self._check_content_length(response, max_bytes)
            written = 0
            with destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise AdapterError("Kaynak izin verilen boyut sınırını aşıyor.")
                    output.write(chunk)
            return final_url, written
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    def _open_response(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ):  # noqa: ANN202
        current = url
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,video/*;q=0.9,*/*;q=0.2",
            **self._safe_request_headers(headers),
        }
        for redirect_count in range(self.max_redirects + 1):
            validate_public_https_url(current)
            request = Request(current, headers=request_headers)
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    exc.close()
                    raise AdapterError(f"Kaynak HTTP {exc.code} yanıtı verdi.") from exc
                location = exc.headers.get("Location")
                exc.close()
                if not location or redirect_count >= self.max_redirects:
                    raise AdapterError("Yönlendirme sınırı aşıldı.") from exc
                current = urljoin(current, location)
                continue

            final_url = response.geturl()
            try:
                validate_public_https_url(final_url)
            except Exception:
                response.close()
                raise
            return response, final_url
        raise AdapterError("Kaynak çözümlenemedi.")

    @staticmethod
    def _safe_request_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if not headers:
            return {}
        safe: dict[str, str] = {}
        for name, value in headers.items():
            canonical = name.title()
            if canonical not in {"Origin", "Referer"}:
                raise AdapterError("Desteklenmeyen medya istek başlığı.")
            if "\r" in value or "\n" in value:
                raise AdapterError("Geçersiz medya istek başlığı.")
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise AdapterError("Medya istek başlığı güvenli bir HTTPS adresi değil.")
            safe[canonical] = value
        return safe

    @staticmethod
    def _check_content_length(response, max_bytes: int) -> None:  # noqa: ANN001
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise AdapterError("Kaynak izin verilen boyut sınırını aşıyor.")
            except ValueError:
                pass


@dataclass(frozen=True)
class _MediaCandidate:
    url: str
    quality: int = 0
    player_type: str = "direct"


@dataclass(frozen=True)
class _ProfileExtraction:
    candidates: tuple[_MediaCandidate, ...] = ()
    json_endpoints: tuple[str, ...] = ()
    title: str | None = None


def _quality_from_text(value: str) -> int:
    match = re.search(r"(?<!\d)(\d{3,4})\s*p?\b", value, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _decode_media_url(value: str) -> str:
    decoded = html_module.unescape(value.strip())
    replacements = {
        r"\/": "/",
        r"\u002F": "/",
        r"\u002f": "/",
        r"\u003A": ":",
        r"\u003a": ":",
        r"\u0026": "&",
        r"\x2F": "/",
        r"\x2f": "/",
        r"\x3A": ":",
        r"\x3a": ":",
    }
    for encoded, plain in replacements.items():
        decoded = decoded.replace(encoded, plain)
    return decoded


def _candidate(value: str, *, quality_hint: str = "") -> _MediaCandidate | None:
    url = _decode_media_url(value)
    if not url.startswith(("https://", "//", "/")):
        return None
    extension = _extension(url)
    if extension not in DIRECT_VIDEO_EXTENSIONS | HLS_EXTENSIONS:
        return None
    return _MediaCandidate(
        url=url,
        quality=_quality_from_text(f"{quality_hint} {url}"),
        player_type="hls" if extension in HLS_EXTENSIONS else "direct",
    )


def _walk_media_values(value, *, hint: str = "") -> list[_MediaCandidate]:  # noqa: ANN001
    candidates: list[_MediaCandidate] = []
    if isinstance(value, dict):
        for key, child in value.items():
            candidates.extend(_walk_media_values(child, hint=f"{hint} {key}"))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(_walk_media_values(child, hint=hint))
    elif isinstance(value, str):
        item = _candidate(value, quality_hint=hint)
        if item:
            candidates.append(item)
    return candidates


def _json_after_marker(html: str, marker: str) -> object | None:
    match = re.search(marker, html, flags=re.IGNORECASE)
    if not match:
        return None
    start = match.end()
    while start < len(html) and html[start] in " \t\r\n=":
        start += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(html[start:])
        return value
    except (json.JSONDecodeError, ValueError):
        return None


def _generic_script_candidates(html: str) -> list[_MediaCandidate]:
    candidates: list[_MediaCandidate] = []
    pattern = re.compile(
        r'''["'](?P<url>(?:https?:)?(?:\\?/){2}[^\s"'<>]+?\.(?:mp4|m4v|mov|webm|m3u8|m3u)(?:\?[^\s"'<>]*)?)["']''',
        re.IGNORECASE,
    )
    relative_pattern = re.compile(
        r'''["'](?P<url>/[^\s"'<>]+?\.(?:mp4|m4v|mov|webm|m3u8|m3u)(?:\?[^\s"'<>]*)?)["']''',
        re.IGNORECASE,
    )
    for match in (*pattern.finditer(html), *relative_pattern.finditer(html)):
        item = _candidate(match.group("url"))
        if item:
            candidates.append(item)
    return candidates


def _profile_extraction(kind: str, html: str) -> _ProfileExtraction:
    candidates: list[_MediaCandidate] = []
    endpoints: list[str] = []
    title: str | None = None

    if kind == "xvideos":
        title_match = re.search(
            r'''setVideoTitle\s*\(\s*(["'])(?P<title>(?:(?!\1).)+)\1''',
            html,
            flags=re.IGNORECASE,
        )
        title = html_module.unescape(title_match.group("title")) if title_match else None
        for match in re.finditer(
            r'''setVideo(?P<kind>[^ (]+)\s*\(\s*(["'])(?P<url>https?.+?)\2\s*\)''',
            html,
            flags=re.IGNORECASE,
        ):
            label = match.group("kind")
            item = _candidate(match.group("url"), quality_hint=label)
            if not item:
                continue
            priority = {"urlhigh": 1000, "hls": 900, "urllow": 100}.get(label.lower(), 0)
            candidates.append(_MediaCandidate(item.url, max(item.quality, priority), item.player_type))

    elif kind == "pornhub":
        flashvars = _json_after_marker(html, r"var\s+flashvars_\d+")
        if isinstance(flashvars, dict):
            raw_title = flashvars.get("video_title")
            title = html_module.unescape(raw_title) if isinstance(raw_title, str) else None
            definitions = flashvars.get("mediaDefinitions")
            if isinstance(definitions, list):
                for definition in definitions:
                    if not isinstance(definition, dict):
                        continue
                    raw_url = definition.get("videoUrl")
                    if not isinstance(raw_url, str):
                        continue
                    item = _candidate(raw_url, quality_hint=str(definition.get("quality", "")))
                    if item:
                        candidates.append(item)
                    elif raw_url.startswith("https://"):
                        endpoints.append(raw_url)
        for match in re.finditer(
            r'''<a[^>]+\bclass=["'][^"']*\bdownloadBtn\b[^"']*["'][^>]+\bhref=(["'])(?P<url>(?:(?!\1).)+)\1''',
            html,
            flags=re.IGNORECASE,
        ):
            item = _candidate(match.group("url"))
            if item:
                candidates.append(item)

    elif kind == "xhamster":
        initials = _json_after_marker(html, r"window\.initials")
        if isinstance(initials, dict):
            video = initials.get("videoModel")
            if isinstance(video, dict) and isinstance(video.get("title"), str):
                title = html_module.unescape(video["title"])
            candidates.extend(_walk_media_values(initials))

    return _ProfileExtraction(tuple(candidates), tuple(endpoints), title)


def _page_has_access_control(html: str) -> bool:
    return any(pattern.search(html) for pattern in ACCESS_CONTROL_MARKERS)


def _age_unsafe_title(title: str) -> bool:
    return bool(AGE_UNSAFE_TITLE_MARKER.search(title))


def _request_headers_for_page(kind: str, page_url: str) -> tuple[tuple[str, str], ...]:
    parsed = urlparse(page_url)
    origin = f"https://{parsed.netloc}"
    if kind == "pornhub":
        return (("Origin", origin), ("Referer", origin + "/"))
    return (("Referer", page_url),)


class _MediaPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._inside_title = False
        self._title_parts: list[str] = []
        self.media_candidates: list[str] = []
        self.embed_candidates: list[str] = []
        self.base_url: str | None = None
        self.player_hints: set[str] = set()
        self._inside_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self._inside_title = True
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._inside_json_ld = True
            self._json_ld_parts = []
        elif tag == "base" and values.get("href"):
            self.base_url = values["href"]
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key in {"og:title", "twitter:title"} and content:
                self.title = content
            if key in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"} and content:
                self.media_candidates.append(content)
        elif tag in {"video", "source"} and values.get("src"):
            self.media_candidates.append(values["src"])
            self.player_hints.add("html5")
        elif tag == "iframe" and values.get("src"):
            self.embed_candidates.append(values["src"])
            self.player_hints.add("iframe")

        combined = " ".join(values.values()).lower()
        for hint in ("videojs", "jwplayer", "plyr", "hls.js"):
            if hint in combined:
                self.player_hints.add(hint)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._inside_title = False
            if not self.title:
                self.title = " ".join(self._title_parts).strip()
        elif tag == "script" and self._inside_json_ld:
            self._inside_json_ld = False
            try:
                document = json.loads("".join(self._json_ld_parts))
            except (json.JSONDecodeError, ValueError):
                return
            self.media_candidates.extend(item.url for item in _walk_media_values(document))

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_parts.append(data.strip())
        if self._inside_json_ld:
            self._json_ld_parts.append(data)


class _Rule34VideoParser(_MediaPageParser):
    """Collect public download links exposed directly by a video page."""

    def __init__(self) -> None:
        super().__init__()
        self.download_candidates: list[tuple[int, str]] = []
        self._download_href: str | None = None
        self._download_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if tag.lower() != "a":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        href = values.get("href", "").strip()
        download_values = {
            value.lower()
            for value in parse_qs(urlparse(href).query, keep_blank_values=True).get("download", [])
        }
        if "true" in download_values:
            self._download_href = href
            self._download_text = []

    def handle_data(self, data: str) -> None:
        super().handle_data(data)
        if self._download_href is not None:
            self._download_text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        super().handle_endtag(tag)
        if tag.lower() != "a" or self._download_href is None:
            return
        label = " ".join(part for part in self._download_text if part)
        quality_match = re.search(r"(?<!\d)(\d{3,4})\s*p\b", label, flags=re.IGNORECASE)
        quality = int(quality_match.group(1)) if quality_match else 0
        self.download_candidates.append((quality, self._download_href))
        self._download_href = None
        self._download_text = []


def _extension(url: str) -> str:
    return Path(unquote(urlparse(url).path)).suffix.lower()


def _title_from_url(url: str) -> str:
    name = Path(unquote(urlparse(url).path)).stem.replace("-", " ").replace("_", " ").strip()
    return name or "İsimsiz video"


class AdapterRegistry:
    def __init__(
        self,
        client: PublicHttpClient | None = None,
        *,
        max_item_bytes: int = 1024 * 1024 * 1024,
    ):
        self.client = client or PublicHttpClient()
        self.max_item_bytes = max_item_bytes

    def resolve(self, source: dict, page_url: str) -> AdapterResult:
        if source.get("status") != "active":
            raise AdapterError("Kaynak yönetici tarafından etkinleştirilmemiş.")
        if source.get("kind") in {"catalog", "metadata", "api"}:
            raise AdapterError("Bu kaynak video indeksleme adaptörü değildir.")
        if not source_allows_url(source, page_url):
            raise UnsafeUrlError("Video adresi seçilen kaynağın alan adına ait değil.")
        validate_public_https_url(page_url)

        if source.get("kind") == "archive-org":
            return self._resolve_archive_item(page_url)
        if source.get("kind") == "rule34video":
            return self._resolve_rule34video(source, page_url)
        if source.get("kind") in PROFILED_PAGE_PATTERNS:
            return self._resolve_profiled_page(source, page_url)

        extension = _extension(page_url)
        if extension in DIRECT_VIDEO_EXTENSIONS:
            return AdapterResult(
                adapter="direct-video",
                page_url=page_url,
                title=_title_from_url(page_url),
                media_url=page_url,
                player_type="direct",
                indexable=True,
            )
        if extension in HLS_EXTENSIONS:
            return AdapterResult(
                adapter="direct-hls",
                page_url=page_url,
                title=_title_from_url(page_url),
                media_url=page_url,
                player_type="hls",
                indexable=True,
            )
        return self._resolve_html_page(source, page_url)

    def _resolve_profiled_page(self, source: dict, page_url: str) -> AdapterResult:
        kind = str(source.get("kind"))
        if not _profile_url_is_video(kind, page_url):
            raise UnsupportedMediaError(f"Adres geçerli bir {source.get('name', kind)} video sayfası değil.")
        result = self._resolve_html_page(source, page_url, adapter_name=f"{kind}-public-page")
        return result

    def _resolve_rule34video(self, source: dict, page_url: str) -> AdapterResult:
        if not RULE34VIDEO_PAGE_PATH.match(urlparse(page_url).path):
            raise UnsupportedMediaError("Adres geçerli bir Rule34Video video sayfası değil.")

        html, final_url = self.client.fetch_text(page_url)
        if not source_allows_url(source, final_url):
            raise UnsafeUrlError("Kaynak sayfası izinli alan adının dışına yönlendirdi.")
        if not RULE34VIDEO_PAGE_PATH.match(urlparse(final_url).path):
            raise UnsupportedMediaError("Kaynak geçerli bir video sayfasına yönlendirmedi.")

        parser = _Rule34VideoParser()
        parser.feed(html)
        title = parser.title or _title_from_url(final_url)
        if _age_unsafe_title(title):
            return AdapterResult(
                adapter="rule34video-public-download",
                page_url=final_url,
                title=title,
                media_url=None,
                player_type=self._player_type(parser, "unknown"),
                indexable=False,
                reason="Başlık yaş güvenliği filtresine takıldı; içerik indekslenmez.",
            )
        if _page_has_access_control(html):
            return AdapterResult(
                adapter="rule34video-public-download",
                page_url=final_url,
                title=title,
                media_url=None,
                player_type=self._player_type(parser, "unknown"),
                indexable=False,
                reason="Sayfa CAPTCHA, DRM veya oturum erişim kontrolü istiyor; otomatik çözülmez.",
            )
        if not parser.download_candidates:
            return AdapterResult(
                adapter="rule34video-public-download",
                page_url=final_url,
                title=title,
                media_url=None,
                player_type=self._player_type(parser, "unknown"),
                indexable=False,
                reason=(
                    "Herkese açık doğrudan indirme bağlantısı bulunamadı; "
                    "CAPTCHA, DRM veya oturum gerektiren akışlar desteklenmez."
                ),
            )

        _, candidate = max(parser.download_candidates, key=lambda item: item[0])
        media_url = urljoin(final_url, candidate)
        validate_public_https_url(media_url)
        return AdapterResult(
            adapter="rule34video-public-download",
            page_url=final_url,
            title=title,
            media_url=media_url,
            player_type="direct",
            indexable=True,
        )

    def _resolve_archive_item(self, page_url: str) -> AdapterResult:
        identifier = archive_org.parse_identifier(page_url)
        if identifier is None:
            raise UnsupportedMediaError("Adres bir Archive.org öğesi değil.")
        payload, _ = self.client.fetch_json(archive_org.metadata_url(identifier))
        document = archive_org.parse_metadata(payload)
        try:
            plan = archive_org.plan_item(document, max_bytes=self.max_item_bytes)
        except archive_org.ArchiveOrgError as exc:
            # A refusal is a permanent property of the item, not a transport
            # failure, so report it as non-indexable rather than raising.
            return AdapterResult(
                adapter="archive-org",
                page_url=page_url,
                title=identifier,
                media_url=None,
                player_type="direct",
                indexable=False,
                reason=str(exc),
            )
        validate_public_https_url(plan["media_url"])
        duration = plan.get("duration_seconds")
        return AdapterResult(
            adapter="archive-org",
            page_url=page_url,
            title=plan["title"],
            media_url=plan["media_url"],
            player_type="direct",
            indexable=True,
            duration_ms=int(duration * 1000) if duration else None,
        )

    def _resolve_html_page(
        self,
        source: dict,
        page_url: str,
        *,
        adapter_name: str | None = None,
        root_page_url: str | None = None,
        depth: int = 0,
        seen: set[str] | None = None,
    ) -> AdapterResult:
        root_page_url = root_page_url or page_url
        seen = seen if seen is not None else set()
        if page_url in seen:
            raise UnsupportedMediaError("Döngüsel iframe zinciri reddedildi.")
        seen.add(page_url)

        html, final_url = self.client.fetch_text(page_url)
        if not source_allows_url(source, final_url):
            raise UnsafeUrlError("Kaynak sayfası izinli alan adının dışına yönlendirdi.")
        parser = _MediaPageParser()
        parser.feed(html)
        base_url = urljoin(final_url, parser.base_url) if parser.base_url else final_url
        title = parser.title or _title_from_url(final_url)

        if _page_has_access_control(html):
            return AdapterResult(
                adapter=adapter_name or "html-page",
                page_url=root_page_url,
                title=title,
                media_url=None,
                player_type=self._player_type(parser, "unknown"),
                indexable=False,
                reason="Sayfa CAPTCHA, DRM veya oturum erişim kontrolü istiyor; otomatik çözülmez.",
                embed_url=final_url if depth else None,
            )

        kind = str(source.get("kind", "video-site"))
        profile = _profile_extraction(kind, html)
        if profile.title:
            title = profile.title
        if bool(source.get("adult")) and _age_unsafe_title(title):
            return AdapterResult(
                adapter=adapter_name or "html-page",
                page_url=root_page_url,
                title=title,
                media_url=None,
                player_type=self._player_type(parser, "unknown"),
                indexable=False,
                reason="Başlık yaş güvenliği filtresine takıldı; içerik indekslenmez.",
                embed_url=final_url if depth else None,
            )
        candidates = list(profile.candidates)
        candidates.extend(_MediaCandidate(url) for url in parser.media_candidates)
        candidates.extend(_generic_script_candidates(html))

        request_headers = _request_headers_for_page(kind, final_url)
        for endpoint in profile.json_endpoints[:3]:
            endpoint_url = urljoin(base_url, endpoint)
            try:
                validate_public_https_url(endpoint_url)
                payload, endpoint_final = self.client.fetch_json(
                    endpoint_url,
                    headers=dict(request_headers),
                )
                if not source_allows_url({"base_url": endpoint_url}, endpoint_final):
                    continue
                document = json.loads(payload)
            except (AdapterError, json.JSONDecodeError, ValueError):
                continue
            candidates.extend(_walk_media_values(document))

        resolved_candidates: list[_MediaCandidate] = []
        known: set[str] = set()
        for item in candidates:
            media_url = urljoin(base_url, item.url)
            if media_url in known:
                continue
            try:
                validate_public_https_url(media_url)
            except AdapterError:
                continue
            extension = _extension(media_url)
            if extension not in DIRECT_VIDEO_EXTENSIONS | HLS_EXTENSIONS:
                continue
            known.add(media_url)
            resolved_candidates.append(
                _MediaCandidate(
                    media_url,
                    item.quality,
                    "hls" if extension in HLS_EXTENSIONS else item.player_type,
                )
            )

        if resolved_candidates:
            selected = max(
                resolved_candidates,
                key=lambda item: (item.quality, item.player_type != "hls"),
            )
            specific_adapter = adapter_name or (
                "html5-hls" if selected.player_type == "hls" else "html5-media"
            )
            return AdapterResult(
                adapter=specific_adapter,
                page_url=root_page_url,
                title=title,
                media_url=selected.url,
                player_type=(
                    "hls"
                    if selected.player_type == "hls"
                    else self._player_type(parser, selected.player_type)
                ),
                indexable=True,
                embed_url=final_url if depth else None,
                request_headers=request_headers,
            )

        first_embed: str | None = None
        blocked_child: AdapterResult | None = None
        if depth < MAX_IFRAME_DEPTH:
            for raw_embed in parser.embed_candidates[:MAX_IFRAME_CANDIDATES]:
                embed_url = urljoin(base_url, raw_embed)
                try:
                    validate_public_https_url(embed_url)
                except AdapterError:
                    continue
                first_embed = first_embed or embed_url
                embed_source = {
                    "base_url": embed_url,
                    "kind": "video-site",
                    "name": "iframe",
                    "adult": bool(source.get("adult")),
                }
                try:
                    child = self._resolve_html_page(
                        embed_source,
                        embed_url,
                        adapter_name="iframe-public-media",
                        root_page_url=root_page_url,
                        depth=depth + 1,
                        seen=seen,
                    )
                except AdapterError:
                    continue
                if child.indexable:
                    return AdapterResult(
                        adapter=child.adapter,
                        page_url=root_page_url,
                        title=parser.title or child.title,
                        media_url=child.media_url,
                        player_type=child.player_type,
                        indexable=True,
                        embed_url=embed_url,
                        request_headers=child.request_headers,
                    )
                if child.reason and any(
                    term in child.reason for term in ("CAPTCHA", "DRM", "oturum", "yaş güvenliği")
                ):
                    blocked_child = child

        if blocked_child:
            return AdapterResult(
                adapter=adapter_name or "html-page",
                page_url=root_page_url,
                title=title,
                media_url=None,
                player_type="iframe",
                indexable=False,
                reason=blocked_child.reason,
                embed_url=blocked_child.embed_url or first_embed,
            )
        return AdapterResult(
            adapter=adapter_name or "html-page",
            page_url=root_page_url,
            title=title,
            media_url=None,
            player_type=self._player_type(parser, "unknown"),
            indexable=False,
            reason=(
                "Herkese açık MP4/HLS bulunamadı; CAPTCHA, DRM, oturum veya ödeme duvarı "
                "gerektiren akışlar desteklenmez."
            ),
            embed_url=first_embed,
        )

    @staticmethod
    def _player_type(parser: _MediaPageParser, fallback: str) -> str:
        for player in ("jwplayer", "videojs", "plyr", "hls.js", "iframe", "html5"):
            if player in parser.player_hints:
                return player
        return fallback
