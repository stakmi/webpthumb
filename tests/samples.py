"""Tiny PNG, HEIF, and PDF bytes for tests."""

from __future__ import annotations

import io

import pillow_heif
import pymupdf
from PIL import Image


def png_bytes(size: tuple[int, int] = (800, 200), color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def heif_bytes(size: tuple[int, int] = (800, 200), color: str = "red") -> bytes:
    """A single-frame HEIF image. The same bytes are valid for a ``.heic`` name."""
    pillow_heif.register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="HEIF")
    return buffer.getvalue()


def pdf_bytes(pages: int = 1) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=400, height=200)
        page.insert_text((72, 72), f"page {index + 1}")
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()
