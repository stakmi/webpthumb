"""Watch a directory and write a WebP thumbnail for each new image or PDF."""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import replace
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .core import ThumbError, planned_outputs, render_file
from .options import ThumbOptions

log = logging.getLogger("webpthumb")

# Core Pillow formats, HEIF/HEIC/AVIF from the pillow-heif extra, plus PDF.
WATCH_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico",
    ".heic", ".heics", ".heif", ".heifs", ".hif", ".avif", ".avifs",
    ".pdf",
})


def accepts(path: Path) -> bool:
    """True for a visible image or PDF. Hidden names and ``*.part`` files are ignored."""
    name = path.name
    if not name or name.startswith(".") or name.endswith(".part"):
        return False
    return path.suffix.lower() in WATCH_EXTENSIONS


def ensure_output_outside(input_dir: Path, output_dir: Path) -> None:
    """Refuse an output directory that the watcher would also see as input."""
    source = input_dir.resolve()
    destination = output_dir.resolve()
    if destination == source or source in destination.parents:
        raise ValueError(
            f"output directory must not be inside the input directory: {output_dir}"
        )


def wait_until_stable(path: Path, pause: float = 0.5, timeout: float = 10.0) -> bool:
    """Wait until ``path`` keeps the same size for ``pause`` seconds, or until ``timeout``."""
    last_size: int | None = None
    stable_since: float | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.is_file():
            return False
        size = path.stat().st_size
        now = time.monotonic()
        if last_size is not None and size == last_size:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= pause:
                return True
        else:
            last_size = size
            stable_since = None
        time.sleep(0.1)
    return path.is_file()


def iter_sources(root: Path, *, recursive: bool) -> list[Path]:
    if recursive:
        found = [path for path in root.rglob("*") if path.is_file() and accepts(path)]
    else:
        found = [path for path in root.iterdir() if path.is_file() and accepts(path)]
    return sorted(found)


class ThumbnailHandler(FileSystemEventHandler):
    """Turn created, modified, and moved files into WebP thumbnails."""

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        options: ThumbOptions,
        *,
        recursive: bool = False,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.options = options
        self.recursive = recursive
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._again: set[str] = set()

    def on_created(self, event) -> None:  # noqa: ANN001
        self._on_event(event.src_path, event.is_directory)

    def on_modified(self, event) -> None:  # noqa: ANN001
        self._on_event(event.src_path, event.is_directory)

    def on_moved(self, event) -> None:  # noqa: ANN001
        self._on_event(event.dest_path, event.is_directory)

    def _on_event(self, raw: str, is_directory: bool) -> None:
        if is_directory:
            return
        path = Path(raw)
        if not accepts(path):
            return
        self.process(path, settle=True)

    def scan(self) -> None:
        """Thumbnail files that are already in the input directory."""
        for path in iter_sources(self.input_dir, recursive=self.recursive):
            self.process(path, settle=False)

    def process(self, path: Path, *, settle: bool = False) -> None:
        """Thumbnail one file. A second call skips a WebP that is newer than the source."""
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        with self._lock:
            if key in self._inflight:
                self._again.add(key)
                return
            self._inflight.add(key)
        try:
            while True:
                if settle and not wait_until_stable(path):
                    if not path.exists():
                        log.error("failed %s: file disappeared", path)
                    break
                if path.is_file():
                    self._render_one(path)
                with self._lock:
                    if key in self._again:
                        self._again.discard(key)
                        continue
                    break
        finally:
            with self._lock:
                self._inflight.discard(key)
                self._again.discard(key)

    def _render_one(self, path: Path) -> None:
        if not accepts(path):
            return
        try:
            relative = path.resolve().parent.relative_to(self.input_dir.resolve())
        except ValueError:
            log.error("failed %s: outside the watch directory", path)
            return
        out_dir = self.output_dir / relative
        try:
            planned = planned_outputs(path, out_dir, self.options)
            source_mtime = path.stat().st_mtime
        except ThumbError as exc:
            log.error("failed %s: %s", path, exc)
            return
        except OSError as exc:
            log.error("failed %s: %s", path, exc)
            return
        fresh = [
            dest for dest in planned
            if dest.exists() and dest.stat().st_mtime > source_mtime
        ]
        if planned and not self.options.overwrite and len(fresh) == len(planned):
            log.info("skipped %s", path)
            return
        effective = self.options if self.options.overwrite else replace(self.options, overwrite=True)
        results = render_file(path, self.output_dir, effective, relative_dir=relative)
        errors = [result for result in results if result.status == "error"]
        if errors:
            log.error("failed %s: %s", path, errors[0].error)
            return
        written = [str(result.output) for result in results if result.output is not None]
        log.info("ok %s -> %s", path, ", ".join(written))


def run_watch(
    input_dir: Path | str,
    output_dir: Path | str,
    options: ThumbOptions,
    *,
    recursive: bool = False,
    install_signals: bool = True,
    stop_event: threading.Event | None = None,
) -> int:
    """Scan ``input_dir``, then keep watching until a signal or ``stop_event``."""
    source = Path(input_dir)
    destination = Path(output_dir)
    if not source.is_dir():
        log.error("watch directory does not exist: %s", source)
        return 1
    try:
        options.validate()
        ensure_output_outside(source, destination)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    destination.mkdir(parents=True, exist_ok=True)
    handler = ThumbnailHandler(source, destination, options, recursive=recursive)
    log.info("watching %s -> %s", source, destination)
    handler.scan()
    observer = Observer()
    observer.schedule(handler, str(source), recursive=recursive)
    observer.start()
    stop = stop_event or threading.Event()

    def _request_stop(signum, _frame) -> None:  # noqa: ANN001
        log.info("stopping watcher (signal %s)", signum)
        stop.set()

    previous = []
    if install_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous.append((sig, signal.signal(sig, _request_stop)))
    try:
        while not stop.wait(0.5):
            if not observer.is_alive():
                log.error("watcher stopped unexpectedly")
                return 1
    finally:
        observer.stop()
        observer.join()
        for sig, handler_fn in previous:
            signal.signal(sig, handler_fn)
    return 0
