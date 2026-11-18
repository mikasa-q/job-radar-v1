"""Revert only stage-three code/docs; leave the SQLite database untouched."""
import hashlib
import json
import shutil
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def restore(target):
    manifest = json.loads((HERE / "changes.json").read_text(encoding="utf-8"))
    # Preflight every file before changing any of them.
    for relative, hashes in manifest.items():
        path = target / relative
        if digest(path) not in {hashes["before"], hashes["after"]}:
            raise SystemExit(f"File changed since verification; kept untouched: {path}")
        if hashes["before"] is not None:
            assert digest(HERE / "baseline" / relative) == hashes["before"], relative
    for relative, hashes in manifest.items():
        path = target / relative
        if hashes["before"] is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(HERE / "baseline" / relative, path)
        assert digest(path) == hashes["before"], relative
    print(f"Restored stage-two code: {len(manifest)} paths; database untouched")


if __name__ == "__main__":
    restore(Path(sys.argv[1]).resolve())
