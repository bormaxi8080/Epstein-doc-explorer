"""Reproducible text/scan/table/rotated/encrypted/corrupt document fixtures."""

import json
import struct
import zipfile
import zlib
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from .state import digest_file


def _pdf(path, text=None, rotation=0, scan=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    resources = DictionaryObject()
    content = DecodedStreamObject()
    if scan:
        image = DecodedStreamObject()
        image.set_data(bytes((index % 2) * 255 for index in range(32 * 32)))
        image.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(32),
                NameObject("/Height"): NumberObject(32),
                NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            }
        )
        resources[NameObject("/XObject")] = DictionaryObject(
            {NameObject("/Im0"): writer._add_object(image)}
        )
        content.set_data(b"q 100 0 0 100 20 20 cm /Im0 Do Q")
    else:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources[NameObject("/Font")] = DictionaryObject(
            {NameObject("/F1"): writer._add_object(font)}
        )
        lines = (text or "Synthetic test document").splitlines()
        commands = ["BT /F1 12 Tf 20 270 Td"]
        for index, line in enumerate(lines):
            if index:
                commands.append("0 -20 Td")
            commands.append(
                "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj"
            )
        commands.append("ET")
        content.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(content)
    if rotation:
        page.rotate(rotation)
    if encrypted:
        writer.encrypt("synthetic-public-test-password")
    with path.open("wb") as output:
        writer.write(output)


def _png(path):
    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    raster = b"".join(b"\x00" + bytes([255] * 32) for _ in range(32))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raster))
        + chunk(b"IEND", b"")
    )


def _tiff(path):
    ifd_size, pixels = 2 + 9 * 12 + 4, 64
    output = bytearray(b"II*\x00" + struct.pack("<I", 8))
    for page in range(2):
        tags = [
            (256, 4, 8),
            (257, 4, 8),
            (258, 3, 8),
            (259, 3, 1),
            (262, 3, 1),
            (273, 4, 8 + 2 * ifd_size + page * pixels),
            (277, 3, 1),
            (278, 4, 8),
            (279, 4, pixels),
        ]
        output.extend(struct.pack("<H", len(tags)))
        for tag, kind, value in tags:
            output.extend(struct.pack("<HHII", tag, kind, 1, value))
        output.extend(struct.pack("<I", 8 + ifd_size if page == 0 else 0))
    output.extend(b"\xff" * pixels + b"\x00" * pixels)
    path.write_bytes(output)


def generate_corpus(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    _pdf(root / "text.pdf", "Synthetic correspondence for offline extraction tests.")
    _pdf(root / "rotated.pdf", "Synthetic rotated page for offline extraction tests.", rotation=90)
    _pdf(root / "table.pdf", "Item        Amount\nSynthetic A     10\nSynthetic B     20")
    _pdf(root / "scan.pdf", scan=True)
    _pdf(root / "encrypted.pdf", encrypted=True)
    (root / "corrupt.pdf").write_bytes(b"%PDF-1.7\nsynthetic truncated fixture")
    _png(root / "blank.png")
    _tiff(root / "multipage.tiff")
    with zipfile.ZipFile(root / "documents.zip", "w") as archive:
        archive.write(root / "text.pdf", "documents/text.pdf")
        archive.write(root / "blank.png", "images/blank.png")
    with zipfile.ZipFile(root / "unsafe.zip", "w") as archive:
        archive.writestr("../escape.txt", "Synthetic unsafe member")
    expected = {
        "text.pdf": "extracted",
        "rotated.pdf": "extracted",
        "table.pdf": "extracted",
        "scan.pdf": "needs_ocr",
        "encrypted.pdf": "encrypted",
        "corrupt.pdf": "invalid",
        "multipage.tiff": "two_image_pages",
        "blank.png": "no_text_image",
        "documents.zip": "zip_crc",
        "unsafe.zip": "unsafe_archive",
    }
    manifest = {
        "format_version": 1,
        "synthetic": True,
        "purpose": "routing and robustness; not OCR accuracy",
        "files": [
            {"path": name, "sha256": digest_file(root / name), "expected": status}
            for name, status in expected.items()
        ],
    }
    (root / "corpus.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
