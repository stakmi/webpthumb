"""Dispatch PDFs to pdfthumb and images to image-to-webp."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps
from image_to_webp import ImageToWebPError, convert_image, open_image, supported_extensions
from pdfthumb import PDFThumbError, parse_pages, thumbnail_bytes

from .options import ThumbOptions

# Tall enough that, with no --height, Pillow's thumbnail box only limits width.
_NO_HEIGHT_CAP = 1_000_000_000


class ThumbError(Exception):
    """A file could not be turned into a thumbnail."""


@dataclass
class ThumbResult:
    source: str
    status: str  # "ok" | "skipped" | "error"
    output: Path | None = None
    error: str | None = None


def is_image_name(filename: str) -> bool:
    """True when image-to-webp can read this extension in the current install."""
    return Path(filename).suffix.lower() in supported_extensions()


def looks_like_pdf(source: bytes | Path, filename: str) -> bool:
    """True for a ``.pdf`` name or a ``%PDF-`` header."""
    if Path(filename).suffix.lower() == ".pdf":
        return True
    if isinstance(source, Path):
        try:
            with source.open("rb") as handle:
                head = handle.read(5)
        except OSError:
            return False
    else:
        head = bytes(source[:5])
    return head.startswith(b"%PDF-")


def _max_size(options: ThumbOptions) -> tuple[int, int]:
    height = options.height if options.height is not None else _NO_HEIGHT_CAP
    return (options.width, height)


def _still_frame(source: bytes | Path, filename: str) -> Image.Image:
    """Open one frame. image-to-webp would otherwise keep a GIF or APNG animated."""
    hint = None if isinstance(source, Path) else filename
    image = open_image(source, filename=hint)
    try:
        image.seek(0)
        oriented = ImageOps.exif_transpose(image) or image
        if oriented.mode not in ("RGB", "RGBA"):
            has_alpha = (
                oriented.mode in ("LA", "PA", "La", "RGBa")
                or (oriented.mode == "P" and "transparency" in oriented.info)
                or "A" in oriented.getbands()
            )
            oriented = oriented.convert("RGBA" if has_alpha else "RGB")
        return Image.frombytes(oriented.mode, oriented.size, oriented.tobytes())
    finally:
        image.close()


def make_thumbnail(
    source: bytes | Path,
    filename: str,
    options: ThumbOptions,
    *,
    page: int = 1,
) -> bytes:
    """Return WebP bytes for one PDF page or the first frame of an image.

    Images are only shrunk, into ``width`` by ``height`` (or ``width`` alone).
    PDFs are rendered at the requested width by pdfthumb.
    """
    options.validate()
    if page < 1:
        raise ThumbError(f"page {page} is out of range")
    if looks_like_pdf(source, filename):
        try:
            return thumbnail_bytes(
                source,
                page=page,
                width=options.width,
                height=options.height,
                crop_top=options.crop_top,
                quality=options.quality,
            )
        except PDFThumbError as exc:
            raise ThumbError(str(exc)) from exc
        except ValueError as exc:
            raise ThumbError(str(exc)) from exc

    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix and not is_image_name(filename):
        raise ThumbError(f"unsupported format: {filename}")
    if isinstance(source, Path) and not is_image_name(source.name):
        raise ThumbError(f"unsupported format: {source.name}")
    try:
        still = _still_frame(source, filename)
        return convert_image(still, quality=options.quality, max_size=_max_size(options))
    except ImageToWebPError as exc:
        raise ThumbError(str(exc)) from exc
    except ValueError as exc:
        raise ThumbError(str(exc)) from exc


def pdf_page_numbers(source: bytes | Path, options: ThumbOptions) -> list[int]:
    """Return the 1-based pages selected by ``options.pages``."""
    try:
        if isinstance(source, Path):
            document = pymupdf.open(source)
        else:
            document = pymupdf.open(stream=source, filetype="pdf")
    except Exception as exc:
        raise ThumbError(f"cannot open PDF: {exc}") from exc
    try:
        if document.needs_pass:
            raise ThumbError("PDF is password-protected")
        count = document.page_count
        try:
            indices = parse_pages(options.pages, count)
        except ValueError as exc:
            raise ThumbError(str(exc)) from exc
    except ThumbError:
        raise
    except Exception as exc:
        raise ThumbError(f"cannot open PDF: {exc}") from exc
    finally:
        document.close()
    if not indices:
        raise ThumbError(f"no pages match {options.pages!r} (document has {count} pages)")
    return [index + 1 for index in indices]


def output_names(src: Path, options: ThumbOptions, page_numbers: list[int]) -> list[str]:
    """Match pdfthumb: ``name.webp`` for ``-p 1``, otherwise ``name_pN.webp``."""
    if len(page_numbers) == 1 and options.pages.strip() == "1":
        return [f"{src.stem}.webp"]
    return [f"{src.stem}_p{number}.webp" for number in page_numbers]


def atomic_write(dest: Path, data: bytes) -> None:
    """Write ``dest`` via a sibling temp file so readers never see a partial WebP."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f".{dest.name}.{os.getpid()}.part")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, dest)
    finally:
        if temporary.exists():
            temporary.unlink()


