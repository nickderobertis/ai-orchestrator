"""Small clean export inputs, built here without upstream fixture dependencies."""

from __future__ import annotations

import gzip
import io
import struct
import zipfile
import zlib


def files() -> dict[str, bytes]:
    """Deterministic JPEG/EXIF, PNG/text, 3MF/XML and gzip content."""
    exif = b"Exif\x00\x00II\x2a\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00"

    def segment(marker: int, payload: bytes) -> bytes:
        return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload

    # A baseline 1x1 grayscale JPEG: one quantization table and single-symbol Huffman
    # tables encode a zero DC difference and end-of-block, followed by one-fill padding.
    jpeg = b"\xff\xd8" + segment(0xE1, exif)
    jpeg += segment(0xDB, b"\x00" + bytes([1] * 64))
    jpeg += segment(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
    counts = bytes([1] + [0] * 15)
    jpeg += segment(0xC4, b"\x00" + counts + b"\x00" + b"\x10" + counts + b"\x00")
    jpeg += segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x3f\xff\xd9"

    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content))
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"tEXt", b"Description\x00A clean frame")
    png += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        member = zipfile.ZipInfo("3D/3dmodel.model", (2020, 1, 1, 0, 0, 0))
        zipped.writestr(
            member,
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            '<metadata name="Title">A clean model</metadata><resources/><build/></model>',
        )
    return {
        "frame.jpg": jpeg,
        "frame.png": png,
        "model.3mf": archive.getvalue(),
        "print.gcode.gz": gzip.compress(b"G28 ; home the axes\n", mtime=0),
    }
