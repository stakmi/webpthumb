"""Command line: convert files, serve HTTP, or watch a directory."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pdfthumb import generate_thumbnails

from .api import create_app
from .core import ThumbResult, is_image_name, render_file
from .daemon import start_watcher, stop_watcher, watcher_status
from .options import ThumbOptions
from .watch import run_watch

log = logging.getLogger("webpthumb")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_options(args: argparse.Namespace) -> ThumbOptions:
    if args.width is not None:
        width = args.width
    else:
        width = int(os.environ.get("WEBTHUMB_WIDTH", "256"))
    if args.height is not None:
        height = args.height
    else:
        raw_height = os.environ.get("WEBTHUMB_HEIGHT")
        height = int(raw_height) if raw_height else None
    if args.quality is not None:
        quality = args.quality
    else:
        quality = int(os.environ.get("WEBTHUMB_QUALITY", "80"))
    pages = args.pages if args.pages is not None else os.environ.get("WEBTHUMB_PAGES", "1")
    if args.crop_top is not None:
        crop_top = args.crop_top
    else:
        raw_crop = os.environ.get("WEBTHUMB_CROP_TOP")
        crop_top = float(raw_crop) if raw_crop else None
    return ThumbOptions(
        width=width,
        height=height,
        quality=quality,
        pages=pages,
        crop_top=crop_top,
        overwrite=bool(args.overwrite) or _env_flag("WEBTHUMB_OVERWRITE"),
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-w", "--width", type=int, default=None, help="thumbnail width (default 256)")
    parser.add_argument("--height", type=int, default=None, help="maximum height; fit inside width x height")
    parser.add_argument("-q", "--quality", type=int, default=None, help="WebP quality 0-100 (default 80)")
    parser.add_argument("-p", "--pages", default=None, help="PDF pages, e.g. 1, 1,3-5, all (default 1)")
    parser.add_argument(
        "-c",
        "--crop-top",
        nargs="?",
        const=1.0,
        type=float,
        default=None,
        help="PDF only: keep the top band, height = width x RATIO (default 1 when the flag has no value)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace an existing thumbnail")
    parser.add_argument("-r", "--recursive", action="store_true", help="walk directories recursively")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webpthumb",
        description="Write a WebP thumbnail for an image or a PDF.",
    )
    commands = parser.add_subparsers(dest="cmd", required=True)

    convert = commands.add_parser("convert", help="thumbnail files or directories")
    convert.add_argument("inputs", nargs="+", help="image files, PDF files, or directories")
    convert.add_argument("-o", "--output", default=None, help="output directory (default ./thumbnails)")
    convert.add_argument("-j", "--jobs", type=int, default=None, help="parallel workers (default: CPU count)")
    _add_common(convert)

    serve = commands.add_parser("serve", help="serve POST /v1/thumbnail")
    serve.add_argument("--host", default=None, help="bind address (default 127.0.0.1, or WEBTHUMB_HOST)")
    serve.add_argument("--port", type=int, default=None, help="bind port (default 8080, or WEBTHUMB_PORT)")

    watch = commands.add_parser("watch", help="keep thumbnailing files added to a directory")
    _add_watch_args(watch, background_flag=True)

    start = commands.add_parser("start", help="start the directory watcher in the background")
    _add_watch_args(start, background_flag=False)

    commands.add_parser("stop", help="stop the background watcher")
    commands.add_parser("status", help="show whether the background watcher is running")
    return parser


def _add_watch_args(parser: argparse.ArgumentParser, *, background_flag: bool) -> None:
    parser.add_argument("input", help="directory to watch")
    parser.add_argument("-o", "--output", default=None, help="output directory (or WEBTHUMB_OUTPUT)")
    _add_common(parser)
    if background_flag:
        parser.add_argument(
            "-b",
            "--background",
            action="store_true",
            help="run in the background and return",
        )


def _iter_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        files = [path for path in root.rglob("*") if path.is_file() and not path.name.startswith(".")]
    else:
        files = [path for path in root.iterdir() if path.is_file() and not path.name.startswith(".")]
    return sorted(files)


def _collect(inputs: list[str], recursive: bool) -> tuple[list[Path], list[tuple[Path, Path]], list[str]]:
    """Split inputs into PDF paths (for pdfthumb), image jobs, and error strings."""
    pdf_inputs: list[Path] = []
    images: list[tuple[Path, Path]] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            pdf_inputs.append(path)
            for found in _iter_files(path, recursive):
                if found.suffix.lower() == ".pdf" or not is_image_name(found.name):
                    continue
                resolved = found.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                images.append((found, found.parent.relative_to(path)))
        elif path.is_file():
            if path.suffix.lower() == ".pdf":
                pdf_inputs.append(path)
            elif is_image_name(path.name):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    images.append((path, Path(".")))
            else:
                errors.append(f"unsupported format: {path}")
        else:
            errors.append(f"not found: {path}")
    return pdf_inputs, images, errors


def _print_result(result: ThumbResult) -> None:
    if result.status == "error":
        print(f"failed {result.source}: {result.error}", file=sys.stderr)
    elif result.status == "skipped":
        print(f"skipped {result.source} -> {result.output}")
    else:
        print(f"ok {result.source} -> {result.output}")


def cmd_convert(args: argparse.Namespace) -> int:
    try:
        options = _build_options(args)
        options.validate()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.jobs is not None:
        jobs = args.jobs
    else:
        raw_jobs = os.environ.get("WEBTHUMB_JOBS")
        jobs = int(raw_jobs) if raw_jobs else (os.cpu_count() or 1)
    if jobs < 1:
        print("error: jobs must be positive", file=sys.stderr)
        return 2
    output = Path(args.output or os.environ.get("WEBTHUMB_OUTPUT", "thumbnails"))
    recursive = bool(args.recursive) or _env_flag("WEBTHUMB_RECURSIVE")
    pdf_inputs, images, errors = _collect(args.inputs, recursive)
    failed = len(errors)
    for message in errors:
        print(f"failed {message}", file=sys.stderr)

    if pdf_inputs:
        try:
            pdf_results = generate_thumbnails(
                pdf_inputs,
                output,
                width=options.width,
                height=options.height,
                quality=options.quality,
                pages=options.pages,
                crop_top=options.crop_top,
                recursive=recursive,
                overwrite=options.overwrite,
                jobs=jobs,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for result in pdf_results:
            if result.status == "error":
                print(f"failed {result.input}: {result.error}", file=sys.stderr)
                failed += 1
            elif result.status == "skipped":
                print(f"skipped {result.input} -> {result.output}")
            else:
                print(f"ok {result.input} -> {result.output}")

    def _render(item: tuple[Path, Path]) -> list[ThumbResult]:
        src, relative = item
        return render_file(src, output, options, relative_dir=relative)

    if jobs == 1 or len(images) < 2:
        rendered = [_render(item) for item in images]
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, len(images))) as pool:
            rendered = list(pool.map(_render, images))
    for results in rendered:
        for result in results:
            _print_result(result)
            if result.status == "error":
                failed += 1
    return 1 if failed else 0


def _start_embedded_watch() -> None:
    watch = os.environ.get("WEBTHUMB_WATCH")
    output = os.environ.get("WEBTHUMB_OUTPUT")
    if not watch and not output:
        return
    if not watch or not output:
        log.error("set both WEBTHUMB_WATCH and WEBTHUMB_OUTPUT to watch while serving")
        return
    try:
        options = ThumbOptions(
            width=int(os.environ.get("WEBTHUMB_WIDTH", "256")),
            height=int(os.environ["WEBTHUMB_HEIGHT"]) if os.environ.get("WEBTHUMB_HEIGHT") else None,
            quality=int(os.environ.get("WEBTHUMB_QUALITY", "80")),
            pages=os.environ.get("WEBTHUMB_PAGES", "1"),
            crop_top=float(os.environ["WEBTHUMB_CROP_TOP"]) if os.environ.get("WEBTHUMB_CROP_TOP") else None,
            overwrite=_env_flag("WEBTHUMB_OVERWRITE"),
        )
        options.validate()
    except ValueError as exc:
        log.error("watcher options: %s", exc)
        return
    thread = threading.Thread(
        target=run_watch,
        args=(watch, output, options),
        kwargs={
            "recursive": _env_flag("WEBTHUMB_RECURSIVE"),
            "install_signals": False,
        },
        name="webpthumb-watch",
        daemon=True,
    )
    thread.start()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    host = args.host or os.environ.get("WEBTHUMB_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("WEBTHUMB_PORT", "8080"))
    if not 1 <= port <= 65535:
        print("error: port must be from 1 to 65535", file=sys.stderr)
        return 2
    _start_embedded_watch()
    uvicorn.run(create_app(), host=host, port=port)
    return 0


def _run_watch_command(args: argparse.Namespace, *, background: bool) -> int:
    try:
        options = _build_options(args)
        options.validate()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = args.output or os.environ.get("WEBTHUMB_OUTPUT")
    if not output:
        print("error: watch needs -o or WEBTHUMB_OUTPUT", file=sys.stderr)
        return 2
    recursive = bool(args.recursive) or _env_flag("WEBTHUMB_RECURSIVE")
    if background:
        return start_watcher(args.input, output, options, recursive=recursive)
    return run_watch(args.input, output, options, recursive=recursive)


def cmd_watch(args: argparse.Namespace) -> int:
    return _run_watch_command(args, background=bool(args.background))


def cmd_start(args: argparse.Namespace) -> int:
    return _run_watch_command(args, background=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "convert":
        return cmd_convert(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return stop_watcher()
    if args.cmd == "status":
        return watcher_status()
    parser.error(f"unknown command {args.cmd}")
    return 2
