from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urljoin, urlparse

from .adapters import AdapterError, PublicHttpClient, UnsafeUrlError, validate_public_https_url


MEDIA_EXTENSIONS = {".ts", ".m4s", ".mp4", ".m4v", ".cmfv", ".cmfa", ".aac"}
ATTRIBUTE_PATTERN = re.compile(r"([A-Z0-9-]+)=(\"[^\"]*\"|[^,]*)", re.IGNORECASE)
URI_ATTRIBUTE_PATTERN = re.compile(r'URI="([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class HlsMirrorResult:
    manifest_path: Path
    manifest_url: str
    segment_count: int
    total_bytes: int
    duration_seconds: float


class HlsMirror:
    def __init__(
        self,
        client: PublicHttpClient,
        *,
        max_segments: int = 20_000,
        max_duration_seconds: float = 4 * 60 * 60,
        target_height: int = 480,
    ) -> None:
        self.client = client
        self.max_segments = max_segments
        self.max_duration_seconds = max_duration_seconds
        self.target_height = target_height

    def mirror(
        self,
        manifest_url: str,
        output_dir: Path,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> HlsMirrorResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        master_text, master_final = self._fetch_playlist(manifest_url, headers)
        self._require_allowed_host(manifest_url, master_final)
        selected_url = master_final
        media_text = master_text

        variants = self._parse_variants(master_text, master_final)
        if variants:
            selected_url = self._select_variant(variants)["url"]
            self._require_allowed_host(master_final, selected_url)
            media_text, media_final = self._fetch_playlist(selected_url, headers)
            self._require_allowed_host(master_final, media_final)
            selected_url = media_final

        lines, resources, duration = self._parse_media_playlist(media_text, selected_url)
        if len(resources) > self.max_segments:
            raise AdapterError("HLS segment sayısı güvenlik sınırını aşıyor.")
        if duration > self.max_duration_seconds:
            raise AdapterError("HLS video süresi izin verilen sınırı aşıyor.")

        local_names: dict[str, str] = {}
        total_bytes = 0
        for resource_url in resources:
            if resource_url in local_names:
                continue
            self._require_allowed_host(selected_url, resource_url)
            extension = Path(unquote(urlparse(resource_url).path)).suffix.lower()
            if extension not in MEDIA_EXTENSIONS:
                raise AdapterError(f"Desteklenmeyen HLS parça uzantısı: {extension or 'yok'}")
            local_name = f"asset-{len(local_names) + 1:06d}{extension}"
            remaining = max_bytes - total_bytes
            if remaining <= 0:
                raise AdapterError("HLS toplam indirme boyutu sınırı aşıldı.")
            download_kwargs = {
                "max_bytes": remaining,
                "allowed_content_prefixes": ("video/", "audio/"),
                "allowed_content_types": {
                    "application/octet-stream",
                    "binary/octet-stream",
                    "application/mp4",
                },
            }
            if headers:
                download_kwargs["headers"] = headers
            final_url, written = self.client.download_resource(
                resource_url,
                output_dir / local_name,
                **download_kwargs,
            )
            try:
                self._require_allowed_host(selected_url, final_url)
            except Exception:
                (output_dir / local_name).unlink(missing_ok=True)
                raise
            total_bytes += written
            local_names[resource_url] = local_name

        rewritten = self._rewrite_playlist(lines, selected_url, local_names)
        manifest_path = output_dir / "index.m3u8"
        manifest_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        return HlsMirrorResult(
            manifest_path=manifest_path,
            manifest_url=selected_url,
            segment_count=len(resources),
            total_bytes=total_bytes,
            duration_seconds=duration,
        )

    def _fetch_playlist(
        self,
        url: str,
        headers: Mapping[str, str] | None,
    ) -> tuple[str, str]:
        if headers:
            return self.client.fetch_playlist(url, headers=headers)
        return self.client.fetch_playlist(url)

    @staticmethod
    def _parse_variants(text: str, base_url: str) -> list[dict]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        variants = []
        for index, line in enumerate(lines):
            if not line.upper().startswith("#EXT-X-STREAM-INF:"):
                continue
            uri = lines[index + 1] if index + 1 < len(lines) and not lines[index + 1].startswith("#") else None
            if not uri:
                raise AdapterError("HLS ana manifestinde varyant adresi eksik.")
            attributes = _parse_attributes(line.split(":", 1)[1])
            resolution = attributes.get("RESOLUTION", "")
            width = 0
            height = 0
            if "x" in resolution.lower():
                try:
                    width_text, height_text = resolution.lower().split("x", 1)
                    width = int(width_text)
                    height = int(height_text)
                except ValueError:
                    width = 0
                    height = 0
            try:
                bandwidth = int(attributes.get("BANDWIDTH", "0") or 0)
            except ValueError:
                bandwidth = 0
            variants.append(
                {"url": urljoin(base_url, uri), "width": width, "height": height, "bandwidth": bandwidth}
            )
            if len(variants) > 100:
                raise AdapterError("HLS varyant sayısı güvenlik sınırını aşıyor.")
        return variants

    def _select_variant(self, variants: list[dict]) -> dict:
        large_enough = [variant for variant in variants if variant["height"] >= self.target_height]
        if large_enough:
            return min(large_enough, key=lambda item: (item["height"], item["bandwidth"] or 10**15))
        sized = [variant for variant in variants if variant["height"] > 0]
        if sized:
            return max(sized, key=lambda item: (item["height"], item["bandwidth"]))
        return min(variants, key=lambda item: item["bandwidth"] or 10**15)

    def _parse_media_playlist(self, text: str, base_url: str) -> tuple[list[str], list[str], float]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != "#EXTM3U":
            raise AdapterError("Geçersiz HLS manifesti.")
        if any(line.upper().startswith("#EXT-X-STREAM-INF:") for line in lines):
            raise AdapterError("İç içe HLS ana manifesti desteklenmiyor.")
        if "#EXT-X-ENDLIST" not in {line.upper() for line in lines}:
            raise AdapterError("Canlı HLS akışları indekslenmez; yalnızca tamamlanmış VOD kabul edilir.")

        resources: list[str] = []
        duration = 0.0
        for line in lines:
            upper = line.upper()
            if upper.startswith("#EXTINF:"):
                try:
                    duration += float(line.split(":", 1)[1].split(",", 1)[0])
                except ValueError as exc:
                    raise AdapterError("HLS segment süresi geçersiz.") from exc
            elif upper.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
                attributes = _parse_attributes(line.split(":", 1)[1])
                if attributes.get("METHOD", "").upper() != "NONE":
                    raise AdapterError("Şifreli HLS akışları otomatik indekslenmez.")
            elif upper.startswith(("#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:")):
                raise AdapterError("Düşük gecikmeli/canlı HLS parçaları desteklenmiyor.")
            elif upper.startswith("#EXT-X-MAP:"):
                uri = _uri_attribute(line)
                resources.append(urljoin(base_url, uri))
            elif "URI=" in upper:
                raise AdapterError("Desteklenmeyen HLS URI etiketi bulundu.")
            elif not line.startswith("#"):
                resources.append(urljoin(base_url, line))
        if not resources:
            raise AdapterError("HLS manifestinde indirilebilir parça bulunamadı.")
        return lines, resources, duration

    @staticmethod
    def _rewrite_playlist(lines: list[str], base_url: str, local_names: dict[str, str]) -> list[str]:
        rewritten = []
        for line in lines:
            if line.upper().startswith("#EXT-X-MAP:"):
                remote = urljoin(base_url, _uri_attribute(line))
                rewritten.append(URI_ATTRIBUTE_PATTERN.sub(f'URI="{local_names[remote]}"', line, count=1))
            elif not line.startswith("#"):
                rewritten.append(local_names[urljoin(base_url, line)])
            else:
                rewritten.append(line)
        return rewritten

    @staticmethod
    def _require_allowed_host(root_url: str, candidate_url: str) -> None:
        validate_public_https_url(candidate_url)
        root_host = (urlparse(root_url).hostname or "").lower().removeprefix("www.")
        candidate_host = (urlparse(candidate_url).hostname or "").lower().removeprefix("www.")
        if not root_host or not candidate_host:
            raise UnsafeUrlError("HLS alan adı geçersiz.")
        if candidate_host != root_host and not candidate_host.endswith(f".{root_host}"):
            raise UnsafeUrlError("HLS manifesti farklı bir alan adına yönlendiriyor.")


def _parse_attributes(value: str) -> dict[str, str]:
    attributes = {}
    for match in ATTRIBUTE_PATTERN.finditer(value):
        raw = match.group(2).strip()
        attributes[match.group(1).upper()] = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
    return attributes


def _uri_attribute(line: str) -> str:
    match = URI_ATTRIBUTE_PATTERN.search(line)
    if not match:
        raise AdapterError("HLS URI etiketi geçersiz.")
    return match.group(1)
