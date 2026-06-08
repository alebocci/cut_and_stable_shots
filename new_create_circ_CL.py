#!/usr/bin/env python3
"""
generate_and_cut.py
===================
Generate meaningful quantum circuits from the literature, run PennyLane's
automatic circuit cutter (KaHyPar-based), validate that the resulting
fragments satisfy user-specified constraints, and produce:

  1. A pickle file  : valid_circuits.pkl   — list of dicts with full info
  2. A CSV table    : summary_table.csv    — human-readable summary
  3. Console output : pretty-printed table

Constraints checked
-------------------
  * >= 2 subcircuits (fragments)
  * every fragment has between 2 and n-1 qubits (inclusive)
  * every fragment has <= max_fragment_qubits (default 30) qubits
  * total number of variations (= 4^num_cuts) <= max_variations (default 30)

Circuit families implemented (all from the literature)
------------------------------------------------------
  1. QFT  – Quantum Fourier Transform
  2. QAOA – Approximate optimisation for MaxCut on random regular graphs
  3. HEA  – Hardware-efficient ansatz with brick-layer entanglement
  4. Clifford+T – Random circuits in the standard universal gate set
  5. Trotter-Ising – Trotterised 1-D transverse-field Ising simulation
  6. QuantumVolume – QV-style random SU(4) layers

Each family builds a list of PennyLane operations which are then wrapped
into a QuantumTape and handed to PennyLane's `qcut` low-level API for
automatic partitioning via KaHyPar.

Requirements
------------
  pip install pennylane kahypar networkx

Usage
-----
  python new_create_circ_CL.py --n 10 --k 20 [--seed 42] \\
      [--max_frag_qubits 30] [--max_variations 30] [--timeout 30]
"""

import argparse
import csv
import itertools
import math
import os
import pickle
import signal
import sys
import time
from typing import List, Tuple, Callable, Optional, Dict, Any

import numpy as np
import pennylane as qml


# ════════════════════════════════════════════════════════════════════════
#  Timeout helper (UNIX only; on Windows this is a no-op)
# ════════════════════════════════════════════════════════════════════════
class CutTimeoutError(Exception):
    pass


class timeout_context:
    """Context manager that raises CutTimeoutError after `seconds`."""
    def __init__(self, seconds: int):
        self.seconds = seconds

    def _handler(self, signum, frame):
        raise CutTimeoutError(f"Timed out after {self.seconds}s")

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            self.old = signal.signal(signal.SIGALRM, self._handler)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *args):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self.old)


# ════════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════════

def _random_partition(n: int, rng: np.random.Generator):
    """Split 0..n-1 into two groups each of size >= 2."""
    size_a = int(rng.integers(2, n - 1))   # [2, n-2]
    qubits = list(range(n))
    rng.shuffle(qubits)
    return sorted(qubits[:size_a]), sorted(qubits[size_a:])


# ════════════════════════════════════════════════════════════════════════
#  Circuit families
#  Each returns (ops: List[qml.Operation], meas: List[qml.measurements])
#  *** Only a SINGLE expectation value is supported by cut_circuit ***
# ════════════════════════════════════════════════════════════════════════

