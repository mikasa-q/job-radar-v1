#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=$(CDPATH= cd -- "$HERE/../.." && pwd)
TARGET=${1:-$PROJECT}
PYTHON="$TARGET/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$PROJECT/.venv/bin/python"
exec "$PYTHON" - "$HERE" "$TARGET" <<'PY'
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

here, target = (Path(value).resolve() for value in sys.argv[1:])
manifest = json.loads((here / "changes.json").read_text())

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

for relative, hashes in manifest.items():
    if digest(target / relative) not in {hashes["before"], hashes["after"]}:
        raise SystemExit(f"File changed since verification; kept untouched: {target / relative}")
    if hashes["before"] is not None:
        assert digest(here / "baseline" / relative) == hashes["before"], relative

# Remove only this target's editable registration, never another checkout's.
try:
    distribution = importlib.metadata.distribution("job_radar")
except importlib.metadata.PackageNotFoundError:
    distribution = None
if distribution is not None:
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    parsed = urlparse(direct.get("url", ""))
    installed_target = Path(unquote(parsed.path)).resolve() if parsed.scheme == "file" else None
    if installed_target == target and direct.get("dir_info", {}).get("editable"):
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "job_radar"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

for relative, hashes in manifest.items():
    path = target / relative
    if hashes["before"] is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(here / "baseline" / relative, path)
    assert digest(path) == hashes["before"], relative
print(f"ROLLBACK: {len(manifest)} packaging/document paths restored; matching editable install removed; source/tests/database untouched")
PY
