import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image, ImageDraw

from sahne_avcisi.fingerprint import (
    _flattened_pixels,
    fingerprint_bytes,
    hamming_distance,
    similarity,
)


def make_image(invert: bool = False) -> bytes:
    image = Image.new("RGB", (320, 180), "black" if not invert else "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 35, 250, 145), fill="white" if not invert else "black")
    draw.ellipse((120, 55, 200, 135), fill=(255, 60, 100))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FingerprintTests(unittest.TestCase):
    def test_identical_images_match(self) -> None:
        first = fingerprint_bytes(make_image())
        second = fingerprint_bytes(make_image())
        self.assertEqual(first.dhash, second.dhash)
        self.assertEqual(first.ahash, second.ahash)
        self.assertEqual(similarity(first, second.dhash, second.ahash), 1.0)

    def test_hamming_distance(self) -> None:
        self.assertEqual(hamming_distance("0000000000000000", "0000000000000001"), 1)

    def test_different_images_reduce_similarity(self) -> None:
        first = fingerprint_bytes(make_image())
        second = fingerprint_bytes(make_image(invert=True))
        self.assertLess(similarity(first, second.dhash, second.ahash), 1.0)

    def test_flattened_pixels_uses_getdata_when_get_flattened_data_missing(self) -> None:
        image = Image.new("L", (2, 2), 10)
        with patch.object(Image.Image, "get_flattened_data", None, create=True):
            pixels = _flattened_pixels(image)
        self.assertEqual(pixels, [10, 10, 10, 10])

    def test_flattened_pixels_prefers_get_flattened_data_when_present(self) -> None:
        image = Image.new("L", (2, 2), 5)
        pixels = _flattened_pixels(image)
        self.assertEqual(pixels, list(image.getdata()))

    def test_fingerprint_bytes_works_without_get_flattened_data(self) -> None:
        with patch.object(Image.Image, "get_flattened_data", None, create=True):
            fingerprint = fingerprint_bytes(make_image())
        self.assertEqual(len(fingerprint.dhash), 16)
        self.assertEqual(len(fingerprint.ahash), 16)


if __name__ == "__main__":
    unittest.main()

