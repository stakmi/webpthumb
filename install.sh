#!/usr/bin/env bash
# Install webpthumb into a local virtualenv and link the CLI onto PATH.
#
#   ./install.sh                 install
#   ./install.sh --dev           also install pytest
#   ./install.sh --uninstall     remove the linked command and .venv
#   BIN_DIR=~/.local/bin ./install.sh
#   PYTHON=python3.12 ./install.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
CMD_NAME="${CMD_NAME:-webpthumb}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
TARGET="$BIN_DIR/$CMD_NAME"
LINK_SOURCE="$VENV_DIR/bin/$CMD_NAME"

usage() {
  cat <<EOF
Usage: ./install.sh [--dev | --uninstall]

  ./install.sh              create .venv, install webpthumb, link \$BIN_DIR/$CMD_NAME
  ./install.sh --dev        also install the dev extra (pytest)
  ./install.sh --uninstall  remove the link and the .venv

Environment:
  BIN_DIR    directory for the command link (default: ~/.local/bin)
  CMD_NAME   command name (default: webpthumb)
  PYTHON     interpreter used to create the virtualenv (default: python3.12, else python3)
EOF
}

# Run a command, escalating with sudo only if BIN_DIR isn't writable.
as_root() {
  if [ -w "$BIN_DIR" ] || { [ ! -e "$BIN_DIR" ] && [ -w "$(dirname "$BIN_DIR")" ]; }; then
    "$@"
  else
    echo "==> sudo required to write to $BIN_DIR"
    sudo "$@"
  fi
}

uninstall() {
  if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "$LINK_SOURCE" ]; then
    as_root rm -f "$TARGET"
    echo "Removed $TARGET"
  elif [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    echo "Left $TARGET in place (it does not point at $LINK_SOURCE)" >&2
  else
    echo "Nothing to remove at $TARGET"
  fi
  if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "Removed $VENV_DIR"
  fi
}

case "${1:-}" in
  ""|--dev) ;;
  -h|--help) usage; exit 0 ;;
  --uninstall) uninstall; exit 0 ;;
  *)
    echo "error: unknown argument: $1" >&2
    usage >&2
    exit 1
    ;;
esac

if [ -z "${PYTHON:-}" ]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON=python3.12
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "error: python3 not found. Install Python >= 3.9." >&2
    exit 1
  fi
fi
command -v "$PYTHON" >/dev/null 2>&1 \
  || { echo "error: $PYTHON not found. Install Python >= 3.9." >&2; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "error: Python >= 3.9 required (found $("$PYTHON" -V 2>&1))." >&2; exit 1; }

echo "==> Creating virtualenv at $VENV_DIR"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi

echo "==> Installing webpthumb"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
if [ "${1:-}" = "--dev" ]; then
  "$VENV_DIR/bin/python" -m pip install --quiet -e "$PROJECT_DIR[dev]"
else
  "$VENV_DIR/bin/python" -m pip install --quiet -e "$PROJECT_DIR"
fi

if [ ! -x "$LINK_SOURCE" ]; then
  echo "error: $LINK_SOURCE was not created" >&2
  exit 1
fi

echo "==> Linking $TARGET -> $LINK_SOURCE"
as_root mkdir -p "$BIN_DIR"
as_root ln -sf "$LINK_SOURCE" "$TARGET"

"$TARGET" --help >/dev/null

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "warning: $BIN_DIR is not on your PATH. Add this to your shell profile:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac
echo "Done. Try: $CMD_NAME --help"
