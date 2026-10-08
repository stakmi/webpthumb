"""Folder watcher: skip fresh thumbnails, refresh stale ones, reject nested output."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from webpthumb import ThumbOptions
from webpthumb.watch import ThumbnailHandler, accepts, ensure_output_outside, run_watch

from samples import heif_bytes, png_bytes


def test_accepts_heif_names() -> None:
    assert accepts(Path("photo.heif"))
    assert accepts(Path("photo.HEIC"))
    assert not accepts(Path(".photo.heif"))
    assert not accepts(Path("photo.heif.part"))


def test_heif_file_is_thumbnailed(tmp_path: Path) -> None:
    source_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    source = source_dir / "shot.heif"
    source.write_bytes(heif_bytes((80, 40)))
    handler = ThumbnailHandler(source_dir, output_dir, ThumbOptions(width=20))
    handler.process(source)
    dest = output_dir / "shot.webp"
    assert dest.is_file()
    assert dest.read_bytes()[8:12] == b"WEBP"


def test_second_process_does_not_rewrite(tmp_path: Path) -> None:
    source_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    source = source_dir / "a.png"
    source.write_bytes(png_bytes((60, 30)))
    handler = ThumbnailHandler(source_dir, output_dir, ThumbOptions(width=24))
    handler.process(source)
    dest = output_dir / "a.webp"
    assert dest.is_file()
    assert dest.read_bytes()[:4] == b"RIFF"
    stamp = dest.stat().st_mtime_ns
    time.sleep(0.05)
    handler.process(source)
    assert dest.stat().st_mtime_ns == stamp


def test_newer_source_is_rewritten(tmp_path: Path) -> None:
    source_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    source = source_dir / "a.png"
    source.write_bytes(png_bytes((60, 30), "red"))
    handler = ThumbnailHandler(source_dir, output_dir, ThumbOptions(width=24))
    handler.process(source)
    dest = output_dir / "a.webp"
    stamp = dest.stat().st_mtime_ns
    future = time.time() + 5
    source.write_bytes(png_bytes((60, 30), "blue"))
    import os
    os.utime(source, (future, future))
    handler.process(source)
    assert dest.stat().st_mtime_ns != stamp


def test_output_inside_input_is_rejected(tmp_path: Path) -> None:
    source_dir = tmp_path / "in"
    source_dir.mkdir()
    with pytest.raises(ValueError, match="inside"):
        ensure_output_outside(source_dir, source_dir / "out")
    with pytest.raises(ValueError, match="inside"):
        ensure_output_outside(source_dir, source_dir)


def test_run_watch_scans_then_sees_a_new_file(tmp_path: Path) -> None:
    source_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    (source_dir / "already.png").write_bytes(png_bytes((40, 20)))
    stop = threading.Event()
    thread = threading.Thread(
        target=run_watch,
        args=(source_dir, output_dir, ThumbOptions(width=16)),
        kwargs={"install_signals": False, "stop_event": stop},
        daemon=True,
    )
    thread.start()
    try:
        existing = output_dir / "already.webp"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not existing.is_file():
            time.sleep(0.05)
        assert existing.is_file()
        (source_dir / "fresh.png").write_bytes(png_bytes((40, 20), "green"))
        fresh = output_dir / "fresh.webp"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not fresh.is_file():
            time.sleep(0.05)
        assert fresh.is_file()
        assert fresh.read_bytes()[8:12] == b"WEBP"
    finally:
        stop.set()
        thread.join(5)
        assert not thread.is_alive()
