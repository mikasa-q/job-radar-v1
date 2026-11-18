#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=$(CDPATH= cd -- "$HERE/../.." && pwd)
PYTHON="$PROJECT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" "$HERE/rollback.py" "${1:-$PROJECT}"
