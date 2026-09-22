import unittest
from io import BytesIO

from PIL import Image, ImageDraw

from sahne_avcisi.fingerprint import (
    fingerprint_bytes,
    hamming_distance,
    similarity,
    to_signed64,
    to_unsigned64,
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
        self.assertEqual(hamming_distance(0b0000, 0b0001), 1)
        self.assertEqual(hamming_distance(0b1010, 0b0101), 4)

    def test_hashes_are_integers_within_64_bits(self) -> None:
        fingerprint = fingerprint_bytes(make_image())
        for value in (fingerprint.dhash, fingerprint.ahash):
            self.assertIsInstance(value, int)
            self.assertTrue(0 <= value < (1 << 64))

    def test_signed_roundtrip_preserves_the_full_range(self) -> None:
        for value in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
            stored = to_signed64(value)
            self.assertTrue(-(1 << 63) <= stored < (1 << 63))
            self.assertEqual(to_unsigned64(stored), value)

    def test_different_images_reduce_similarity(self) -> None:
        first = fingerprint_bytes(make_image())
        second = fingerprint_bytes(make_image(invert=True))
        self.assertLess(similarity(first, second.dhash, second.ahash), 1.0)


if __name__ == "__main__":
    unittest.main()

