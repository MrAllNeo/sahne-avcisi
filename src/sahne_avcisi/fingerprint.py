from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    """Raised when uploaded bytes cannot be decoded as an image."""


@dataclass(frozen=True)
class Fingerprint:
    dhash: str
    ahash: str
    width: int
    height: int


def _open_image(data: bytes) -> Image.Image:
    if not data:
        raise InvalidImageError("Görsel verisi boş.")
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("Desteklenmeyen veya bozuk görsel.") from exc
    return ImageOps.exif_transpose(image).convert("RGB")


def _trim_near_black_borders(image: Image.Image, threshold: int = 12) -> Image.Image:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    cropped = image.crop(bbox)
    if cropped.width < image.width * 0.4 or cropped.height < image.height * 0.4:
        return image
    return cropped


def _flattened_pixels(image: Image.Image) -> list[int]:
    # Pillow 11.0 does not have get_flattened_data(); it was added later.
    get_flattened_data = getattr(image, "get_flattened_data", None)
    if get_flattened_data is not None:
        return list(get_flattened_data())
    return list(image.getdata())


def _difference_hash(image: Image.Image, size: int = 8) -> int:
    resized = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = _flattened_pixels(resized)
    result = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            result <<= 1
            result |= pixels[offset + column] > pixels[offset + column + 1]
    return result


def _average_hash(image: Image.Image, size: int = 8) -> int:
    resized = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = _flattened_pixels(resized)
    mean = sum(pixels) / len(pixels)
    result = 0
    for value in pixels:
        result <<= 1
        result |= value >= mean
    return result


def fingerprint_bytes(data: bytes) -> Fingerprint:
    image = _open_image(data)
    width, height = image.size
    normalized = _trim_near_black_borders(image)
    return Fingerprint(
        dhash=f"{_difference_hash(normalized):016x}",
        ahash=f"{_average_hash(normalized):016x}",
        width=width,
        height=height,
    )


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def similarity(query: Fingerprint, candidate_dhash: str, candidate_ahash: str) -> float:
    distance = hamming_distance(query.dhash, candidate_dhash) + hamming_distance(query.ahash, candidate_ahash)
    return max(0.0, 1.0 - distance / 128.0)