def render_file(
    src: Path,
    dest_dir: Path,
    options: ThumbOptions,
    *,
    relative_dir: Path | None = None,
) -> list[ThumbResult]:
    """Write thumbnails for one file. Failures are results, not exceptions."""
    try:
        options.validate()
    except ValueError as exc:
        return [ThumbResult(str(src), "error", error=str(exc))]

    out_dir = Path(dest_dir) / (relative_dir if relative_dir is not None else Path("."))
    label = str(src)
    try:
        if looks_like_pdf(src, src.name):
            pages = pdf_page_numbers(src, options)
            targets = list(zip(pages, output_names(src, options, pages)))
            kind = "pdf"
        elif is_image_name(src.name):
            targets = [(1, f"{src.stem}.webp")]
            kind = "image"
        else:
            return [ThumbResult(label, "error", error=f"unsupported format: {src.name}")]
    except ThumbError as exc:
        return [ThumbResult(label, "error", error=str(exc))]

    results: list[ThumbResult] = []
    for page, name in targets:
        dest = out_dir / name
        if dest.exists() and not options.overwrite:
            results.append(ThumbResult(label, "skipped", dest))
            continue
        try:
            data = make_thumbnail(src, src.name, options, page=page if kind == "pdf" else 1)
            atomic_write(dest, data)
        except ThumbError as exc:
            results.append(ThumbResult(label, "error", error=str(exc)))
            continue
        except OSError as exc:
            results.append(ThumbResult(label, "error", error=str(exc)))
            continue
        results.append(ThumbResult(label, "ok", dest))
    return results


def write_thumbnail(
    src: Path,
    dest_dir: Path,
    options: ThumbOptions,
    *,
    relative_dir: Path | None = None,
) -> list[Path]:
    """Write thumbnails and return their paths. Raises ``ThumbError`` on failure.

    An existing file is left in place unless ``options.overwrite`` is set, and
    its path is still returned.
    """
    results = render_file(src, dest_dir, options, relative_dir=relative_dir)
    errors = [result.error for result in results if result.status == "error"]
    if errors:
        raise ThumbError(errors[0] or "thumbnail failed")
    return [result.output for result in results if result.output is not None]


def planned_outputs(src: Path, dest_dir: Path, options: ThumbOptions) -> list[Path]:
    """Paths ``render_file`` would write for ``src`` into ``dest_dir``."""
    if looks_like_pdf(src, src.name):
        pages = pdf_page_numbers(src, options)
        names = output_names(src, options, pages)
    elif is_image_name(src.name):
        names = [f"{src.stem}.webp"]
    else:
        raise ThumbError(f"unsupported format: {src.name}")
    return [dest_dir / name for name in names]
