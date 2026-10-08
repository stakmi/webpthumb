"""Shared thumbnail settings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThumbOptions:
    """Size and encode settings used by the CLI, the API, and the watcher.

    ``pages`` is a pdfthumb spec (``1``, ``1,3-5``, ``all``). The HTTP endpoint
    renders a single page and ignores this field. ``crop_top`` applies to PDFs
    only: the kept band has height ``width * crop_top``.
    """

    width: int = 256
    height: int | None = None
    quality: int = 80
    pages: str = "1"
    crop_top: float | None = None
    overwrite: bool = False

    def validate(self) -> None:
        if self.width < 1 or (self.height is not None and self.height < 1):
            raise ValueError("width and height must be positive")
        if not isinstance(self.quality, int) or not 0 <= self.quality <= 100:
            raise ValueError("quality must be an int from 0 to 100")
        if self.crop_top is not None and self.crop_top <= 0:
            raise ValueError("crop_top ratio must be positive")
        if self.crop_top is not None and self.height is not None:
            raise ValueError("crop_top cannot be combined with height")
        if not str(self.pages).strip():
            raise ValueError("pages spec is empty")
