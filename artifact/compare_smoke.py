#!/usr/bin/env python3
"""Compare two Level B summary CSVs by matching circuit and execution settings."""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

KEY = ("circuit_name", "backend", "budget", "mode")
VALUES = ("result", "absolute_error")
REQUIRED = set(KEY + VALUES + ("shots_executed",))


def read_rows(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError(f"{path}: missing required columns: {sorted(REQUIRED - set(reader.fieldnames or []))}")
        rows = {}
        for line_number, row in enumerate(reader, start=2):
            key = tuple(str(row[name]) for name in KEY)
            if key in rows:
                raise ValueError(f"{path}: duplicate entry at line {line_number}: {key}")
            for name in VALUES:
                if not math.isfinite(float(row[name])):
                    raise ValueError(f"{path}: non-finite {name} at line {line_number}")
            int(row["shots_executed"])
            rows[key] = row
    if not rows:
        raise ValueError(f"{path}: summary has no rows")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="reference summary.csv")
    parser.add_argument("--observed", type=Path, required=True, help="newly executed summary.csv")
    parser.add_argument("--atol", type=float, default=None,
                        help="optional acceptance tolerance on both numerical values; omitted = diagnostic only")
    args = parser.parse_args()
    if args.atol is not None and (not math.isfinite(args.atol) or args.atol < 0):
        parser.error("--atol must be a finite non-negative number")
    try:
        reference = read_rows(args.reference)
        observed = read_rows(args.observed)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    missing = sorted(observed.keys() - reference.keys())
    if missing:
        print(f"Comparison: FAIL ({len(missing)} observed rows absent from reference)", file=sys.stderr)
        for key in missing[:5]:
            print(f"  missing reference: {key}", file=sys.stderr)
        return 1

    print("Matched modes | |delta(result)| | |delta(absolute_error)| | delta(shots_executed)")
    violations = 0
    for key in sorted(observed):
        a, b = reference[key], observed[key]
        deltas = [abs(float(a[name]) - float(b[name])) for name in VALUES]
        shot_delta = int(b["shots_executed"]) - int(a["shots_executed"])
        print(f"{key[3]:28} | {deltas[0]:.9g} | {deltas[1]:.9g} | {shot_delta:+d}")
        if args.atol is not None and (any(d > args.atol for d in deltas) or shot_delta != 0):
            violations += 1

    if args.atol is None:
        print(f"Comparison: DIAGNOSTIC ({len(observed)} matched rows; no acceptance tolerance applied)")
        return 0
    if violations:
        print(f"Comparison: FAIL ({violations} rows exceed atol={args.atol:g} or differ in shots)")
        return 1
    print(f"Comparison: PASS ({len(observed)} matched rows, atol={args.atol:g}, shots equal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
