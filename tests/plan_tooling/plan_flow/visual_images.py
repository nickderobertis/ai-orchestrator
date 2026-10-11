"""Screenshot-sized PNGs for the visual-plan journeys, generated per test and never committed.

A visual plan's spike report and design document carry before-and-after screenshots as
plan-store assets, and what a journey proves about carrying them is worth only as much as
the images are real-sized: a few hundred bytes would pass through a store that chokes on
a screenshot. So each image here is seeded pseudo-random pixels, which compression cannot
shrink, sized into the range a real screenshot spans, and distinct per seed.
"""

from __future__ import annotations

import random
import struct
import zlib
from collections.abc import Mapping
from pathlib import Path

#: The range a real screenshot's encoded size spans, in bytes, and the band "about 500 KB"
#: names, which at least one image of each journey falls in.
SMALLEST = 50_000
LARGEST = 500_000
ABOUT_500_KB = 450_000

#: Width and height giving an encoded PNG of about 120 KB, and of about 470 KB.
TYPICAL = (200, 200)
LARGE = (400, 390)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = struct.pack(">I", zlib.crc32(kind + data))
    return struct.pack(">I", len(data)) + kind + data + crc


def noise_png(seed: int, size: tuple[int, int] = TYPICAL) -> bytes:
    """An RGB PNG of ``size`` whose every pixel is drawn from a generator seeded ``seed``."""
    width, height = size
    pixels = random.Random(seed)
    raw = b"".join(b"\x00" + pixels.randbytes(width * 3) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def write_noise_png(path: Path, seed: int, size: tuple[int, int] = TYPICAL) -> bytes:
    """Write :func:`noise_png` to ``path`` and answer its bytes."""
    image = noise_png(seed, size)
    path.write_bytes(image)
    return image


# llmlint: ignore-block[tests_hold_no_nonfunctional_thresholds] The bounds are this journey's
# acceptance criteria for its own inputs, which must be screenshot-sized for an asset to prove
# anything; they limit no product behavior.
def sized_like_screenshots(images: Mapping[str, bytes]) -> None:
    """Fail, naming each image and its size, unless every one is screenshot-sized."""
    sizes_like_screenshots({name: len(image) for name, image in images.items()})


def sizes_like_screenshots(sizes: Mapping[str, int]) -> None:
    """Fail, naming each image and its size, unless every size is a screenshot's.

    Each is 50,000 to 500,000 bytes and at least one is 450,000 to 500,000: the sizes a
    store has to carry for a visual plan's pictures to reach the person approving it.
    """
    outside = {name: size for name, size in sizes.items() if not SMALLEST <= size <= LARGEST}
    assert not outside, f"images outside {SMALLEST:,}-{LARGEST:,} bytes: {outside}"
    assert any(size >= ABOUT_500_KB for size in sizes.values()), (
        f"no image of {ABOUT_500_KB:,}-{LARGEST:,} bytes among {sizes}"
    )


# llmlint: ignore-end[tests_hold_no_nonfunctional_thresholds]


#: Bytes per pixel of each 8-bit PNG colour type: grey, RGB, palette, grey-alpha, RGBA.
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(left: int, up: int, corner: int) -> int:
    estimate = left + up - corner
    distances = (abs(estimate - left), abs(estimate - up), abs(estimate - corner))
    return (left, up, corner)[distances.index(min(distances))]


def decoded_pixels(image: bytes) -> tuple[int, int, bytes]:
    """The width, height and unfiltered scanlines of an 8-bit, non-interlaced PNG.

    What two captures *show* is compared here rather than their encodings, which two
    encoders may write differently for one picture.
    """
    assert image.startswith(PNG_SIGNATURE), "not a PNG"
    offset, header, data = len(PNG_SIGNATURE), b"", b""
    while offset < len(image):
        (length,) = struct.unpack(">I", image[offset : offset + 4])
        kind = image[offset + 4 : offset + 8]
        body = image[offset + 8 : offset + 8 + length]
        header = body if kind == b"IHDR" else header
        data += body if kind == b"IDAT" else b""
        offset += 12 + length
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", header)
    assert depth == 8 and interlace == 0, (depth, interlace)
    unit = CHANNELS[colour]
    stride = width * unit
    raw = zlib.decompress(data)
    rows: list[bytearray] = []
    previous = bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        method, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for index in range(stride):
            left = line[index - unit] if index >= unit else 0
            up = previous[index]
            corner = previous[index - unit] if index >= unit else 0
            predicted = (0, left, up, (left + up) // 2, _paeth(left, up, corner))[method]
            line[index] = (line[index] + predicted) & 0xFF
        rows.append(line)
        previous = line
    return width, height, b"".join(rows)
