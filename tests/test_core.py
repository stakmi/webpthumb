"""Thumbnail bytes and files for PNG, HEIF, and PDF inputs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from image_to_webp import plugin_status, supported_extensions
from PIL import Image

from samples import heif_bytes, pdf_bytes, png_bytes
from webpthumb import ThumbError, ThumbOptions, make_thumbnail, write_thumbnail
from webpthumb.watch import WATCH_EXTENSIONS


def _webp_image(data: bytes) -> Image.Image:
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WEBP"
    return Image.open(io.BytesIO(data))


def test_png_is_shrunk_to_width(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    source.write_bytes(png_bytes((800, 200)))
    data = make_thumbnail(source, source.name, ThumbOptions(width=64))
    image = _webp_image(data)
    assert image.width <= 64
    assert image.width == 64
    assert image.height == 16


def test_png_respects_height_box(tmp_path: Path) -> None:
    source = tmp_path / "tall.png"
    source.write_bytes(png_bytes((200, 800)))
    data = make_thumbnail(source, source.name, ThumbOptions(width=256, height=40))
    image = _webp_image(data)
    assert image.width <= 256
    assert image.height <= 40


def test_small_png_is_not_upscaled(tmp_path: Path) -> None:
    source = tmp_path / "small.png"
    source.write_bytes(png_bytes((20, 10)))
    image = _webp_image(make_thumbnail(source, source.name, ThumbOptions(width=256)))
    assert image.size == (20, 10)


def test_pdf_page_is_webp(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    source.write_bytes(pdf_bytes())
    image = _webp_image(make_thumbnail(source, source.name, ThumbOptions(width=64)))
    assert image.width == 64


def test_pdf_bytes_without_suffix_are_detected() -> None:
    image = _webp_image(make_thumbnail(pdf_bytes(), "upload.bin", ThumbOptions(width=32)))
    assert image.width == 32


def test_multipage_names(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    source.write_bytes(pdf_bytes(pages=2))
    output = tmp_path / "out"
    paths = write_thumbnail(source, output, ThumbOptions(width=32, pages="all"))
    assert sorted(path.name for path in paths) == ["doc_p1.webp", "doc_p2.webp"]
    single = write_thumbnail(source, output, ThumbOptions(width=32, pages="1", overwrite=True))
    assert [path.name for path in single] == ["doc.webp"]


def test_gif_thumbnail_is_one_frame(tmp_path: Path) -> None:
    gif = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (30, 10), color) for color in ("red", "green", "blue")]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=80, loop=0)
    image = _webp_image(make_thumbnail(gif, gif.name, ThumbOptions(width=16)))
    assert getattr(image, "n_frames", 1) == 1
    assert image.width <= 16


def test_existing_output_is_skipped_unless_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "a.png"
    source.write_bytes(png_bytes((40, 40)))
    output = tmp_path / "out"
    first = write_thumbnail(source, output, ThumbOptions(width=16))
    stamp = first[0].stat().st_mtime_ns
    again = write_thumbnail(source, output, ThumbOptions(width=16))
    assert again == first
    assert first[0].stat().st_mtime_ns == stamp
    rewritten = write_thumbnail(source, output, ThumbOptions(width=16, overwrite=True))
    assert rewritten == first
    assert first[0].stat().st_mtime_ns != stamp


def test_heif_extensions_are_decodable() -> None:
    heif = {".heic", ".heics", ".heif", ".heifs", ".hif", ".avif", ".avifs"}
    assert plugin_status()["heif"] is None
    assert heif <= supported_extensions()
    assert heif <= WATCH_EXTENSIONS


def test_heif_is_shrunk_to_width(tmp_path: Path) -> None:
    source = tmp_path / "wide.heif"
    source.write_bytes(heif_bytes((800, 200)))
    image = _webp_image(make_thumbnail(source, source.name, ThumbOptions(width=64)))
    assert image.size == (64, 16)


def test_heic_bytes_are_shrunk_to_width() -> None:
    image = _webp_image(make_thumbnail(heif_bytes((800, 200)), "photo.heic", ThumbOptions(width=64)))
    assert image.size == (64, 16)


def test_text_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_bytes(b"hello")
    with pytest.raises(ThumbError, match="unsupported format"):
        make_thumbnail(source, source.name, ThumbOptions())


def test_crop_top_rejects_height() -> None:
    with pytest.raises(ValueError, match="crop_top"):
        ThumbOptions(crop_top=1.0, height=100).validate()
