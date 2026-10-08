"""WebP thumbnails for images and PDFs."""

from .core import ThumbError, ThumbResult, make_thumbnail, write_thumbnail
from .options import ThumbOptions

__version__ = "0.1.0"

__all__ = [
    "ThumbError",
    "ThumbOptions",
    "ThumbResult",
    "make_thumbnail",
    "write_thumbnail",
    "__version__",
]
