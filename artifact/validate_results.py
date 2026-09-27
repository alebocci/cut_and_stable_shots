#!/usr/bin/env python3
"""Validate the archived ICSoC 2026 result table using only the stdlib."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "analysis" / "data" / "paper_results.csv"
PAPER_CONFIG = ROOT / "configs" / "stable_shots3.json"
EXPECTED_CONFIG = {
    "stopping_criterion": "delta",
    "distance_metric": "tvd",
    "threshold": 0.05,
    "offset": 3,
    "stability_k": 5,
    "batch_shots": 50,
}

MODES = (
    "noisy_vanilla",
    "cut_divided_budget",
    "cut_qubit_prop",
    "cut_incremental_budget",
    "cut_incremental_qubit_prop",
)
BACKENDS = (
    "aer.fake_kawasaki",
    "aer.fake_kyoto",
    "aer.fake_sherbrooke",
    "aer.fake_torino",
)
BUDGETS = (5000, 10000, 20000)
QUBITS = tuple(range(10, 17))
PAIRS = (
    ("cut_divided_budget", "cut_incremental_budget", "SS-Sub / CC-Sub"),
    ("cut_qubit_prop", "cut_incremental_qubit_prop", "SS-Qub / CC-Qub"),
)


def percentile(values: list[float], probability: float) -> float:
    """Return the linearly interpolated percentile (NumPy's default method)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_rows(path: Path) -> list[dict[str, object]]:
    required = {
        "circuit_name",
        "circuit_qubits",
        "observable",
        "mode",
        "budget",
        "backend",
        "absolute_error",
        "shots_executed",
    }
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
        rows: list[dict[str, object]] = []
        for line_number, raw in enumerate(reader, start=2):
            try:
                rows.append(
                    {
                        **raw,
                        "circuit_qubits": int(raw["circuit_qubits"]),
                        "budget": int(float(raw["budget"])),
                        "absolute_error": float(raw["absolute_error"]),
                        "shots_executed": int(float(raw["shots_executed"])),
                    }
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value on CSV line {line_number}: {exc}") from exc
    return rows


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def mean_for(rows: list[dict[str, object]], field: str) -> float:
    return statistics.fmean(float(row[field]) for row in rows)


def validate(rows: list[dict[str, object]]) -> dict[str, object]:
    require(len(rows) == 4200, f"expected 4,200 rows, found {len(rows):,}")
    require({str(row["mode"]) for row in rows} == set(MODES), "unexpected mode coverage")
    require({str(row["backend"]) for row in rows} == set(BACKENDS), "unexpected backend coverage")
    require({int(row["budget"]) for row in rows} == set(BUDGETS), "unexpected budget coverage")
    require({int(row["circuit_qubits"]) for row in rows} == set(QUBITS), "unexpected qubit coverage")

    mode_counts = Counter(str(row["mode"]) for row in rows)
    require(all(mode_counts[mode] == 840 for mode in MODES), f"unbalanced modes: {mode_counts}")

    unique_circuits = {
        (int(row["circuit_qubits"]), str(row["circuit_name"])) for row in rows
    }
    require(len(unique_circuits) == 70, f"expected 70 circuits, found {len(unique_circuits)}")
    circuits_by_size = Counter(int(row["circuit_qubits"]) for row in rows if str(row["mode"]) == MODES[0] and int(row["budget"]) == BUDGETS[0] and str(row["backend"]) == BACKENDS[0])
    require(all(circuits_by_size[q] == 10 for q in QUBITS), f"expected 10 circuits per qubit size: {circuits_by_size}")

    keys = [
        (
            int(row["circuit_qubits"]),
            str(row["circuit_name"]),
            str(row["backend"]),
            int(row["budget"]),
            str(row["mode"]),
        )
        for row in rows
    ]
    require(len(set(keys)) == len(keys), "duplicate circuit/backend/budget/mode rows found")

    observations_per_circuit = Counter(
        (int(row["circuit_qubits"]), str(row["circuit_name"])) for row in rows
    )
    require(all(count == 60 for count in observations_per_circuit.values()), "each circuit must have 4 backends x 3 budgets x 5 modes")

    cells = Counter(
        (int(row["circuit_qubits"]), str(row["backend"]), int(row["budget"]))
        for row in rows
    )
    require(len(cells) == 84, f"expected 84 campaign cells, found {len(cells)}")
    require(all(count == 50 for count in cells.values()), "each campaign cell must contain 10 circuits x 5 modes")

    for row in rows:
        shots = int(row["shots_executed"])
        budget = int(row["budget"])
        mode = str(row["mode"])
        require(0 < shots <= budget, f"invalid shot count for {mode}: {shots}/{budget}")
        if mode in {"noisy_vanilla", "cut_divided_budget", "cut_qubit_prop"}:
            require(shots == budget, f"fixed-budget mode did not consume its budget: {mode}")
        expected_observable = "Z" + "I" * (int(row["circuit_qubits"]) - 1)
        require(str(row["observable"]) == expected_observable, f"unexpected observable for {row['circuit_name']}")

    by_mode: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_mode[str(row["mode"])].append(row)

    grouped: dict[tuple[int, int, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["circuit_qubits"]), int(row["budget"]), str(row["mode"]))].append(row)

    group_errors = {key: mean_for(group, "absolute_error") for key, group in grouped.items()}
    group_savings = {
        key: statistics.fmean(
            100.0 * (int(row["budget"]) - int(row["shots_executed"])) / int(row["budget"])
            for row in group
        )
        for key, group in grouped.items()
        if key[2].startswith("cut_incremental")
    }

    pair_summaries = []
    for fixed, adaptive, label in PAIRS:
        fixed_errors = [group_errors[(q, budget, fixed)] for q in QUBITS for budget in BUDGETS]
        adaptive_errors = [group_errors[(q, budget, adaptive)] for q in QUBITS for budget in BUDGETS]
        # The paper's paired error test first averages the three budgets within
        # each qubit size, leaving seven paired observations. Savings remain
        # summarized over all 21 qubit-size/budget groups.
        differences = [
            statistics.fmean(group_errors[(q, budget, adaptive)] for budget in BUDGETS)
            - statistics.fmean(group_errors[(q, budget, fixed)] for budget in BUDGETS)
            for q in QUBITS
        ]
        savings = [group_savings[(q, budget, adaptive)] for q in QUBITS for budget in BUDGETS]
        pair_summaries.append(
            {
                "label": label,
                "fixed_error": statistics.fmean(fixed_errors),
                "adaptive_error": statistics.fmean(adaptive_errors),
                "median_error_delta": statistics.median(differences),
                "median_savings": statistics.median(savings),
                "savings_q1": percentile(savings, 0.25),
                "savings_q3": percentile(savings, 0.75),
            }
        )

    savings_by_budget = {}
    for budget in BUDGETS:
        values = [
            100.0 * (budget - int(row["shots_executed"])) / budget
            for row in rows
            if int(row["budget"]) == budget and str(row["mode"]).startswith("cut_incremental")
        ]
        savings_by_budget[budget] = statistics.fmean(values)

    adaptive_rows = [row for row in rows if str(row["mode"]).startswith("cut_incremental")]
    overall_savings = statistics.fmean(
        100.0 * (int(row["budget"]) - int(row["shots_executed"])) / int(row["budget"])
        for row in adaptive_rows
    )
    peak_group = max(group_savings.items(), key=lambda item: item[1])

    # Paper-level sanity bounds: intentionally broad enough to tolerate display rounding.
    require(overall_savings > 25.0, "paper claim of >25% average adaptive savings was not reproduced")
    require(6.0 <= savings_by_budget[5000] <= 8.5, "5k-budget savings are outside the reported range")
    require(30.0 <= savings_by_budget[10000] <= 34.0, "10k-budget savings are outside the reported range")
    require(54.0 <= savings_by_budget[20000] <= 58.0, "20k-budget savings are outside the reported range")
    require(63.0 <= peak_group[1] <= 68.5, "peak grouped savings are outside the reported range")

    return {
        "mode_counts": mode_counts,
        "unique_circuits": len(unique_circuits),
        "campaign_cells": len(cells),
        "mean_error_by_mode": {mode: mean_for(by_mode[mode], "absolute_error") for mode in MODES},
        "overall_savings": overall_savings,
        "savings_by_budget": savings_by_budget,
        "peak_group": peak_group,
        "pairs": pair_summaries,
    }


def print_report(path: Path, report: dict[str, object]) -> None:
    print("StableShots ICSoC 2026 artifact validation: PASS")
    print(f"Dataset: {path}")
    print(f"Coverage: {report['unique_circuits']} circuits x 4 backends x 3 budgets x 5 modes = 4,200 rows")
    print("Mean absolute error by mode:")
    for mode, value in report["mean_error_by_mode"].items():
        print(f"  {mode:31s} {value:.6f}")
    print(f"Adaptive mean shot savings: {report['overall_savings']:.2f}%")
    print("Adaptive mean shot savings by budget:")
    for budget, value in report["savings_by_budget"].items():
        print(f"  {budget:5d}: {value:.2f}%")
    (qubits, budget, mode), value = report["peak_group"]
    print(f"Peak grouped savings: {value:.2f}% ({qubits} qubits, {budget} shots, {mode})")
    print("Matched policy summaries (error means/savings: 21 groups; median delta: 7 size aggregates):")
    for pair in report["pairs"]:
        print(
            "  {label}: CC error={fixed_error:.4f}, SS error={adaptive_error:.4f}, "
            "median delta={median_error_delta:+.4f}, median savings={median_savings:.1f}% "
            "[IQR {savings_q1:.1f}-{savings_q3:.1f}%]".format(**pair)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    path = args.dataset.resolve()
    try:
        config = json.loads(PAPER_CONFIG.read_text(encoding="utf-8"))
        require(config == EXPECTED_CONFIG, f"paper configuration differs from {EXPECTED_CONFIG}")
        rows = load_rows(path)
        report = validate(rows)
    except (OSError, ValueError, AssertionError) as exc:
        print(f"StableShots ICSoC 2026 artifact validation: FAIL\n{exc}", file=sys.stderr)
        return 1
    print_report(path, report)
    print(f"Configuration: {PAPER_CONFIG} (delta/TVD, batch 50, offset 3, threshold 0.05, k=5)")
    print("Seed and runner evidence: 42 (recorded in per-cell run.log files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
