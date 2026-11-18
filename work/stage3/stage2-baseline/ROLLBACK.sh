#!/bin/sh
set -eu
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$PROJECT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" "$PROJECT/verification/rollback.py" "${1:-$PROJECT}"
