from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener


TRACE_MOE_ENDPOINT = "https://api.trace.moe/search?anilistInfo&cutBorders=2"
TRACE_MOE_ORIGIN = "https://api.trace.moe/"
USER_AGENT = "SahneAvcisi/0.4 (+https://github.com/MrAllNeo/sahne-avcisi)"


class TraceMoeError(RuntimeError):
    pass


class TraceMoeClient:
    def __init__(self, *, api_key: str | None = None, timeout: float = 25.0, opener=None):  # noqa: ANN001
        self.api_key = api_key
        self.timeout = timeout
        self.opener = opener or build_opener()

    def search(
        self,
        image_bytes: bytes,
        *,
        content_type: str,
        allow_adult: bool,
        limit: int = 5,
    ) -> dict:
        if not image_bytes:
            raise TraceMoeError("trace.moe için görsel verisi boş.")
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            content_type = "application/octet-stream"
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": USER_AGENT,
        }
        if self.api_key:
            headers["x-trace-key"] = self.api_key
        request = Request(TRACE_MOE_ENDPOINT, data=image_bytes, headers=headers, method="POST")
        try:
            response = self.opener.open(request, timeout=self.timeout)
            try:
                body = response.read(2 * 1024 * 1024 + 1)
            finally:
                response.close()
        except HTTPError as exc:
            exc.close()
            if exc.code == 429:
                raise TraceMoeError("trace.moe istek sınırına ulaşıldı; biraz sonra tekrar dene.") from exc
            if exc.code == 402:
                raise TraceMoeError(
                    "trace.moe arama kotanız doldu; günlük ücretsiz kota yenilenene kadar bekleyin "
                    "ya da bir API anahtarı ekleyin."
                ) from exc
            if exc.code in {503, 504}:
                raise TraceMoeError("trace.moe şu anda yoğun veya geçici olarak kullanılamıyor.") from exc
            raise TraceMoeError(f"trace.moe HTTP {exc.code} yanıtı verdi.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TraceMoeError("trace.moe bağlantısı kurulamadı.") from exc

        if len(body) > 2 * 1024 * 1024:
            raise TraceMoeError("trace.moe yanıtı beklenen boyutu aştı.")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TraceMoeError("trace.moe geçersiz bir yanıt döndürdü.") from exc
        if not isinstance(payload, dict):
            raise TraceMoeError("trace.moe yanıt biçimi geçersiz.")
        if payload.get("error") and not payload.get("result"):
            raise TraceMoeError(str(payload["error"])[:300])

        results = self._normalize_results(
            payload.get("result", []),
            allow_adult=allow_adult,
            limit=max(1, min(int(limit), 10)),
        )
        return {
            "results": results,
            "frame_count": int(payload.get("frameCount") or 0),
            "quota": _optional_int(payload.get("quota")),
            "quota_used": _optional_int(payload.get("quotaUsed")),
        }

    @staticmethod
    def _normalize_results(raw_results: object, *, allow_adult: bool, limit: int) -> list[dict]:
        if not isinstance(raw_results, list):
            return []
        normalized: list[dict] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            anime = raw.get("anilist")
            anime_info = anime if isinstance(anime, dict) else {}
            is_adult = bool(anime_info.get("isAdult", False))
            if is_adult and not allow_adult:
                continue
            anilist_id = anime_info.get("id") if anime_info else anime
            try:
                anilist_id = int(anilist_id)
            except (TypeError, ValueError):
                anilist_id = None

            title_info = anime_info.get("title") if isinstance(anime_info.get("title"), dict) else {}
            title = (
                title_info.get("english")
                or title_info.get("romaji")
                or title_info.get("native")
                or _clean_filename(raw.get("filename"))
            )
            try:
                similarity = max(0.0, min(float(raw.get("similarity", 0.0)) * 100.0, 100.0))
                timestamp_seconds = float(raw.get("at", raw.get("from", 0.0)) or 0.0)
            except (TypeError, ValueError):
                continue

            normalized.append(
                {
                    "title": str(title or "Bilinmeyen anime"),
                    "episode": _format_episode(raw.get("episode")),
                    "category": "adult-animation" if is_adult else "anime",
                    "adult": is_adult,
                    "source_name": "trace.moe",
                    "source_url": (
                        f"https://anilist.co/anime/{anilist_id}" if anilist_id else "https://trace.moe/"
                    ),
                    "timestamp_ms": int(timestamp_seconds * 1000),
                    "timestamp": _format_timestamp(timestamp_seconds),
                    "similarity": round(similarity, 2),
                    "provider": "trace.moe",
                    "external": True,
                    "anilist_id": anilist_id,
                    "filename": str(raw.get("filename") or ""),
                    "preview_image": _safe_trace_url(raw.get("image")),
                    "preview_video": _safe_trace_url(raw.get("video")),
                }
            )
        normalized.sort(key=lambda item: item["similarity"], reverse=True)
        return normalized[:limit]


def _safe_trace_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    url = urljoin(TRACE_MOE_ORIGIN, value)
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (hostname == "trace.moe" or hostname.endswith(".trace.moe")):
        return None
    return url


def _clean_filename(value: object) -> str:
    filename = str(value or "").rsplit("/", 1)[-1]
    for suffix in (".mkv", ".mp4", ".webm", ".avi"):
        if filename.lower().endswith(suffix):
            filename = filename[: -len(suffix)]
            break
    return filename.replace("_", " ").strip()


def _format_episode(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
