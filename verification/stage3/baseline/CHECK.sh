#!/bin/sh
set -eu
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PROJECT/.venv/bin/python" "$PROJECT/verification/check.py" "${1:-modified}"