def build_qft(n, rng):
    """Quantum Fourier Transform — long-range CPhase gates."""
    ops = []
    for i in range(n):
        ops.append(qml.Hadamard(wires=i))
        for j in range(i + 1, n):
            angle = math.pi / (2 ** (j - i))
            ops.append(qml.ControlledPhaseShift(angle, wires=[j, i]))
    for i in range(n // 2):
        ops.append(qml.SWAP(wires=[i, n - 1 - i]))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


def build_qaoa(n, rng, p=2):
    """QAOA for MaxCut on a random ≈3-regular graph."""
    edges = []
    deg = [0] * n
    cands = list(itertools.combinations(range(n), 2))
    rng.shuffle(cands)
    for u, v in cands:
        if deg[u] < 3 and deg[v] < 3:
            edges.append((int(u), int(v)))
            deg[u] += 1
            deg[v] += 1

    ops = []
    for _ in range(p):
        gamma = float(rng.uniform(0, 2 * math.pi))
        beta  = float(rng.uniform(0, math.pi))
        for u, v in edges:
            ops.append(qml.CNOT(wires=[u, v]))
            ops.append(qml.RZ(gamma, wires=v))
            ops.append(qml.CNOT(wires=[u, v]))
        for i in range(n):
            ops.append(qml.RX(2 * beta, wires=i))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


def build_hea(n, rng, layers=3):
    """Hardware-efficient ansatz with sparse cross-group bridges."""
    ga, gb = _random_partition(n, rng)
    ops = []
    for _ in range(layers):
        for i in range(n):
            ops.append(qml.RY(float(rng.uniform(0, 2 * math.pi)), wires=i))
            ops.append(qml.RZ(float(rng.uniform(0, 2 * math.pi)), wires=i))
        for i in range(len(ga) - 1):
            ops.append(qml.CNOT(wires=[ga[i], ga[i + 1]]))
        for i in range(len(gb) - 1):
            ops.append(qml.CNOT(wires=[gb[i], gb[i + 1]]))
        # 1-2 sparse bridges per layer → easy to cut
        nb = int(rng.integers(1, 3))
        for _ in range(nb):
            c = int(rng.choice(ga))
            t = int(rng.choice(gb))
            ops.append(qml.CNOT(wires=[c, t]))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


def build_clifford_t(n, rng, depth=20):
    """Random Clifford+T circuit with bipartite structure."""
    ga, gb = _random_partition(n, rng)
    single_fns = [qml.Hadamard, qml.S, qml.T, qml.PauliX, qml.PauliZ]
    ops = []
    for _ in range(depth):
        gate_fn = rng.choice(single_fns)
        q = int(rng.integers(0, n))
        ops.append(gate_fn(wires=q))

        if rng.random() < 0.8:
            group = ga if (rng.random() < 0.5 and len(ga) >= 2) else gb
            if len(group) >= 2:
                pair = rng.choice(len(group), size=2, replace=False)
                ops.append(qml.CNOT(wires=[group[int(pair[0])],
                                           group[int(pair[1])]]))
        else:
            c = int(rng.choice(ga))
            t = int(rng.choice(gb))
            ops.append(qml.CNOT(wires=[c, t]))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


def build_trotter_ising(n, rng, steps=3):
    """Trotterised 1-D transverse-field Ising — weakened middle bond."""
    J  = float(rng.uniform(0.5, 1.5))
    h  = float(rng.uniform(0.5, 1.5))
    dt = 0.3
    cut_bond = n // 2

    ops = [qml.Hadamard(wires=i) for i in range(n)]
    for _ in range(steps):
        for i in range(n - 1):
            angle = -2 * J * dt
            if i == cut_bond:
                angle *= 0.3       # weaken → cutter prefers to cut here
            ops.append(qml.CNOT(wires=[i, i + 1]))
            ops.append(qml.RZ(angle, wires=i + 1))
            ops.append(qml.CNOT(wires=[i, i + 1]))
        for i in range(n):
            ops.append(qml.RX(-2 * h * dt, wires=i))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


def build_qv(n, rng, depth=None):
    """Quantum-Volume-style circuit with sparse inter-partition coupling."""
    if depth is None:
        depth = n
    ga, gb = _random_partition(n, rng)
    ops = []

    def su4(w0, w1):
        for w in (w0, w1):
            ops.append(qml.RY(float(rng.uniform(0, 2 * math.pi)), wires=w))
            ops.append(qml.RZ(float(rng.uniform(0, 2 * math.pi)), wires=w))
        ops.append(qml.CNOT(wires=[w0, w1]))
        for w in (w0, w1):
            ops.append(qml.RY(float(rng.uniform(0, 2 * math.pi)), wires=w))
            ops.append(qml.RZ(float(rng.uniform(0, 2 * math.pi)), wires=w))
        ops.append(qml.CNOT(wires=[w1, w0]))
        ops.append(qml.RY(float(rng.uniform(0, 2 * math.pi)), wires=w0))

    for _ in range(depth):
        for group in (ga, gb):
            idxs = list(range(len(group)))
            rng.shuffle(idxs)
            for i in range(0, len(idxs) - 1, 2):
                su4(group[idxs[i]], group[idxs[i + 1]])
        if rng.random() < 0.3:
            c = int(rng.choice(ga))
            t = int(rng.choice(gb))
            ops.append(qml.CNOT(wires=[c, t]))
    meas = [qml.expval(qml.PauliZ(0))]
    return ops, meas


FAMILIES: List[Tuple[str, Callable]] = [
    #("QFT",           build_qft),
    #("QAOA-MaxCut",   build_qaoa),
    #("HEA",           build_hea),
    ("Clifford+T",    build_clifford_t),
    #("Trotter-Ising", build_trotter_ising),
    #("QuantumVolume", build_qv),
]


# ════════════════════════════════════════════════════════════════════════
#  Core: build tape → auto-cut → extract fragment info
# ════════════════════════════════════════════════════════════════════════

def try_cut_circuit(
    ops, meas, n: int, max_frag_qubits: int, cut_timeout: int
) -> Optional[Dict[str, Any]]:
    """
    Run PennyLane's low-level qcut pipeline:
      tape → graph → find_and_place_cuts → replace_wire_cut_nodes
      → fragment_graph → inspect fragments.

    Returns dict with fragment info, or None on any failure.
    """
    tape  = qml.tape.QuantumTape(ops, meas)
    graph = qml.qcut.tape_to_graph(tape)

    # CutStrategy: max fragment width = min(max_frag_qubits, n-1)
    effective_max = min(max_frag_qubits, n - 1)
    strategy = qml.qcut.CutStrategy(
        max_free_wires=effective_max,
        num_fragments_probed=(2, min(6, n)),
    )

    # ── Step 1: find cuts ─────────────────────────────────────────────
    try:
        with timeout_context(cut_timeout):
            cut_graph = qml.qcut.find_and_place_cuts(
                graph=graph,
                cut_strategy=strategy,
            )
    except (CutTimeoutError, Exception):
        return None

    # ── Step 2: replace WireCut → MeasureNode + PrepareNode ──────────
    try:
        qml.qcut.replace_wire_cut_nodes(cut_graph)
    except Exception:
        return None

    # ── Step 3: fragment ──────────────────────────────────────────────
    try:
        fragments, comm_graph = qml.qcut.fragment_graph(cut_graph)
    except Exception:
        return None

    if len(fragments) < 2:
        return None

    # ── Step 4: inspect fragments ─────────────────────────────────────
    fragment_qubit_counts = []
    frag_details = []
    for f in fragments:
        try:
            ft = qml.qcut.graph_to_tape(f)
        except Exception:
            return None
        frag_wires = set(ft.wires)
        n_measure = sum(1 for op in ft.operations
                        if isinstance(op, qml.qcut.MeasureNode))
        n_prepare = sum(1 for op in ft.operations
                        if isinstance(op, qml.qcut.PrepareNode))
        fragment_qubit_counts.append(len(frag_wires))
        frag_details.append({
            "qubits": len(frag_wires),
            "measure_nodes": n_measure,
            "prepare_nodes": n_prepare,
        })

    num_cuts = comm_graph.number_of_edges()
    # Per Peng et al., total circuit evaluations = 4^(number of wire cuts)
    total_variations = 4 ** num_cuts

    return {
        "num_fragments":        len(fragments),
        "fragment_qubit_counts": fragment_qubit_counts,
        "fragment_details":      frag_details,
        "num_cuts":              num_cuts,
        "total_variations":      total_variations,
    }


# ════════════════════════════════════════════════════════════════════════
#  Generation loop with constraint validation
# ════════════════════════════════════════════════════════════════════════

def generate_and_validate(
    n: int,
    k: int,
    seed: int             = 42,
    max_frag_qubits: int  = 30,
    max_variations: int   = 30,
    cut_timeout: int      = 30,
) -> List[Dict[str, Any]]:

    if n < 4:
        raise ValueError("n must be >= 4 so subcircuits can have 2..n-1 qubits")
    if n > 31:
        raise ValueError("n must be <= 31 so max fragment size <= 30")

    rng = np.random.default_rng(seed)
    valid   = []
    attempt = 0
    budget  = k * 20      # generous retry budget

    print(f"\n{'='*72}")
    print(f"  Generating up to {k} valid cuttable circuits on {n} qubits")
    print(f"  Fragment qubits ∈ [2, {n-1}] (cap {max_frag_qubits})")
    print(f"  Max variations (4^cuts) ≤ {max_variations}")
    print(f"  Seed={seed}  Timeout={cut_timeout}s per attempt")
    print(f"{'='*72}\n")

    while len(valid) < k and attempt < budget:
        fam_name, fam_fn = FAMILIES[attempt % len(FAMILIES)]
        c_rng = np.random.default_rng(int(rng.integers(0, 2**31)))
        attempt += 1
        tag = f"{fam_name}_{attempt:03d}"

        # Build
        try:
            ops, meas = fam_fn(n, c_rng)
        except Exception as exc:
            print(f"  [{tag}] build error: {exc}")
            continue

        tape = qml.tape.QuantumTape(ops, meas)
        ng  = len(tape.operations)
        n2q = sum(1 for op in tape.operations if len(op.wires) >= 2)
        print(f"  [{tag}]  gates={ng:>4}  2q={n2q:>3}  ...", end="", flush=True)

        t0 = time.time()
        res = try_cut_circuit(ops, meas, n, max_frag_qubits, cut_timeout)
        dt = time.time() - t0

        if res is None:
            print(f"  FAIL ({dt:.1f}s)")
            continue

        fq = res["fragment_qubit_counts"]
        nf = res["num_fragments"]
        tv = res["total_variations"]

        # Constraint checks
        reason = None
        if nf < 2:
            reason = f"only {nf} fragment"
        elif any(q < 2 or q > n - 1 for q in fq):
            reason = f"frag sizes {fq} ∉ [2,{n-1}]"
        elif any(q > max_frag_qubits for q in fq):
            reason = f"frag sizes {fq} exceed {max_frag_qubits}"
        elif tv > max_variations:
            reason = f"variations={tv} > {max_variations}"

        if reason:
            print(f"  SKIP ({reason}, {dt:.1f}s)")
            continue

        entry = dict(
            id              = len(valid) + 1,
            tag             = tag,
            family          = fam_name,
            n_qubits        = n,
            total_gates     = ng,
            two_qubit_gates = n2q,
            num_fragments   = nf,
            fragment_sizes  = fq,
            fragment_details= res["fragment_details"],
            num_cuts        = res["num_cuts"],
            total_variations= tv,
            cut_time_s      = round(dt, 3),
            ops             = ops,       # original PennyLane ops
            meas            = meas,
        )
        valid.append(entry)
        print(f"  ✓  frags={nf} sizes={fq} cuts={res['num_cuts']} "
              f"vars={tv} ({dt:.1f}s)")

    print(f"\n  Result: {len(valid)}/{k} valid circuits "
          f"({attempt} attempts)\n")
    return valid


# ════════════════════════════════════════════════════════════════════════
#  Save: pickle + CSV + console table
# ════════════════════════════════════════════════════════════════════════

def save_results(circuits: List[Dict], out_dir: str = ".", output_file: str = "valid_circuits.pkl"):
    if not circuits:
        print("  ⚠ No valid circuits found — nothing to save.")
        return

    os.makedirs(out_dir, exist_ok=True)

    # ── 1. Pickle (full data including ops/meas for reuse) ────────────
    pkl = os.path.join(out_dir, output_file)
    with open(pkl, "wb") as f:
        pickle.dump(circuits, f)
    print(f"  Saved full data      → {pkl}")

    # ── 2. CSV summary ────────────────────────────────────────────────
    csv_file = output_file.rsplit(".", 1)[0] + "_summary.csv"
    csv_path = os.path.join(out_dir, csv_file)
    fields = [
        "id", "family", "n_qubits", "total_gates", "two_qubit_gates",
        "num_fragments", "fragment_sizes", "num_cuts",
        "total_variations", "cut_time_s",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in circuits:
            row = {k: c[k] for k in fields}
            row["fragment_sizes"] = str(c["fragment_sizes"])
            w.writerow(row)
    print(f"  Saved summary table  → {csv_path}")

    # ── 3. Console table ──────────────────────────────────────────────
    hdr = (f"{'ID':>3}  {'Family':<16} {'n':>3} {'Gates':>5} "
           f"{'2Q':>4} {'Frags':>5} {'Fragment sizes':<22} "
           f"{'Cuts':>4} {'4^cuts':>6} {'Time':>7}")
    sep = "─" * len(hdr)
    print(f"\n{sep}")
    print(hdr)
    print(sep)
    for c in circuits:
        print(f"{c['id']:>3}  {c['family']:<16} {c['n_qubits']:>3} "
              f"{c['total_gates']:>5} {c['two_qubit_gates']:>4} "
              f"{c['num_fragments']:>5} {str(c['fragment_sizes']):<22} "
              f"{c['num_cuts']:>4} {c['total_variations']:>6} "
              f"{c['cut_time_s']:>7.2f}")
    print(sep)
    print()


# ════════════════════════════════════════════════════════════════════════
#  CLI entry-point
# ════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Generate cuttable quantum circuits & validate constraints."
    )
    p.add_argument("--n", type=int, required=True,
                   help="Number of qubits  (4 ≤ n ≤ 31)")
    p.add_argument("--k", type=int, required=True,
                   help="Number of valid circuits desired")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max_frag_qubits", type=int, default=30,
                   help="Max qubits per fragment (default 30)")
    p.add_argument("--max_variations", type=int, default=30,
                   help="Max total variations 4^(#cuts) allowed (default 30)")
    p.add_argument("--timeout", type=int, default=30,
                   help="Seconds per auto-cut attempt (default 30)")
    p.add_argument("--outdir", type=str, default=".",
                   help="Output directory")
    p.add_argument("--output_file", type=str, default="valid_circuits.pkl",
                   help="Output file name for the pickle (default valid_circuits.pkl)")
    args = p.parse_args()

    circuits = generate_and_validate(
        n               = args.n,
        k               = args.k,
        seed            = args.seed,
        max_frag_qubits = args.max_frag_qubits,
        max_variations  = args.max_variations,
        cut_timeout     = args.timeout,
    )

    save_results(circuits, out_dir=args.outdir, output_file=args.output_file)

    # ── Reuse instructions ────────────────────────────────────────────
    print("=" * 72)
    print("  HOW TO RELOAD")
    print("=" * 72)
    print("""
  import pickle, pennylane as qml
  from functools import partial

  with open(args.output_file, "rb") as f:
      circuits = pickle.load(f)

  c   = circuits[0]
  dev = qml.device("default.qubit", wires=c["n_qubits"])

  @partial(qml.cut_circuit, auto_cutter=True)
  @qml.qnode(dev)
  def run():
      for op in c["ops"]:
          qml.apply(op)
      return c["meas"][0]

  print(run())
""")


if __name__ == "__main__":
    main()