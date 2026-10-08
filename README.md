# webpthumb

WebP thumbnails for images and PDFs. One command covers a one-shot convert, an HTTP endpoint, and a directory watcher.

PDFs are rendered by [pdfthumb](https://github.com/stakmi/pdfthumb) (PyMuPDF). Images are resized and encoded by [image-to-webp](https://github.com/stakmi/image-to-webp) (Pillow). The watcher uses [watchdog](https://github.com/gorakhargosh/watchdog). A thumbnail is a single still frame: the first frame of an animated GIF or APNG, or the requested PDF page.

Images are only shrunk, to fit the requested width (and height, when you set one). PDFs are rendered at that width. The defaults match pdfthumb: 256 pixels wide, WebP quality 80, page 1.

## Install

Python 3.9 or newer. `install.sh` prefers `python3.12` when it is on `PATH`.

```bash
./install.sh            # .venv, editable install, link ~/.local/bin/webpthumb
./install.sh --dev      # also install pytest
./install.sh --uninstall
```

Override the link location with `BIN_DIR` (default `~/.local/bin`). The script uses `sudo` only when that directory is not writable.

The default install reads the formats Pillow ships with (PNG, JPEG, GIF, BMP, TIFF, WebP, ICO, and others), HEIF (`.heic`, `.heif`, `.hif`, and AVIF, via pillow-heif), and PDF. Camera RAW and SVG need the optional extras of `image-to-webp` (`pip install "image-to-webp[raw]"` or `[svg]`). SVG also needs the system cairo library. RAW and SVG are not in the Docker image.

## CLI

```bash
webpthumb convert photo.png doc.pdf -o ./thumbnails
webpthumb convert ./inbox -r -o ./thumbnails -w 512 -q 80
webpthumb convert doc.pdf -p all          # doc_p1.webp, doc_p2.webp, ...
webpthumb convert doc.pdf --height 300    # fit inside 256x300
webpthumb convert doc.pdf --crop-top      # PDF only: top square
webpthumb convert doc.pdf -c 0.5          # PDF only: top band, 256x128

webpthumb serve --host 127.0.0.1 --port 8080

webpthumb watch ./inbox -o ./thumbnails -r
webpthumb watch ./inbox -o ./thumbnails -r --background
webpthumb start ./inbox -o ./thumbnails -r
webpthumb status
webpthumb stop
```

`convert` sends PDFs through `pdfthumb.generate_thumbnails` and images through image-to-webp. Output names are `<name>.webp` for an image or for PDF page 1. Any other page spec uses `<name>_p<N>.webp`. Existing files are skipped unless you pass `--overwrite`.

Exit codes: `0` when every file succeeded or was skipped, `1` when a file failed, `2` for bad arguments. `status` exits `1` when the background watcher is not running. `stop` exits `0` when it is already stopped.

## HTTP

`GET /health` returns `{"status":"ok"}`.

`POST /v1/thumbnail` takes one multipart field named `file` and returns `image/webp`. Query parameters: `width`, `height`, `quality`, `page`, `crop_top`. `crop_top` applies only to PDFs. Interactive docs are at `/docs`.

```bash
curl -sS -D- --output thumb.webp \
  -F "file=@photo.png" \
  "http://127.0.0.1:8080/v1/thumbnail?width=256&quality=80"
```

A file that is not an image or a PDF, or a PDF that cannot be read, returns `400`. An upload larger than `WEBTHUMB_MAX_UPLOAD_MB` (default 32) returns `413`. The endpoint reads only the upload. It does not accept a filesystem path.

## Watch

`webpthumb watch` thumbnails files already in the directory, then stays running until SIGINT or SIGTERM. New and updated files are picked up with watchdog. The watcher waits until a file's size stops changing (about half a second) so a copy in progress is not read early.

`webpthumb watch --background` and `webpthumb start` do that work in the background and return. `webpthumb status` prints whether that watcher is running. `webpthumb stop` sends SIGTERM and waits for it to exit. A second start is refused while the first is still running. The pid, the directories, and the log live under `WEBTHUMB_RUN_DIR` (`$XDG_STATE_HOME/webpthumb`, or `~/.local/state/webpthumb`). A foreground `watch` is not tracked. Keep `watch` in the foreground when it is the main process of a container.

Watched extensions: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tif`, `.tiff`, `.webp`, `.ico`, `.heic`, `.heics`, `.heif`, `.heifs`, `.hif`, `.avif`, `.avifs`, `.pdf`. Hidden files and `*.part` files are ignored. The output directory must not sit inside the input directory. With `-r`, the output tree mirrors the input tree.

A thumbnail is rewritten when the source is newer than the WebP, or when `--overwrite` is set.

`webpthumb serve` starts the same watcher in the background when both `WEBTHUMB_WATCH` and `WEBTHUMB_OUTPUT` are set.

## Environment

Used when the matching flag or query parameter is omitted.

| Variable | Default | Used by |
|---|---|---|
| `WEBTHUMB_WIDTH` | `256` | convert, serve, watch |
| `WEBTHUMB_HEIGHT` | unset | convert, serve, watch |
| `WEBTHUMB_QUALITY` | `80` | convert, serve, watch |
| `WEBTHUMB_PAGES` | `1` | convert, watch |
| `WEBTHUMB_CROP_TOP` | unset | convert, serve, watch |
| `WEBTHUMB_OVERWRITE` | off | convert, watch (`1`, `true`, `yes`, `on`) |
| `WEBTHUMB_RECURSIVE` | off | convert, watch |
| `WEBTHUMB_JOBS` | CPU count | convert |
| `WEBTHUMB_OUTPUT` | `./thumbnails` for convert | convert, watch, serve |
| `WEBTHUMB_WATCH` | unset | serve, with `WEBTHUMB_OUTPUT` |
| `WEBTHUMB_HOST` | `127.0.0.1` | serve (the image sets `0.0.0.0`) |
| `WEBTHUMB_PORT` | `8080` | serve |
| `WEBTHUMB_MAX_UPLOAD_MB` | `32` | serve |
| `WEBTHUMB_RUN_DIR` | `~/.local/state/webpthumb` | start, stop, status |

## Docker

The image is `python:3.12-slim` plus this package. PyMuPDF carries its own MuPDF build, so poppler and ImageMagick are not installed.

```bash
docker build -t webpthumb .
docker run --rm -p 8080:8080 webpthumb

# API and watcher together
docker compose up --build

# watcher only
docker run --rm -v "$PWD/in:/data/in" -v "$PWD/out:/data/out" \
  webpthumb watch /data/in -o /data/out -r
```

`docker compose up` publishes port 8080 and mounts `./in` and `./out`. Drop a PNG, HEIC, or PDF into `./in` and the thumbnail appears in `./out`.

## Tests

```bash
pytest
```

## Library

```python
from webpthumb import ThumbOptions, make_thumbnail, write_thumbnail

webp = make_thumbnail(open("doc.pdf", "rb").read(), "doc.pdf", ThumbOptions(width=256))
paths = write_thumbnail("photo.png", "thumbnails", ThumbOptions(width=256, quality=80))
```
