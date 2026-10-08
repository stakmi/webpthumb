"""Start, stop, and report the background directory watcher."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .options import ThumbOptions
from .watch import ensure_output_outside

_STATE_NAME = "watcher.json"
_LOG_NAME = "watcher.log"
_LOCK_NAME = "watcher.lock"
_READY = " INFO watching "
_STOP_TIMEOUT = 5.0


@dataclass(frozen=True)
class WatcherState:
    pid: int
    input: str
    output: str
    recursive: bool
    log: str
    started: str

    def format(self) -> str:
        recursive = "yes" if self.recursive else "no"
        return (
            "watching\n"
            f"pid {self.pid}\n"
            f"input {self.input}\n"
            f"output {self.output}\n"
            f"recursive {recursive}\n"
            f"log {self.log}\n"
            f"started {self.started}\n"
        )


def state_dir() -> Path:
    """Directory that holds the pid file and the watcher log.

    ``WEBTHUMB_RUN_DIR`` wins. Otherwise this is ``$XDG_STATE_HOME/webpthumb``
    or ``~/.local/state/webpthumb``.
    """
    raw = os.environ.get("WEBTHUMB_RUN_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "webpthumb"


def command_is_watcher(command: str) -> bool:
    """True when ``command`` is a ``webpthumb watch`` process."""
    parts = command.split()
    program = any(Path(part).name == "webpthumb" for part in parts)
    return program and "watch" in parts


def start_watcher(
    input_dir: Path | str,
    output_dir: Path | str,
    options: ThumbOptions,
    *,
    recursive: bool,
) -> int:
    """Spawn ``webpthumb watch`` in its own session and record its pid."""
    try:
        options.validate()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    source_in = Path(input_dir).expanduser()
    if not source_in.is_dir():
        print(f"error: watch directory does not exist: {source_in}", file=sys.stderr)
        return 1
    source = source_in.resolve()
    destination = Path(output_dir).expanduser().resolve()
    try:
        ensure_output_outside(source, destination)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with _lock(directory):
        current = _live_state(directory)
        if current is not None:
            print(f"error: already watching (pid {current.pid})", file=sys.stderr)
            return 1
        _discard_state(directory)
        log_path = directory / _LOG_NAME
        argv = _watch_argv(source, destination, options, recursive=recursive)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc: subprocess.Popen[bytes] | None = None
        published = False
        try:
            # close_fds drops this lock fd so the child does not keep the lock.
            with log_path.open("wb") as handle:
                try:
                    proc = subprocess.Popen(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                        env=env,
                    )
                except OSError as exc:
                    print(f"error: could not start watcher: {exc}", file=sys.stderr)
                    return 1
            code = _wait_ready(proc, log_path)
            if code is not None:
                tail = _tail(log_path)
                print(f"error: watcher exited ({code})", file=sys.stderr)
                if tail:
                    print(tail, file=sys.stderr)
                if code <= 0:
                    return 1
                return code
            state = WatcherState(
                pid=proc.pid,
                input=str(source),
                output=str(destination),
                recursive=recursive,
                log=str(log_path),
                started=datetime.now(timezone.utc).isoformat(),
            )
            _write_state(directory, state)
            published = True
        finally:
            if proc is not None and not published and proc.poll() is None:
                _signal(proc.pid, signal.SIGTERM)
    print(f"started {state.pid} {state.input} -> {state.output}")
    print(f"log {state.log}")
    return 0


def stop_watcher() -> int:
    """Stop the background watcher. Already stopped is success."""
    directory = state_dir()
    if not directory.is_dir():
        print("not watching")
        return 0
    with _lock(directory):
        state = _live_state(directory)
        if state is None:
            _discard_state(directory)
            print("not watching")
            return 0
        pid = state.pid
        _signal(pid, signal.SIGTERM)
        if not _wait_until_gone(pid, _STOP_TIMEOUT):
            _signal(pid, signal.SIGKILL)
            if not _wait_until_gone(pid, 2.0):
                print(f"error: watcher {pid} did not stop", file=sys.stderr)
                return 1
        _discard_state(directory)
    print(f"stopped {pid}")
    return 0


def watcher_status() -> int:
    """Print whether the background watcher is running.

    Exit 0 when it is watching, 1 when it is not.
    """
    directory = state_dir()
    if not directory.is_dir():
        print("not watching")
        return 1
    with _lock(directory):
        state = _live_state(directory)
        if state is None:
            _discard_state(directory)
            print("not watching")
            return 1
        text = state.format()
    print(text, end="")
    return 0


def _watch_argv(
    source: Path,
    destination: Path,
    options: ThumbOptions,
    *,
    recursive: bool,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "webpthumb",
        "watch",
        str(source),
        "-o",
        str(destination),
        "-w",
        str(options.width),
        "-q",
        str(options.quality),
        "-p",
        str(options.pages),
    ]
    if options.height is not None:
        argv.extend(["--height", str(options.height)])
    if options.crop_top is not None:
        argv.extend(["--crop-top", str(options.crop_top)])
    if options.overwrite:
        argv.append("--overwrite")
    if recursive:
        argv.append("-r")
    return argv


def _wait_ready(proc: subprocess.Popen[bytes], log_path: Path, timeout: float = 20.0) -> int | None:
    """Return an exit code if the watcher quits, or None once it logs that it is watching."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return code
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if _READY in text:
            return None
        time.sleep(0.05)
    return proc.poll()


def _wait_until_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _still_ours(pid):
            return True
        time.sleep(0.1)
    return not _still_ours(pid)


def _still_ours(pid: int) -> bool:
    command = process_command(pid)
    return command is not None and command_is_watcher(command) and not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "state="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip().startswith("Z")


def process_command(pid: int) -> str | None:
    """Command line of ``pid``, or None when that process is not running."""
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return None
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    command = result.stdout.strip()
    return command or None


def _signal(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return


def _live_state(directory: Path) -> WatcherState | None:
    state = _read_state(directory)
    if state is None or not _still_ours(state.pid):
        return None
    return state


def _read_state(directory: Path) -> WatcherState | None:
    path = directory / _STATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    try:
        return WatcherState(
            pid=pid,
            input=str(data["input"]),
            output=str(data["output"]),
            recursive=bool(data.get("recursive")),
            log=str(data.get("log", "")),
            started=str(data.get("started", "")),
        )
    except KeyError:
        return None


def _write_state(directory: Path, state: WatcherState) -> None:
    path = directory / _STATE_NAME
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _discard_state(directory: Path) -> None:
    try:
        (directory / _STATE_NAME).unlink()
    except FileNotFoundError:
        return


def _tail(path: Path, limit: int = 20) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


@contextmanager
def _lock(directory: Path) -> Iterator[None]:
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / _LOCK_NAME).open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
