"""HTTP endpoint that returns a WebP thumbnail for an uploaded image or PDF."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response

from . import __version__
from .core import ThumbError, make_thumbnail
from .options import ThumbOptions

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_optional_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def upload_limit_bytes() -> int:
    megabytes = _env_int("WEBTHUMB_MAX_UPLOAD_MB", 32)
    if megabytes < 1:
        megabytes = 32
    return megabytes * 1024 * 1024


def safe_stem(filename: str | None) -> str:
    stem = Path(filename or "thumbnail").stem
    cleaned = _SAFE.sub("_", stem).strip("._")
    return (cleaned or "thumbnail")[:120]


def create_app(max_upload_bytes: int | None = None) -> FastAPI:
    """Build the API. Query defaults fall back to ``WEBTHUMB_*`` environment variables."""
    limit = upload_limit_bytes() if max_upload_bytes is None else max_upload_bytes
    app = FastAPI(title="webpthumb", version=__version__)
    app.state.max_upload_bytes = limit

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/thumbnail")
    async def thumbnail(
        file: UploadFile,
        width: int | None = None,
        height: int | None = None,
        quality: int | None = None,
        page: int = 1,
        crop_top: float | None = None,
    ) -> Response:
        data = await _read_limited(file, app.state.max_upload_bytes)
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        resolved_width = width if width is not None else _env_int("WEBTHUMB_WIDTH", 256)
        resolved_height = height if height is not None else _env_optional_int("WEBTHUMB_HEIGHT")
        resolved_quality = quality if quality is not None else _env_int("WEBTHUMB_QUALITY", 80)
        resolved_crop = crop_top if crop_top is not None else _env_optional_float("WEBTHUMB_CROP_TOP")
        filename = file.filename or "thumbnail"
        try:
            options = ThumbOptions(
                width=resolved_width,
                height=resolved_height,
                quality=resolved_quality,
                crop_top=resolved_crop,
            )
            webp = make_thumbnail(data, filename, options, page=page)
        except ThumbError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        stem = safe_stem(filename)
        return Response(
            content=webp,
            media_type="image/webp",
            headers={"Content-Disposition": f'inline; filename="{stem}.webp"'},
        )

    return app


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await file.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise HTTPException(status_code=413, detail="upload exceeds limit")
        chunks.append(block)
    return b"".join(chunks)


app = create_app()
