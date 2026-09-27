#!/usr/bin/env python3
"""Generate or verify the deterministic SHA-256 manifest for release files."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", "reproduced", "artifact-output"}
LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")


def release_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        if relative == MANIFEST.relative_to(ROOT):
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if relative.suffix == ".pyc":
            continue
        path = ROOT / relative
        if path.is_file() and not path.is_symlink():
            paths.append(relative)
    return sorted(set(paths), key=lambda value: value.as_posix())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entries() -> list[tuple[str, str]]:
    return [(sha256(ROOT / path), path.as_posix()) for path in release_files()]


def write_manifest() -> None:
    values = entries()
    MANIFEST.write_text("".join(f"{digest}  {path}\n" for digest, path in values), encoding="utf-8")
    print(f"Wrote {MANIFEST.relative_to(ROOT)} with {len(values):,} entries")


def check_manifest() -> int:
    try:
        lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Manifest verification: FAIL ({exc})", file=sys.stderr)
        return 1
    expected = []
    for number, line in enumerate(lines, start=1):
        match = LINE.fullmatch(line)
        if not match:
            print(f"Manifest verification: FAIL (invalid line {number})", file=sys.stderr)
            return 1
        expected.append((match.group(1), match.group(2)))
    actual = entries()
    if expected != actual:
        expected_paths = {path for _, path in expected}
        actual_paths = {path for _, path in actual}
        missing = sorted(expected_paths - actual_paths)
        added = sorted(actual_paths - expected_paths)
        expected_by_path = {path: digest for digest, path in expected}
        actual_by_path = {path: digest for digest, path in actual}
        changed = sorted(
            path
            for path in expected_paths & actual_paths
            if expected_by_path[path] != actual_by_path[path]
        )
        print("Manifest verification: FAIL", file=sys.stderr)
        if missing:
            print(f"  missing files: {', '.join(missing[:5])}", file=sys.stderr)
        if added:
            print(f"  unlisted files: {', '.join(added[:5])}", file=sys.stderr)
        if changed:
            print(f"  changed files: {', '.join(changed[:5])}", file=sys.stderr)
        return 1
    print(f"Manifest verification: PASS ({len(actual):,} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify instead of rewriting the manifest")
    args = parser.parse_args()
    if args.check:
        return check_manifest()
    write_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
