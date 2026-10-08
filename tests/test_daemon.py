"""Background watcher: start, status, and stop."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from webpthumb.cli import main
from webpthumb.daemon import command_is_watcher, state_dir

from samples import png_bytes


@pytest.fixture
def isolated_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WEBTHUMB_RUN_DIR", str(tmp_path / "run"))
    yield
    main(["stop"])


def _wait_for(path: Path, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.05)
    return False


def test_command_is_watcher() -> None:
    assert command_is_watcher("/usr/bin/python -m webpthumb watch /in -o /out")
    assert command_is_watcher("/home/me/.local/bin/webpthumb watch /in")
    assert not command_is_watcher("/usr/bin/python -m webpthumb serve")
    assert not command_is_watcher("python -m pytest tests/test_daemon.py")


def test_state_dir_follows_run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTHUMB_RUN_DIR", str(tmp_path / "state"))
    assert state_dir() == tmp_path / "state"


def test_status_and_stop_when_nothing_is_running(isolated_daemon: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 1
    assert capsys.readouterr().out == "not watching\n"
    assert main(["stop"]) == 0
    assert capsys.readouterr().out == "not watching\n"


def test_status_ignores_an_unrelated_pid(isolated_daemon: None, capsys: pytest.CaptureFixture[str]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True)
    (directory / "watcher.json").write_text(json.dumps({
        "pid": os.getpid(),
        "input": "/in",
        "output": "/out",
        "recursive": False,
        "log": "/log",
        "started": "now",
    }))
    assert main(["status"]) == 1
    assert capsys.readouterr().out == "not watching\n"
    assert not (directory / "watcher.json").exists()
    assert main(["stop"]) == 0


def test_start_rejects_a_missing_directory(isolated_daemon: None, tmp_path: Path) -> None:
    code = main(["start", str(tmp_path / "missing"), "-o", str(tmp_path / "out")])
    assert code == 1
    assert main(["status"]) == 1


def test_start_rejects_bad_width(isolated_daemon: None, tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    code = main(["start", str(inbox), "-o", str(tmp_path / "out"), "-w", "0"])
    assert code == 2
    assert main(["status"]) == 1


def test_start_rejects_output_inside_input(isolated_daemon: None, tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    code = main(["start", str(inbox), "-o", str(inbox / "out")])
    assert code == 2
    assert main(["status"]) == 1


def test_background_watch_then_status_then_stop(
    isolated_daemon: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbox = tmp_path / "in"
    output = tmp_path / "out"
    inbox.mkdir()
    (inbox / "already.png").write_bytes(png_bytes((40, 20)))
    code = main([
        "watch", str(inbox), "-o", str(output), "-w", "16", "--background",
    ])
    assert code == 0
    started = capsys.readouterr().out
    assert started.startswith("started ")
    assert _wait_for(output / "already.webp")
    assert (output / "already.webp").read_bytes()[8:12] == b"WEBP"

    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert status.startswith("watching\n")
    assert f"input {inbox.resolve()}\n" in status
    assert f"output {output.resolve()}\n" in status
    pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith("pid ")))
    os.kill(pid, 0)

    (inbox / "fresh.png").write_bytes(png_bytes((40, 20), "green"))
    assert _wait_for(output / "fresh.webp")

    assert main(["start", str(inbox), "-o", str(output), "-w", "16"]) == 1
    assert "already watching" in capsys.readouterr().err

    assert main(["stop"]) == 0
    assert capsys.readouterr().out == f"stopped {pid}\n"
    assert main(["status"]) == 1
    assert capsys.readouterr().out == "not watching\n"
    (inbox / "after.png").write_bytes(png_bytes((40, 20), "blue"))
    time.sleep(1.2)
    assert not (output / "after.webp").exists()
