#!/usr/bin/env python3
"""Run the 84-cell paper campaign without performing any Git operations."""

from __future__ import annotations

import argparse
import concurrent.futures
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ("torino", "sherbrooke", "kawasaki", "kyoto")
MODES = (
    "noisy_vanilla",
    "cut_divided_budget",
    "cut_qubit_prop",
    "cut_incremental_budget",
    "cut_incremental_qubit_prop",
)


def command_for(qubits: int, budget: int, backend: str, output_root: Path) -> tuple[list[str], Path]:
    output = output_root / f"{budget}_shots" / backend / f"{qubits}_qubits"
    log = output_root / "_logs" / f"q{qubits}_{budget}_{backend}.log"
    command = [
        sys.executable,
        str(ROOT / "src" / "main.py"),
        "--circuits-pkl",
        str(ROOT / "data" / "circuits" / "paper" / f"clif{qubits}.pkl"),
        "--shots",
        str(budget),
        "--noisy-backend",
        f"aer.fake_{backend}",
        "--seed-simulator",
        "42",
        "--modes",
        *MODES,
        "--incremental-config",
        str(ROOT / "configs" / "stable_shots3.json"),
        "--parallel-circuits",
        "1",
        "--output-dir",
        str(output),
    ]
    return command, log


def execute(job: tuple[list[str], Path]) -> tuple[Path, int]:
    command, log = job
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return log, completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "reproduced" / "paper-campaign")
    parser.add_argument("--jobs", type=int, default=1, help="concurrent campaign cells; each may need about 7 GB RAM")
    parser.add_argument("--qubits", type=int, nargs="+", default=list(range(10, 17)))
    parser.add_argument("--budgets", type=int, nargs="+", default=[5000, 10000, 20000])
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if any(qubits not in range(10, 17) for qubits in args.qubits):
        parser.error("paper circuits use 10 through 16 qubits")
    if any(budget <= 0 for budget in args.budgets):
        parser.error("all shot budgets must be positive")

    runner = ROOT / "src" / "main.py"
    config = ROOT / "configs" / "stable_shots3.json"
    if not runner.is_file() or not config.is_file():
        parser.error("required runner or paper configuration is missing")
    missing_inputs = [
        ROOT / "data" / "circuits" / "paper" / f"clif{qubits}.pkl"
        for qubits in args.qubits
        if not (ROOT / "data" / "circuits" / "paper" / f"clif{qubits}.pkl").is_file()
    ]
    if missing_inputs:
        parser.error(f"missing circuit input: {missing_inputs[0]}")

    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root

    jobs = [
        command_for(qubits, budget, backend, output_root.resolve())
        for budget in args.budgets
        for backend in args.backends
        for qubits in args.qubits
    ]
    print(f"Campaign cells: {len(jobs)}")
    if args.dry_run:
        for command, _ in jobs:
            print(shlex.join(command))
        return 0

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for log, returncode in executor.map(execute, jobs):
            status = "PASS" if returncode == 0 else f"FAIL ({returncode})"
            print(f"{status}: {log}", flush=True)
            failures += returncode != 0
    print(f"Completed {len(jobs)} cells; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
