"""Bounded inline image validation and PDF-to-text preprocessing for Llameros inference.

Bounded inline media admission. Never fetch URLs or retain attachments.
"""

import base64
import binascii
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi import HTTPException

PDF_WORKERS = threading.BoundedSemaphore(2)


def decode_inline(value, maximum, media=None):
    """Decode bounded base64 or a matching data URI to bytes; reject remote references.

    Check encoded length before decoding and raise HTTP 400/413 for malformed or oversized
    attachments.
    """
    if not isinstance(value, str):
        raise HTTPException(400, "Inline base64 data required")
    if value.startswith("data:"):
        prefix, separator, value = value.partition(",")
        if not separator or not prefix.endswith(";base64"):
            raise HTTPException(400, "Base64 data URI required")
        if media and prefix != f"data:{media};base64":
            raise HTTPException(400, "Incorrect media type")
    if len(value) > 4 * ((maximum + 2) // 3):
        raise HTTPException(413, "Attachment exceeds configured byte limit")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, "Invalid base64 attachment") from None
    if not data or len(data) > maximum:
        raise HTTPException(413, "Empty or oversized attachment")
    return data


def image_dimensions(data):
    """Inspect raster headers without allocating decompressed pixels."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return "image/png", *struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif", *struct.unpack("<HH", data[6:10])
        if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                return (
                    "image/webp",
                    1 + int.from_bytes(data[24:27], "little"),
                    1 + int.from_bytes(data[27:30], "little"),
                )
            if data[12:16] == b"VP8L" and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                return "image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if data[12:16] == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                w, h = struct.unpack("<HH", data[26:30])
                return "image/webp", w & 0x3FFF, h & 0x3FFF
        if data.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 4 <= len(data):
                if data[offset] != 255:
                    break
                marker = data[offset + 1]
                offset += 2
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    continue
                length = int.from_bytes(data[offset : offset + 2], "big")
                if length < 2 or offset + length > len(data):
                    break
                if marker in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    h, w = struct.unpack(">HH", data[offset + 3 : offset + 7])
                    return "image/jpeg", w, h
                offset += length
    except (IndexError, struct.error):
        pass
    raise HTTPException(400, "Unsupported or malformed raster image (PNG/JPEG/GIF/WebP required)")


def validate_images(body, cfg):
    """Validate supported inline image blocks and return their count without fetching URLs.

    Enforce configured bytes, count, and header pixel limits; this is not a full raster decoder.
    """
    count = 0

    def image(value, declared=None):
        """Validate one inline image against byte, declared media-type, and header dimension limits."""
        nonlocal count
        count += 1
        if count > cfg.image_max_count:
            raise HTTPException(413, "Too many images")
        data = decode_inline(value, cfg.image_max_bytes)
        mime, width, height = image_dimensions(data)
        if declared and declared != mime:
            raise HTTPException(400, "Image media type does not match its header")
        if (
            isinstance(value, str)
            and value.startswith("data:")
            and not value.startswith(f"data:{mime};base64,")
        ):
            raise HTTPException(400, "Image media type does not match its header")
        if width < 1 or height < 1 or width * height > cfg.image_max_pixels:
            raise HTTPException(413, "Image exceeds configured pixel limit")

    def visit(node, depth=0):
        """Walk bounded content nesting and validate recognized inline image representations."""
        if depth > 64:
            raise HTTPException(400, "Content nesting too deep")
        if isinstance(node, list):
            for child in node:
                visit(child, depth + 1)
        elif isinstance(node, dict):
            if "images" in node:
                if not isinstance(node["images"], list):
                    raise HTTPException(400, "images must be an array")
                for value in node["images"]:
                    image(value)
            kind = node.get("type")
            if kind == "image_url":
                value = node.get("image_url")
                image(value.get("url") if isinstance(value, dict) else value)
            elif kind == "input_image":
                if node.get("file_id"):
                    raise HTTPException(400, "Stored image file IDs are not supported")
                image(node.get("image_url"))
            elif kind == "image":
                source = node.get("source")
                if not isinstance(source, dict) or source.get("type") != "base64":
                    raise HTTPException(400, "Only inline base64 image sources are supported")
                image(source.get("data"), source.get("media_type"))
            for name, child in node.items():
                if name not in {"images", "image_url", "source"}:
                    visit(child, depth + 1)

    visit(body)
    return count


def extract_pdf(value, cfg):
    """Return bounded UTF-8 text from an inline PDF using installed Poppler pdftotext.

    Use private temporary files, two worker slots, a 15-second deadline, and configured
    page/text ceilings. Always reap the subprocess; no OCR or sandbox is provided.
    """
    data = decode_inline(value, cfg.pdf_max_bytes, "application/pdf")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF signature required")
    executable = shutil.which("pdftotext")
    if not executable:
        raise HTTPException(503, "PDF extraction requires Poppler pdftotext")
    if not PDF_WORKERS.acquire(blocking=False):
        raise HTTPException(429, "PDF extraction capacity reached", headers={"Retry-After": "1"})
    try:
        with tempfile.TemporaryDirectory(prefix="llameros-pdf-") as directory:
            source, output = Path(directory) / "input.pdf", Path(directory) / "text.txt"
            source.write_bytes(data)
            process = subprocess.Popen(
                [
                    executable,
                    "-f",
                    "1",
                    "-l",
                    str(cfg.pdf_max_pages),
                    "-enc",
                    "UTF-8",
                    str(source),
                    str(output),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 15
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise HTTPException(422, "PDF extraction timed out")
                    if output.exists() and output.stat().st_size > cfg.pdf_max_text_bytes:
                        raise HTTPException(413, "Extracted PDF text exceeds configured limit")
                    time.sleep(0.02)
                if process.returncode != 0 or not output.exists():
                    raise HTTPException(422, "Invalid, encrypted, or unsupported PDF")
                with output.open("rb") as stream:
                    raw = stream.read(cfg.pdf_max_text_bytes + 1)
                if len(raw) > cfg.pdf_max_text_bytes:
                    raise HTTPException(413, "Extracted PDF text exceeds configured limit")
                text = raw.decode("utf-8", errors="replace")
                if not text.strip():
                    raise HTTPException(422, "PDF has no extractable text; OCR is required")
                return text
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    finally:
        PDF_WORKERS.release()


def preprocess_documents(body, cfg):
    """Convert inline PDF blocks to text; reject stored IDs and remote fetches."""
    count = 0
    total = 0

    def visit(node, depth=0):
        """Walk bounded content nesting and replace supported inline PDF blocks with text in place."""
        nonlocal count, total
        if depth > 64:
            raise HTTPException(400, "Content nesting too deep")
        if isinstance(node, list):
            for child in node:
                visit(child, depth + 1)
        elif isinstance(node, dict):
            kind = node.get("type")
            if kind in ("input_file", "document"):
                count += 1
                if count > 4:
                    raise HTTPException(413, "At most four PDFs per request")
                if kind == "input_file":
                    if node.get("file_id") or node.get("file_url"):
                        raise HTTPException(400, "Only inline PDF file_data is supported")
                    value = node.get("file_data")
                else:
                    source = node.get("source", {})
                    if (
                        not isinstance(source, dict)
                        or source.get("type") != "base64"
                        or source.get("media_type") != "application/pdf"
                    ):
                        raise HTTPException(400, "Only inline base64 PDF documents are supported")
                    value = source.get("data")
                text = extract_pdf(value, cfg)
                total += len(text.encode())
                if total > cfg.pdf_max_text_bytes:
                    raise HTTPException(413, "Combined PDF text exceeds configured limit")
                node.clear()
                node.update(type="input_text" if kind == "input_file" else "text", text=text)
            else:
                for child in node.values():
                    visit(child, depth + 1)

    visit(body)
    return count
