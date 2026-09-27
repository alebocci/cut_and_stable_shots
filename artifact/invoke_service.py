#!/usr/bin/env python3
"""Invoke the paper's service-like Cut -> Control -> Execute -> Sew workflow."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "src" / "main.py"
DEFAULT_REQUEST = ROOT / "artifact" / "requests" / "smoke.json"
ALLOWED_MODES = {
    "noisy_vanilla",
    "cut_divided_budget",
    "cut_qubit_prop",
    "cut_incremental_budget",
    "cut_incremental_qubit_prop",
}


def repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def build_command(request: dict[str, object], output_override: Path | None) -> list[str]:
    circuit = request["circuit"]
    controller = request["adaptive_controller"]
    if not isinstance(circuit, dict) or not isinstance(controller, dict):
        raise ValueError("'circuit' and 'adaptive_controller' must be JSON objects")

    modes = request.get("modes", [])
    if not isinstance(modes, list) or not modes or not set(modes).issubset(ALLOWED_MODES):
        raise ValueError(f"'modes' must be a non-empty subset of {sorted(ALLOWED_MODES)}")

    dataset = repo_path(str(circuit["dataset"]))
    config = repo_path(str(controller["config"]))
    output = output_override or repo_path(str(request["response_directory"]))
    for label, path in (("circuit dataset", dataset), ("controller config", config)):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")

    budget = int(request["maximum_shot_budget"])
    if budget <= 0:
        raise ValueError("'maximum_shot_budget' must be positive")

    command = [
        sys.executable,
        str(RUNNER),
        "--circuits-pkl",
        str(dataset),
        "--circuit-index",
        str(int(circuit.get("index", 0))),
        "--shots",
        str(budget),
        "--noisy-backend",
        str(request["backend"]),
        "--seed-simulator",
        str(int(request.get("seed", 42))),
        "--modes",
        *[str(mode) for mode in modes],
        "--incremental-config",
        str(config),
        "--parallel-circuits",
        "1",
        "--output-dir",
        str(output),
    ]
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--output", type=Path, help="override response_directory from the request")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the delegated command")
    args = parser.parse_args()

    try:
        request_path = args.request if args.request.is_absolute() else ROOT / args.request
        request = json.loads(request_path.read_text(encoding="utf-8"))
        command = build_command(request, args.output)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print("Service stages: Cut -> Allocate -> StableShots batch/check/stop -> Execute -> Sew")
    print("Delegated command:", shlex.join(command))
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
