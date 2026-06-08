#!/usr/bin/env python3
"""Benchmark cuttable circuits loaded from valid_circuits.pkl.

Examples
--------
1) Baseline + non-incremental cut modes:
    python main.py \
        --circuits-pkl circuits/7q.pkl \
        --shots 20000 \
        --noisy-backend aer.fake_torino \
        --seed-simulator 42 \
        --modes noisy_vanilla cut_divided_budget cut_qubit_prop \
        --parallel-circuits 5 \
        --output-dir results/benchmark_results_7qubits

2) Incremental with Delta stopping criterion (default metric: TVD):
    python main.py \
        --circuits-pkl circuits/7q.pkl \
        --shots 20000 \
        --noisy-backend aer.fake_torino \
        --seed-simulator 42 \
        --modes cut_incremental_budget \
        --incremental-stopping-criterion delta \
        --incremental-distance-metric tvd \
        --incremental-threshold 0.03 \
        --incremental-offset 2 \
        --incremental-stability-k 3 \
        --incremental-batch-shots 50 \
        --parallel-circuits 5 \
        --output-dir results/delta_run

3) Incremental with DMA stopping criterion:
    python main.py \
        --circuits-pkl circuits/7q.pkl \
        --shots 20000 \
        --noisy-backend aer.fake_torino \
        --seed-simulator 42 \
        --modes cut_incremental_budget cut_incremental_qubit_prop \
        --incremental-stopping-criterion dma \
        --incremental-distance-metric tvd \
        --incremental-threshold 0.03 \
        --incremental-offset 2 \
        --incremental-window-size 3 \
        --incremental-stability-k 3 \
        --incremental-batch-shots 50 \
        --parallel-circuits 5 \
        --output-dir results/dma_run

4) Incremental with EWMA stopping criterion and JS distance:
    python main.py \
        --circuits-pkl circuits/7q.pkl \
        --shots 20000 \
        --noisy-backend aer.fake_torino \
        --seed-simulator 42 \
        --modes cut_incremental_budget \
        --incremental-stopping-criterion ewma \
        --incremental-distance-metric js \
        --incremental-threshold 0.03 \
        --incremental-offset 2 \
        --incremental-window-size 5 \
        --incremental-ewma-alpha 0.5 \
        --incremental-stability-k 3 \
        --incremental-batch-shots 50 \
        --parallel-circuits 5 \
        --output-dir results/ewma_js_run

5) Disable the extra persistence layer and stop as soon as the chosen
   stopping criterion is satisfied once:
    python main.py \
        --circuits-pkl circuits/7q.pkl \
        --shots 20000 \
        --noisy-backend aer.fake_torino \
        --seed-simulator 42 \
        --modes cut_incremental_budget \
        --incremental-stopping-criterion delta \
        --incremental-distance-metric tvd \
        --incremental-threshold 0.03 \
        --incremental-offset 2 \
        --incremental-stability-k 1 \
        --output-dir results/no_persistence_run

Description
-----------
This script loads the circuits produced by generate_and_cut.py
(valid_circuits.pkl), converts the stored PennyLane operations to QASM,
and then runs the same benchmark / cut / sew / statistics pipeline.

Input
-----
Only one input source is supported:

    --circuits-pkl valid_circuits.pkl

The observable for loaded circuits is derived from the original generator:
    qml.expval(qml.PauliZ(0))
so the benchmark observable is always:
    "Z" + "I" * (n_qubits - 1)

Error definition
----------------
    absolute_error = | perf_exp_val - estimated_exp_val |

where perf_exp_val is the exact PennyLane statevector expectation value
of the observable on the uncut circuit.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import functools
import hashlib
import json
import logging
import math
import pickle
import platform
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pennylane as qml
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel

logger = logging.getLogger("cuttable-circuit-benchmark")

FreqCounts = Dict[str, int]


@dataclass
class CircuitSpec:
    id: int
    circuit_qasm: str
    observable: str
    n_qubits: int
    circuit_name: str
    circuit_conf: Any
    family: Optional[str] = None
    tag: Optional[str] = None
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def hash_circuit(qasm: str) -> str:
    return hashlib.md5(qasm.encode()).hexdigest()

def derive_seed(base_seed: Optional[int], *parts: Any) -> Optional[int]:
    """Derive a deterministic 32-bit simulator seed from a base seed.

    This avoids reusing the exact same Aer simulator seed across repeated
    batch executions of the same fragment/observable.

    Why this matters:
        run_counts(..., shots=938, seed=42)

    is not equivalent to:

        run_counts(..., shots=50, seed=42)
        run_counts(..., shots=50, seed=42)
        ...
        run_counts(..., shots=38, seed=42)

    because the second form repeatedly restarts the simulator RNG from the
    same seed. Incremental execution therefore needs a unique seed per batch.

    If base_seed is None, we return None and let Aer choose its own randomness.
    """
    if base_seed is None:
        return None

    payload = json.dumps(
        {
            "base_seed": int(base_seed),
            "parts": [str(p) for p in parts],
        },
        sort_keys=True,
    ).encode("utf-8")

    digest = hashlib.blake2s(payload, digest_size=4).digest()
    seed = int.from_bytes(digest, byteorder="big", signed=False)

    # Avoid seed 0 just in case a backend treats it specially.
    return seed if seed != 0 else 1

@functools.lru_cache(maxsize=None)
def parse_qasm(qasm: str) -> QuantumCircuit:
    return QuantumCircuit.from_qasm_str(qasm)


def export_qasm(circuit: QuantumCircuit) -> str:
    try:
        from qiskit import qasm2
        return qasm2.dumps(circuit)
    except Exception:
        if hasattr(circuit, "qasm"):
            return circuit.qasm()
        raise RuntimeError("Unable to export OpenQASM")


def pl_ops_to_qasm(ops: Sequence[Any], n_qubits: int) -> str:
    """Convert PennyLane ops stored in valid_circuits.pkl into Qiskit QASM.

    Adds identity gates on unused qubits so the QASM -> PennyLane import
    preserves the full declared register size.
    """
    qc = QuantumCircuit(n_qubits)
    used_wires = set()

    for op in ops:
        name = op.name
        wires = [int(w) for w in op.wires]
        params = [float(p) for p in op.parameters]

        used_wires.update(wires)

        if name == "Hadamard":
            qc.h(wires[0])
        elif name == "ControlledPhaseShift":
            qc.cp(params[0], wires[0], wires[1])
        elif name == "SWAP":
            qc.swap(wires[0], wires[1])
        elif name == "CNOT":
            qc.cx(wires[0], wires[1])
        elif name == "RX":
            qc.rx(params[0], wires[0])
        elif name == "RY":
            qc.ry(params[0], wires[0])
        elif name == "RZ":
            qc.rz(params[0], wires[0])
        elif name == "S":
            qc.s(wires[0])
        elif name == "T":
            qc.t(wires[0])
        elif name == "PauliX":
            qc.x(wires[0])
        elif name == "PauliZ":
            qc.z(wires[0])
        else:
            raise ValueError(f"Unsupported PennyLane gate in pickle conversion: {name!r}")

    for q in range(n_qubits):
        if q not in used_wires:
            qc.id(q)

    return export_qasm(qc)


@functools.lru_cache(maxsize=None)
def circuit_stats(qasm: str) -> Dict[str, Any]:
    qc = parse_qasm(qasm)
    return {
        "qubits": qc.num_qubits,
        "depth": qc.depth(),
        "num_gates": qc.size(),
        "2q_depth": qc.depth(filter_function=lambda x: x.operation.num_qubits == 2),
        "num_1q_gates": sum(1 for op in qc.data if op.operation.num_qubits == 1),
        "num_2q_gates": sum(1 for op in qc.data if op.operation.num_qubits == 2),
        "num_measurements": sum(1 for op in qc.data if op.operation.name == "measure"),
        "gates": dict(qc.count_ops()),
    }


def stringify_result_key(qasm: str, observable: str) -> str:
    return str((hash_circuit(qasm), observable))


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _resolve_fake_backend(fake_name: str):
    try:
        from qiskit_ibm_runtime.fake_provider import FakeProviderForBackendV2
        return FakeProviderForBackendV2().backend(fake_name)
    except Exception:
        pass
    try:
        from qiskit_ibm_runtime import fake_provider
        cls = "".join(p.capitalize() for p in fake_name.split("_")) + "V2"
        if hasattr(fake_provider, cls):
            return getattr(fake_provider, cls)()
    except Exception:
        pass
    raise ValueError(f"Cannot resolve fake backend {fake_name!r}")


@functools.lru_cache(maxsize=None)
def make_backend(backend_name: str) -> AerSimulator:
    if backend_name == "aer.perfect":
        return AerSimulator()
    if backend_name.startswith("aer.fake_"):
        fake = _resolve_fake_backend(backend_name[4:])
        return AerSimulator(noise_model=NoiseModel.from_backend(fake))
    raise ValueError(f"Unsupported backend: {backend_name!r}")


# ---------------------------------------------------------------------------
# Ground truth via PennyLane default.qubit statevector
# ---------------------------------------------------------------------------

def _pennylane_expval(qasm: str, observable: str) -> float:
    penny_circ = qml.from_qasm(qasm)
    n = parse_qasm(qasm).num_qubits

    if len(observable) != n:
        raise ValueError(f"Observable length {len(observable)} != circuit qubits {n}")

    obs_op = qml.pauli.string_to_pauli_word(
        observable,
        wire_map={i: i for i in range(n)},
    )

    dev = qml.device("default.qubit", wires=n)

    @qml.qnode(dev)
    def circuit():
        penny_circ()
        return qml.expval(obs_op)

    return float(circuit())


def exact_expectation_value(qasm: str, observable: str) -> float:
    return _pennylane_expval(qasm, observable)


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _prepare_measured_circuit(qasm: str, observable: str) -> QuantumCircuit:
    base = parse_qasm(qasm).remove_final_measurements(inplace=False)
    n = base.num_qubits
    if len(observable) != n:
        raise ValueError(f"Observable length {len(observable)} != circuit qubits {n}")

    measured = QuantumCircuit(n, n)
    measured.compose(base, inplace=True)

    for qubit, pauli in enumerate(observable):
        if pauli == "X":
            measured.h(qubit)
        elif pauli == "Y":
            measured.sdg(qubit)
            measured.h(qubit)
        elif pauli not in {"Z", "I"}:
            raise ValueError(f"Unsupported Pauli character: {pauli!r}")

    measured.measure(range(n), range(n))
    return measured


def run_counts(
    qasm: str,
    observable: str,
    backend: AerSimulator,
    shots: int,
    seed_simulator: Optional[int] = None,
) -> FreqCounts:
    if shots <= 0:
        return {}
    measured = _prepare_measured_circuit(qasm, observable)
    run_kwargs = {"shots": shots}
    if seed_simulator is not None:
        run_kwargs["seed_simulator"] = seed_simulator
    result = backend.run(measured, **run_kwargs).result()
    return {str(b): int(c) for b, c in result.get_counts().items()}


def expected_value_from_counts(counts: FreqCounts, observable: str) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    expval = 0.0
    for bitstring, count in counts.items():
        bits = bitstring[::-1]
        parity = 0
        for i, pauli in enumerate(observable):
            if pauli != "I" and i < len(bits) and bits[i] == "1":
                parity ^= 1
        expval += (1.0 if parity == 0 else -1.0) * (count / total)
    return float(expval)


# ---------------------------------------------------------------------------
# PennyLane cutting
# ---------------------------------------------------------------------------

def qasm_to_pennylane(qasm: str) -> Callable[[], None]:
    qasm_circuit = qml.from_qasm(qasm)

    def fun():
        qasm_circuit()
    return fun


def _obs_from_measurement(mp, wire_map: dict, n: int) -> str:
    obs = getattr(mp, "obs", None)
    if obs is None:
        return "I" * n
    obs_parts = ["I"] * n
    try:
        pw_str = qml.pauli.pauli_word_to_string(obs, wire_map=wire_map)
        for i, ch in enumerate(pw_str):
            if ch != "I":
                obs_parts[i] = ch
    except Exception:
        pass
    return "".join(obs_parts)


_QISKIT_GATE_MAP = {
    "Hadamard": ("h", []),
    "PauliX": ("x", []),
    "PauliY": ("y", []),
    "PauliZ": ("z", []),
    "S": ("s", []),
    "T": ("t", []),
    "SX": ("sx", []),
    "Adjoint(S)": ("sdg", []),
    "Adjoint(T)": ("tdg", []),
    "RX": ("rx", None),
    "RY": ("ry", None),
    "RZ": ("rz", None),
    "PhaseShift": ("p", None),
    "Rot": ("u", None),
    "CNOT": ("cx", []),
    "CZ": ("cz", []),
    "CY": ("cy", []),
    "SWAP": ("swap", []),
    "ISWAP": ("iswap", []),
    "IsingZZ": ("rzz", None),
    "IsingXX": ("rxx", None),
    "IsingYY": ("ryy", None),
    "CCX": ("ccx", []),
    "CSWAP": ("cswap", []),
    "Identity": ("id", []),
}
_SKIP_OPS = {"PrepareNode", "MeasureNode"}


def _tape_to_executable(tape: Any) -> Tuple[str, List[str]]:
    wires = list(tape.wires)
    wire_map = {w: i for i, w in enumerate(wires)}
    n = len(wires)

    obs_list = [_obs_from_measurement(mp, wire_map, n) for mp in tape.measurements]
    if not obs_list:
        obs_list = ["I" * n]

    qc = QuantumCircuit(n)
    for op in tape.operations:
        if op.name in _SKIP_OPS:
            continue
        mapped_wires = [wire_map[w] for w in op.wires]
        params = [float(p) for p in op.parameters]
        if op.name in _QISKIT_GATE_MAP:
            gname, gparams = _QISKIT_GATE_MAP[op.name]
            gparams = params if gparams is None else gparams
            method = getattr(qc, gname, None)
            if method is not None:
                method(*gparams, *mapped_wires)
            else:
                logger.warning("No Qiskit method for gate %s - skipping", op.name)
        else:
            try:
                from qiskit.extensions import UnitaryGate
                qc.append(UnitaryGate(op.matrix()), mapped_wires)
            except Exception:
                logger.warning("Skipping unknown gate %s in fragment", op.name)

    frag_qasm = export_qasm(parse_qasm(export_qasm(qc)))
    return frag_qasm, obs_list


def pennylane_cut(circuit_qasm: str, observable: str):
    penny_circ = qasm_to_pennylane(circuit_qasm)
    obs_word = qml.pauli.string_to_pauli_word(observable)

    qs = qml.tape.make_qscript(penny_circ)()
    uncut_tape = qml.tape.QuantumTape(qs.operations, [qml.expval(obs_word)])
    n_wires = uncut_tape.num_wires

    if n_wires < 4:
        raise ValueError(
            f"Circuit has only {n_wires} wires; cutting requires >= 4 wires "
            f"(CutStrategy min_free_wires=2 -> at least two 2-wire fragments)."
        )

    graph = qml.qcut.tape_to_graph(uncut_tape)
    cut_graph = qml.qcut.find_and_place_cuts(
        graph=graph,
        cut_strategy=qml.qcut.CutStrategy(
            max_free_wires=n_wires - 1,
            min_free_wires=2,
        ),
    )
    qml.qcut.replace_wire_cut_nodes(cut_graph)
    fragments, communication_graph = qml.qcut.fragment_graph(cut_graph)
    fragment_tapes = [qml.qcut.graph_to_tape(f) for f in fragments]

    cut_info = {
        "num_fragments": len(fragment_tapes),
        "fragments_qubits": [len(t.wires) for t in fragment_tapes],
    }

    fragment_tapes = [
        qml.map_wires(t, {w: i for i, w in enumerate(t.wires)})[0][0]
        for t in fragment_tapes
    ]

    expanded = [qml.qcut.expand_fragment_tape(t) for t in fragment_tapes]
    configurations, prepare_nodes, measure_nodes = [], [], []
    for tapes, p_nodes, m_nodes in expanded:
        configurations.append(tapes)
        prepare_nodes.append(p_nodes)
        measure_nodes.append(m_nodes)

    cut_info["num_variations"] = sum(len(c) for c in configurations)
    cut_info["variations"] = [len(c) for c in configurations]

    tape_variants: List[Dict[str, Any]] = []
    idx = 0
    for config in configurations:
        for tape in config:
            frag_qasm, obs_list = _tape_to_executable(tape)
            tape_variants.append({
                "frag_qasm": frag_qasm,
                "obs_list": obs_list,
                "idx": idx,
            })
            idx += 1

    return (
        tape_variants,
        configurations,
        communication_graph,
        prepare_nodes,
        measure_nodes,
        cut_info,
    )


def cut(circuit_qasm: str, observable: str):
    (
        tape_variants,
        configurations,
        comm_graph,
        prep_nodes,
        meas_nodes,
        cut_info,
    ) = pennylane_cut(circuit_qasm, observable)

    output: List[Tuple[str, List[str]]] = []
    tv_iter = iter(tape_variants)
    for config in configurations:
        group_frag_qasm = None
        group_obs: List[str] = []
        for _ in config:
            tv = next(tv_iter)
            if group_frag_qasm is None:
                group_frag_qasm = tv["frag_qasm"]
            group_obs.append(tv["obs_list"][0])
        if group_frag_qasm is not None:
            output.append((group_frag_qasm, group_obs))

    sew_data = {
        "tape_variants": tape_variants,
        "configurations": configurations,
        "communication_graph": comm_graph,
        "prepare_nodes": prep_nodes,
        "measure_nodes": meas_nodes,
    }

    return output, sew_data, cut_info


def sew(index_expvals: Dict[int, Any], sew_data: Dict[str, Any]) -> float:
    tape_variants = sew_data["tape_variants"]
    comm_graph = sew_data["communication_graph"]
    prep_nodes = sew_data["prepare_nodes"]
    meas_nodes = sew_data["measure_nodes"]

    for tv in tape_variants:
        idx = tv["idx"]
        if idx not in index_expvals:
            raise KeyError(
                f"Missing expval for tape idx={idx} "
                f"(frag={hash_circuit(tv['frag_qasm'])[:8]!r}, "
                f"obs={tv['obs_list']!r})"
            )

    results = [index_expvals[tv["idx"]] for tv in tape_variants]

    value = qml.qcut.qcut_processing_fn(
        results,
        comm_graph,
        prep_nodes,
        meas_nodes,
        use_opt_einsum=True,
    )
    return float(getattr(value, "real", value))


# ---------------------------------------------------------------------------
# Incremental execution helpers
# ---------------------------------------------------------------------------

def normalised_frequency(freq: FreqCounts) -> Dict[str, float]:
    total = sum(freq.values())
    if total == 0:
        return {k: 0.0 for k in freq}
    return {k: v / total for k, v in freq.items()}


def merge_distributions(prev: FreqCounts, new: FreqCounts) -> FreqCounts:
    result = prev.copy()
    for k, v in new.items():
        result[k] = result.get(k, 0) + v
    return result


def subtract_distributions(main: FreqCounts, sub: FreqCounts) -> FreqCounts:
    result = main.copy()
    for k, v in sub.items():
        result[k] = result.get(k, 0) - v
    return result


def cumulative_history(history: List[FreqCounts]) -> List[FreqCounts]:
    snapshots: List[FreqCounts] = []
    cumulative: FreqCounts = {}
    for batch in history:
        cumulative = merge_distributions(cumulative, batch)
        snapshots.append(cumulative.copy())
    return snapshots


def total_variation_distance(f1: FreqCounts, f2: FreqCounts) -> float:
    keys = set(f1) | set(f2)
    n1, n2 = normalised_frequency(f1), normalised_frequency(f2)
    return 0.5 * sum(abs(n1.get(k, 0.0) - n2.get(k, 0.0)) for k in keys)


def hellinger_distance(f1: FreqCounts, f2: FreqCounts) -> float:
    keys = set(f1) | set(f2)
    n1, n2 = normalised_frequency(f1), normalised_frequency(f2)
    s = 0.0
    for k in keys:
        s += (math.sqrt(n1.get(k, 0.0)) - math.sqrt(n2.get(k, 0.0))) ** 2
    return math.sqrt(0.5 * s)


def js_distance(f1: FreqCounts, f2: FreqCounts) -> float:
    keys = set(f1) | set(f2)
    p, q = normalised_frequency(f1), normalised_frequency(f2)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def kl(a: Dict[str, float], b: Dict[str, float]) -> float:
        s = 0.0
        for k in keys:
            ak = a.get(k, 0.0)
            bk = b.get(k, 0.0)
            if ak > 0.0 and bk > 0.0:
                s += ak * math.log(ak / bk)
        return s

    js = 0.5 * kl(p, m) + 0.5 * kl(q, m)
    return math.sqrt(js)


def get_distance_fn(name: str) -> Callable[[FreqCounts, FreqCounts], float]:
    if name == "tvd":
        return total_variation_distance
    if name == "hellinger":
        return hellinger_distance
    if name == "js":
        return js_distance
    raise ValueError(f"Unknown distance metric {name!r}")


def moving_average(values: List[float]) -> float:
    return sum(values) / len(values)


def ewma(values: List[float], alpha: float) -> float:
    s = values[0]
    for v in values[1:]:
        s = alpha * v + (1.0 - alpha) * s
    return s


def delta_distance_criterion(
    threshold: float = 0.05,
    offset: int = 1,
    distance_fn: Callable[[FreqCounts, FreqCounts], float] = total_variation_distance,
):
    def criterion(history, cumulative, last_result):
        snapshots = cumulative_history(history)
        if len(snapshots) <= offset:
            return False, {
                "criterion": "delta",
                "distance": None,
                "threshold": threshold,
                "offset": offset,
                "reason": "not_enough_history",
            }

        current = snapshots[-1]
        previous = snapshots[-1 - offset]
        distance = distance_fn(current, previous)

        return distance < threshold, {
            "criterion": "delta",
            "distance": distance,
            "threshold": threshold,
            "offset": offset,
        }

    return criterion


def delta_moving_average_criterion(
    threshold: float = 0.05,
    offset: int = 1,
    window: int = 3,
    distance_fn: Callable[[FreqCounts, FreqCounts], float] = total_variation_distance,
    alpha: Optional[float] = None,
):
    def criterion(history, cumulative, last_result):
        snapshots = cumulative_history(history)

        if len(snapshots) <= offset or len(snapshots) - offset < window:
            return False, {
                "criterion": "ewma" if alpha is not None else "dma",
                "distances": [],
                "aggregate": None,
                "threshold": threshold,
                "offset": offset,
                "window": window,
                "alpha": alpha,
                "reason": "not_enough_history",
            }

        distances = []
        start = len(snapshots) - window
        for i in range(start, len(snapshots)):
            prev_i = i - offset
            if prev_i < 0:
                return False, {
                    "criterion": "ewma" if alpha is not None else "dma",
                    "distances": [],
                    "aggregate": None,
                    "threshold": threshold,
                    "offset": offset,
                    "window": window,
                    "alpha": alpha,
                    "reason": "not_enough_history",
                }
            d = distance_fn(snapshots[i], snapshots[prev_i])
            distances.append(d)

        aggregate = ewma(distances, alpha) if alpha is not None else moving_average(distances)

        return aggregate < threshold, {
            "criterion": "ewma" if alpha is not None else "dma",
            "distances": distances,
            "aggregate": aggregate,
            "threshold": threshold,
            "offset": offset,
            "window": window,
            "alpha": alpha,
        }

    return criterion


def make_stopping_criterion(
    stopping_name: str,
    threshold: float,
    offset: int,
    distance_metric: str,
    window_size: int,
    ewma_alpha: float,
):
    distance_fn = get_distance_fn(distance_metric)

    if stopping_name == "delta":
        return delta_distance_criterion(
            threshold=threshold,
            offset=offset,
            distance_fn=distance_fn,
        )
    if stopping_name == "dma":
        return delta_moving_average_criterion(
            threshold=threshold,
            offset=offset,
            window=window_size,
            distance_fn=distance_fn,
            alpha=None,
        )
    if stopping_name == "ewma":
        return delta_moving_average_criterion(
            threshold=threshold,
            offset=offset,
            window=window_size,
            distance_fn=distance_fn,
            alpha=ewma_alpha,
        )
    raise ValueError(f"Unknown stopping criterion {stopping_name!r}")


def constant_stability_criterion(k: int = 1):
    return lambda history, cumulative, last, info: k


def constant_next_shots(default_shots: int = 10):
    return lambda history, cumulative, last, info: default_shots


class IncrementalExecution:
    def __init__(
        self,
        stopping_criterion,
        stability_criterion,
        next_shots,
        default_shots: int,
        max_shots: Optional[int] = None,
        initial_shots: Optional[int] = None,
        runner: Optional[Callable] = None,
    ):
        self.stopping_criterion = stopping_criterion
        self.stability_criterion = stability_criterion
        self.next_shots_fn = next_shots
        self.default_shots = default_shots
        self.max_shots = max_shots
        self.initial_shots = initial_shots
        self.runner = runner
        self.shots_run = 0
        self.iterations = 0
        self.last_stopping_info: Dict[str, Any] = {}

    def run(self, runner=None, *args, **kwargs) -> FreqCounts:
        active_runner = runner or self.runner
        if active_runner is None:
            raise ValueError("A runner must be provided.")

        cumulative: FreqCounts = {}
        history: List[FreqCounts] = []
        current_shots = 0
        iteration_count = 0
        stop_flag = False
        stability_counter = 0
        max_allowed = self.max_shots if self.max_shots is not None else float("inf")
        next_shots = self.initial_shots if self.initial_shots is not None else self.default_shots

        while (not stop_flag) and (current_shots < max_allowed):
            iteration_count += 1
            next_shots = min(next_shots, max_allowed - current_shots)
            if next_shots <= 0:
                break

            last_result = active_runner(*args, shots=next_shots, **kwargs)
            cumulative = merge_distributions(cumulative, last_result)
            history.append(last_result)
            current_shots += sum(last_result.values())

            stopping_flag, info = self.stopping_criterion(history, cumulative, last_result)
            self.last_stopping_info = info or {}

            stability_counter = (stability_counter + 1) if stopping_flag else 0
            if stability_counter >= self.stability_criterion(history, cumulative, last_result, info):
                stop_flag = True

            next_shots = self.next_shots_fn(history, cumulative, last_result, info)

        self.shots_run = current_shots
        self.iterations = iteration_count
        return cumulative


# ---------------------------------------------------------------------------
# Shot allocation policies over execution units
# ---------------------------------------------------------------------------

def _flatten_execution_units(
    tape_variants: Sequence[Dict[str, Any]]
) -> List[Tuple[int, str, str]]:
    """One execution unit per (tape variant, observable)."""
    units: List[Tuple[int, str, str]] = []
    for tv in tape_variants:
        idx = tv["idx"]
        fq = tv["frag_qasm"]
        for obs in tv["obs_list"]:
            units.append((idx, fq, obs))
    return units


def _execution_unit_stats(
    tape_variants: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Stats for each execution unit = (tape, observable)."""
    stats = []
    for idx, frag_qasm, obs in _flatten_execution_units(tape_variants):
        cs = circuit_stats(frag_qasm)
        stats.append({
            "idx": idx,
            "frag_qasm": frag_qasm,
            "observable": obs,
            "n_qubits": cs["qubits"],
            "n_2q_gates": cs["num_2q_gates"],
        })
    return stats


def _round_execution_unit_weights(
    unit_stats: Sequence[Dict[str, Any]],
    raw_weights: Sequence[float],
    total: int,
) -> Dict[Tuple[int, str], int]:
    """Round weights to integers while preserving the total exactly."""
    if len(unit_stats) != len(raw_weights):
        raise ValueError("unit_stats and raw_weights must have same length")
    if total < 0:
        raise ValueError("total must be >= 0")

    floors = [int(w) for w in raw_weights]
    deficit = total - sum(floors)
    order = sorted(
        range(len(raw_weights)),
        key=lambda i: -(raw_weights[i] - floors[i]),
    )

    for k in range(deficit):
        floors[order[k]] += 1

    alloc: Dict[Tuple[int, str], int] = {}
    for item, shots in zip(unit_stats, floors):
        alloc[(item["idx"], item["observable"])] = int(shots)
    return alloc


def _alloc_exec_equal_split(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    if not unit_stats:
        return {}
    base = shots // len(unit_stats)
    rem = shots % len(unit_stats)
    alloc = {}
    for i, item in enumerate(unit_stats):
        alloc[(item["idx"], item["observable"])] = base + (1 if i < rem else 0)
    return alloc


def _alloc_exec_full_budget(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    return {(item["idx"], item["observable"]): shots for item in unit_stats}


def _alloc_exec_qubit_prop(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    if not unit_stats:
        return {}
    total_q = sum(item["n_qubits"] for item in unit_stats)
    if total_q == 0:
        raw = [shots / len(unit_stats)] * len(unit_stats)
    else:
        raw = [item["n_qubits"] / total_q * shots for item in unit_stats]
    return _round_execution_unit_weights(unit_stats, raw, shots)


def _alloc_exec_qubit_exp(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    if not unit_stats:
        return {}
    exp_q = [math.exp(item["n_qubits"]) for item in unit_stats]
    total_exp = sum(exp_q)
    if total_exp == 0:
        raw = [shots / len(unit_stats)] * len(unit_stats)
    else:
        raw = [e / total_exp * shots for e in exp_q]
    return _round_execution_unit_weights(unit_stats, raw, shots)


def _alloc_exec_gate2q_prop(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
    alpha: float = 0.8,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    if not unit_stats:
        return {}
    total_g = sum(item["n_2q_gates"] for item in unit_stats)
    base = shots / len(unit_stats)
    if total_g == 0:
        raw = [shots / len(unit_stats)] * len(unit_stats)
    else:
        raw = [
            alpha * (item["n_2q_gates"] / total_g) * shots + (1 - alpha) * base
            for item in unit_stats
        ]
    return _round_execution_unit_weights(unit_stats, raw, shots)


def _alloc_exec_gate2q_exp(
    tape_variants: Sequence[Dict[str, Any]],
    shots: int,
    alpha: float = 0.8,
) -> Dict[Tuple[int, str], int]:
    unit_stats = _execution_unit_stats(tape_variants)
    if not unit_stats:
        return {}
    exp_g = [math.exp(item["n_2q_gates"]) for item in unit_stats]
    total_exp = sum(exp_g)
    base = shots / len(unit_stats)
    if total_exp == 0:
        raw = [shots / len(unit_stats)] * len(unit_stats)
    else:
        raw = [
            alpha * (e / total_exp) * shots + (1 - alpha) * base
            for e in exp_g
        ]
    return _round_execution_unit_weights(unit_stats, raw, shots)


_ALLOCATION_CONFIGS: Dict[str, Dict[str, str]] = {
    "divide": {
        "op_name": "cc_divide_budget",
        "allocation_name": "equal_split_budget",
        "incremental_op_name": "cc_incremental_budget",
        "incremental_allocation_name": "equal_split_budget_incremental",
        "progress_desc": "cut_incremental_budget",
    },
    "full": {
        "op_name": "cc_full_budget_per_variant",
        "allocation_name": "full_budget_per_variant",
        "incremental_op_name": "cc_incremental_full_budget_per_variant",
        "incremental_allocation_name": "full_budget_per_variant_incremental",
        "progress_desc": "cut_incremental_full_budget_per_variant",
    },
    "qubit_prop": {
        "op_name": "cc_qubit_prop",
        "allocation_name": "qubit_driven_proportional",
        "incremental_op_name": "cc_incremental_qubit_prop",
        "incremental_allocation_name": "qubit_driven_proportional_incremental",
        "progress_desc": "cut_incremental_qubit_prop",
    },
    "qubit_exp": {
        "op_name": "cc_qubit_exp",
        "allocation_name": "qubit_driven_exponential",
        "incremental_op_name": "cc_incremental_qubit_exp",
        "incremental_allocation_name": "qubit_driven_exponential_incremental",
        "progress_desc": "cut_incremental_qubit_exp",
    },
    "gate2q_prop": {
        "op_name": "cc_gate2q_prop",
        "allocation_name": "gate2q_driven_proportional",
        "incremental_op_name": "cc_incremental_gate2q_prop",
        "incremental_allocation_name": "gate2q_driven_proportional_incremental",
        "progress_desc": "cut_incremental_gate2q_prop",
    },
    "gate2q_exp": {
        "op_name": "cc_gate2q_exp",
        "allocation_name": "gate2q_driven_exponential",
        "incremental_op_name": "cc_incremental_gate2q_exp",
        "incremental_allocation_name": "gate2q_driven_exponential_incremental",
        "progress_desc": "cut_incremental_gate2q_exp",
    },
}


# ---------------------------------------------------------------------------
# Cut circuit caching
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _cut_circuit_cached(circuit_qasm: str, observable: str):
    cut_output, sew_data, cut_info = cut(circuit_qasm, observable)
    tape_variants = sew_data["tape_variants"]
    return cut_output, sew_data, cut_info, tape_variants


def _prepare_cut(
    circuit_qasm: str,
    observable: str,
    shots: int,
    allocation_mode: str,
    incremental: bool = False,
    gate2q_alpha: float = 0.8,
):
    if allocation_mode not in _ALLOCATION_CONFIGS:
        raise ValueError(f"Unknown allocation mode {allocation_mode!r}")

    cut_output, sew_data, cut_info, tape_variants = _cut_circuit_cached(circuit_qasm, observable)

    if allocation_mode == "divide":
        allocation = _alloc_exec_equal_split(tape_variants, shots)
    elif allocation_mode == "full":
        allocation = _alloc_exec_full_budget(tape_variants, shots)
    elif allocation_mode == "qubit_prop":
        allocation = _alloc_exec_qubit_prop(tape_variants, shots)
    elif allocation_mode == "qubit_exp":
        allocation = _alloc_exec_qubit_exp(tape_variants, shots)
    elif allocation_mode == "gate2q_prop":
        allocation = _alloc_exec_gate2q_prop(tape_variants, shots, alpha=gate2q_alpha)
    elif allocation_mode == "gate2q_exp":
        allocation = _alloc_exec_gate2q_exp(tape_variants, shots, alpha=gate2q_alpha)
    else:
        raise ValueError(f"Unknown allocation mode {allocation_mode!r}")

    cfg = _ALLOCATION_CONFIGS[allocation_mode]
    op_name = cfg["incremental_op_name" if incremental else "op_name"]
    allocation_name = cfg["incremental_allocation_name" if incremental else "allocation_name"]

    return (
        cut_output,
        sew_data,
        cut_info,
        tape_variants,
        allocation,
        op_name,
        allocation_name,
    )


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _value_tree(backend_name, qasm, observable, value):
    return {"ibm_aer": {backend_name: {stringify_result_key(qasm, observable): value}}}


def make_payload(
    *,
    operation,
    circuit_qasm,
    observable,
    backend_name,
    shots_requested,
    result_value,
    perf_exp_val,
    time_total,
    extra_times=None,
    stats=None,
    shots_executed,
    extra_params=None,
) -> Dict[str, Any]:
    absolute_error = abs(perf_exp_val - result_value)

    params = {
        "circuits": circuit_qasm,
        "observable": observable,
        "shots": shots_requested,
        "backends": [("ibm_aer", backend_name)],
        "operation": operation,
        "perf_exp_val": perf_exp_val,
    }
    if extra_params:
        params.update(extra_params)

    times = {"time_total": time_total}
    if extra_times:
        times.update(extra_times)

    return {
        "params": params,
        "results": _value_tree(backend_name, circuit_qasm, observable, result_value),
        "times": times,
        "stats": stats or {},
        "absolute_error": _value_tree(backend_name, circuit_qasm, observable, absolute_error),
        "shots_executed": shots_executed,
    }


def extract_payload_value(payload):
    provider = next(iter(payload["results"]))
    backend = next(iter(payload["results"][provider]))
    value = float(next(iter(payload["results"][provider][backend].values())))
    return provider, backend, value


def extract_payload_absolute_error(payload):
    provider = next(iter(payload["absolute_error"]))
    backend = next(iter(payload["absolute_error"][provider]))
    return float(next(iter(payload["absolute_error"][provider][backend].values())))


def build_cut_statistics(
    *,
    cut_info,
    tape_variants,
    allocation,
    executed=None,
    iterations=None,
) -> Dict[str, Any]:
    """Per execution-unit stats.

    allocation: {(variant_idx, observable): shots}
    executed:   {(variant_idx, observable): shots}
    """
    details, alloc_vals, exec_vals = [], [], []

    for tv in tape_variants:
        idx = tv["idx"]
        fq = tv["frag_qasm"]

        for obs in tv["obs_list"]:
            key = (idx, obs)
            allocated = int(allocation.get(key, 0))
            executed_shots = int(executed.get(key, allocated) if executed else allocated)

            item = {
                "variant_index": idx,
                "variant_key": stringify_result_key(fq, obs),
                "fragment_hash": hash_circuit(fq),
                "observable": obs,
                "allocated_shots": allocated,
                "executed_shots": executed_shots,
            }
            if iterations is not None:
                item["iterations"] = int(iterations.get(key, 0))

            details.append(item)
            alloc_vals.append(allocated)
            exec_vals.append(executed_shots)

    return {
        "num_fragments": int(cut_info.get("num_fragments", 0)),
        "num_variants": len(details),
        "variations_per_fragment": list(cut_info.get("variations", [])),
        "total_allocated_shots": sum(alloc_vals),
        "total_executed_shots": sum(exec_vals),
        "min_allocated_shots_per_variant": min(alloc_vals) if alloc_vals else None,
        "max_allocated_shots_per_variant": max(alloc_vals) if alloc_vals else None,
        "min_executed_shots_per_variant": min(exec_vals) if exec_vals else None,
        "max_executed_shots_per_variant": max(exec_vals) if exec_vals else None,
        "shots_per_variant": details,
    }


def _format_executed_shots_per_variant(payload):
    details = payload.get("stats", {}).get("cut_statistics", {}).get("shots_per_variant") or []
    if details:
        return "[" + "-".join(str(int(d.get("executed_shots", 0))) for d in details) + "]"
    return ""


def _iter_variants(variants, desc, show_progress):
    try:
        from tqdm.auto import tqdm
        yield from tqdm(variants, total=len(variants), desc=desc, leave=False, disable=not show_progress)
    except ImportError:
        yield from variants


# ---------------------------------------------------------------------------
# Execution modes
# ---------------------------------------------------------------------------

def run_vanilla(
    circuit_qasm: str,
    observable: str,
    shots: int,
    backend: AerSimulator,
    backend_name: str,
    perf_exp_val: float,
    seed_simulator: Optional[int],
) -> Dict[str, Any]:
    start = time.perf_counter()
    counts = run_counts(circuit_qasm, observable, backend, shots, seed_simulator)
    result = expected_value_from_counts(counts, observable)
    total = time.perf_counter() - start

    return make_payload(
        operation="vanilla",
        circuit_qasm=circuit_qasm,
        observable=observable,
        backend_name=backend_name,
        shots_requested=shots,
        result_value=result,
        perf_exp_val=perf_exp_val,
        time_total=total,
        extra_times={"time_execution": total},
        stats={
            "circuit_stats": circuit_stats(circuit_qasm),
            "counts": counts,
        },
        shots_executed=shots,
    )


def _require_opt_einsum_if_needed(sew_data: Dict[str, Any]) -> None:
    import importlib.util

    n_edges = sew_data["communication_graph"].number_of_edges()
    if n_edges > 52 and importlib.util.find_spec("opt_einsum") is None:
        raise RuntimeError(
            f"This circuit needs opt_einsum for reconstruction "
            f"({n_edges} cut edges > 52). Install it with: pip install opt_einsum"
        )


def run_cutting(
    circuit_qasm: str,
    observable: str,
    shots: int,
    backend: AerSimulator,
    backend_name: str,
    perf_exp_val: float,
    seed_simulator: Optional[int],
    allocation_mode: str,
    show_progress: bool = False,
    gate2q_alpha: float = 0.8,
) -> Dict[str, Any]:
    overall_start = time.perf_counter()

    cut_start = time.perf_counter()
    cut_output, sew_data, cut_info, tape_variants, allocation, op_name, allocation_name = \
        _prepare_cut(
            circuit_qasm,
            observable,
            shots,
            allocation_mode,
            incremental=False,
            gate2q_alpha=gate2q_alpha,
        )
    _require_opt_einsum_if_needed(sew_data)
    time_cutting = time.perf_counter() - cut_start

    counts_by_variant: Dict[str, FreqCounts] = {}
    expvals: Dict[int, Any] = {}
    executed_map: Dict[Tuple[int, str], int] = {}

    exec_start = time.perf_counter()
    for tv in _iter_variants(tape_variants, allocation_name, show_progress):
        idx = tv["idx"]
        fq = tv["frag_qasm"]
        obs_list = tv["obs_list"]

        tape_evs = []
        for obs in obs_list:
            key = (idx, obs)
            unit_shots = allocation.get(key, 0)

            if unit_shots <= 0:
                counts_by_variant[stringify_result_key(fq, obs)] = {}
                tape_evs.append(0.0)
                executed_map[key] = 0
                continue

            unit_seed = derive_seed(
                seed_simulator,
                "cutting",
                allocation_mode,
                hash_circuit(fq),
                idx,
                obs,
                unit_shots,
            )

            counts = run_counts(fq, obs, backend, unit_shots, unit_seed)
            ev = expected_value_from_counts(counts, obs)
            tape_evs.append(ev)
            counts_by_variant[stringify_result_key(fq, obs)] = counts
            executed_map[key] = sum(counts.values())

        expvals[idx] = tuple(tape_evs) if len(tape_evs) > 1 else tape_evs[0]

    time_execution = time.perf_counter() - exec_start

    sew_start = time.perf_counter()
    result_value = sew(expvals, sew_data)
    time_sew = time.perf_counter() - sew_start
    total = time.perf_counter() - overall_start

    cut_statistics = build_cut_statistics(
        cut_info=cut_info,
        tape_variants=tape_variants,
        allocation=allocation,
        executed=executed_map,
    )

    stats = {
        "circuit_stats": circuit_stats(circuit_qasm),
        "cut_info": cut_info,
        "cut_statistics": cut_statistics,
        "cut_output": [
            {
                "fragment_hash": hash_circuit(q),
                "observables": list(obs_list),
                "fragment_stats": circuit_stats(q),
            }
            for q, obs_list in cut_output
        ],
        "variant_counts": counts_by_variant,
        "variant_exp_values": {
            stringify_result_key(tv["frag_qasm"], tv["obs_list"][0]): expvals[tv["idx"]]
            for tv in tape_variants
        },
        "variant_shots_budget": {
            stringify_result_key(tv["frag_qasm"], obs): allocation.get((tv["idx"], obs), 0)
            for tv in tape_variants for obs in tv["obs_list"]
        },
        "variant_shots_executed": {
            stringify_result_key(tv["frag_qasm"], obs): executed_map.get((tv["idx"], obs), 0)
            for tv in tape_variants for obs in tv["obs_list"]
        },
    }

    return make_payload(
        operation=op_name,
        circuit_qasm=circuit_qasm,
        observable=observable,
        backend_name=backend_name,
        shots_requested=shots,
        result_value=result_value,
        perf_exp_val=perf_exp_val,
        time_total=total,
        extra_times={
            "time_cutting": time_cutting,
            "time_execution": time_execution,
            "time_sew": time_sew,
        },
        stats=stats,
        shots_executed=cut_statistics["total_executed_shots"],
        extra_params={
            "shots_allocation": allocation_name,
            "gate2q_alpha": gate2q_alpha,
        },
    )


def run_cutting_incremental(
    circuit_qasm: str,
    observable: str,
    shots: int,
    backend: AerSimulator,
    backend_name: str,
    perf_exp_val: float,
    seed_simulator: Optional[int],
    batch_shots: int,
    threshold: float,
    offset: int,
    stability_k: int,
    initial_batch_shots: Optional[int] = None,
    allocation_mode: str = "divide",
    show_progress: bool = False,
    gate2q_alpha: float = 0.8,
    stopping_name: str = "delta",
    distance_metric: str = "tvd",
    window_size: int = 3,
    ewma_alpha: float = 0.5,
) -> Dict[str, Any]:
    overall_start = time.perf_counter()

    cut_start = time.perf_counter()
    cut_output, sew_data, cut_info, tape_variants, allocation, op_name, allocation_name = \
        _prepare_cut(
            circuit_qasm,
            observable,
            shots,
            allocation_mode,
            incremental=True,
            gate2q_alpha=gate2q_alpha,
        )
    _require_opt_einsum_if_needed(sew_data)
    cfg = _ALLOCATION_CONFIGS[allocation_mode]
    time_cutting = time.perf_counter() - cut_start

    stopping_criterion = make_stopping_criterion(
        stopping_name=stopping_name,
        threshold=threshold,
        offset=offset,
        distance_metric=distance_metric,
        window_size=window_size,
        ewma_alpha=ewma_alpha,
    )
    stability_criterion = constant_stability_criterion(stability_k)

    counts_by_variant: Dict[str, FreqCounts] = {}
    expvals: Dict[int, Any] = {}
    executed_map: Dict[Tuple[int, str], int] = {}
    iterations_map: Dict[Tuple[int, str], int] = {}
    stopinfo_map: Dict[Tuple[int, str], Dict[str, Any]] = {}

    exec_start = time.perf_counter()
    for tv in _iter_variants(tape_variants, cfg["progress_desc"], show_progress):
        idx = tv["idx"]
        fq = tv["frag_qasm"]
        obs_list = tv["obs_list"]

        tape_evs = []
        for obs in obs_list:
            key = (idx, obs)
            max_shots_unit = allocation.get(key, 0)

            if max_shots_unit <= 0:
                tape_evs.append(0.0)
                executed_map[key] = 0
                iterations_map[key] = 0
                stopinfo_map[key] = {}
                counts_by_variant[stringify_result_key(fq, obs)] = {}
                continue

            _batch = min(batch_shots, max_shots_unit)
            _initial = (
                min(initial_batch_shots, max_shots_unit)
                if initial_batch_shots is not None else None
            )

            batch_counter = 0

            def runner(*, shots: int, _q=fq, _o=obs, _idx=idx):
                nonlocal batch_counter
                batch_counter += 1

                batch_seed = derive_seed(
                    seed_simulator,
                    "cutting_incremental",
                    allocation_mode,
                    hash_circuit(_q),
                    _idx,
                    _o,
                    batch_counter,
                    shots,
                )

                return run_counts(_q, _o, backend, shots, batch_seed)

            executor = IncrementalExecution(
                stopping_criterion=stopping_criterion,
                stability_criterion=stability_criterion,
                next_shots=constant_next_shots(default_shots=_batch),
                default_shots=_batch,
                max_shots=max_shots_unit,
                initial_shots=_initial,
                runner=runner,
            )

            counts = executor.run()
            ev = expected_value_from_counts(counts, obs)

            tape_evs.append(ev)
            counts_by_variant[stringify_result_key(fq, obs)] = counts
            executed_map[key] = int(executor.shots_run)
            iterations_map[key] = int(executor.iterations)
            stopinfo_map[key] = dict(executor.last_stopping_info or {})

        expvals[idx] = tuple(tape_evs) if len(tape_evs) > 1 else tape_evs[0]

    time_execution = time.perf_counter() - exec_start

    sew_start = time.perf_counter()
    result_value = sew(expvals, sew_data)
    time_sew = time.perf_counter() - sew_start
    total = time.perf_counter() - overall_start

    cut_statistics = build_cut_statistics(
        cut_info=cut_info,
        tape_variants=tape_variants,
        allocation=allocation,
        executed=executed_map,
        iterations=iterations_map,
    )

    stats = {
        "circuit_stats": circuit_stats(circuit_qasm),
        "cut_info": cut_info,
        "cut_statistics": cut_statistics,
        "cut_output": [
            {
                "fragment_hash": hash_circuit(q),
                "observables": list(obs_list),
                "fragment_stats": circuit_stats(q),
            }
            for q, obs_list in cut_output
        ],
        "variant_counts": counts_by_variant,
        "variant_exp_values": {
            stringify_result_key(tv["frag_qasm"], tv["obs_list"][0]): expvals[tv["idx"]]
            for tv in tape_variants
        },
        "variant_shots_budget": {
            stringify_result_key(tv["frag_qasm"], obs): allocation.get((tv["idx"], obs), 0)
            for tv in tape_variants for obs in tv["obs_list"]
        },
        "variant_shots_executed": {
            stringify_result_key(tv["frag_qasm"], obs): executed_map.get((tv["idx"], obs), 0)
            for tv in tape_variants for obs in tv["obs_list"]
        },
        "variant_iterations": {
            stringify_result_key(tv["frag_qasm"], obs): iterations_map.get((tv["idx"], obs), 0)
            for tv in tape_variants for obs in tv["obs_list"]
        },
        "variant_stopping_info": {
            stringify_result_key(tv["frag_qasm"], obs): stopinfo_map.get((tv["idx"], obs), {})
            for tv in tape_variants for obs in tv["obs_list"]
        },
        "incremental": {
            "initial_batch_shots": initial_batch_shots,
            "batch_shots": batch_shots,
            "threshold": threshold,
            "offset": offset,
            "stability_k": stability_k,
            "stopping_criterion": stopping_name,
            "distance_metric": distance_metric,
            "window_size": window_size,
            "ewma_alpha": ewma_alpha if stopping_name == "ewma" else None,
        },
    }

    return make_payload(
        operation=op_name,
        circuit_qasm=circuit_qasm,
        observable=observable,
        backend_name=backend_name,
        shots_requested=shots,
        result_value=result_value,
        perf_exp_val=perf_exp_val,
        time_total=total,
        extra_times={
            "time_cutting": time_cutting,
            "time_execution": time_execution,
            "time_sew": time_sew,
        },
        stats=stats,
        shots_executed=cut_statistics["total_executed_shots"],
        extra_params={
            "shots_allocation": allocation_name,
            "incremental_batch_shots": batch_shots,
            "incremental_threshold": threshold,
            "incremental_offset": offset,
            "incremental_stability_k": stability_k,
            "incremental_stopping_criterion": stopping_name,
            "incremental_distance_metric": distance_metric,
            "incremental_window_size": window_size,
            "incremental_ewma_alpha": ewma_alpha if stopping_name == "ewma" else None,
            "gate2q_alpha": gate2q_alpha,
        },
    )


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def load_pickled_cuttable_circuits(
    circuits_pkl: Path,
    circuit_index: Optional[int] = None,
) -> List[CircuitSpec]:
    with circuits_pkl.open("rb") as fh:
        items = pickle.load(fh)

    if not items:
        raise ValueError(f"{circuits_pkl} is empty")

    if circuit_index is not None:
        items = [items[circuit_index]]

    circuits: List[CircuitSpec] = []

    for i, item in enumerate(items):
        n_qubits = int(item["n_qubits"])
        ops = item["ops"]
        meas = item["meas"]

        if not meas or len(meas) != 1:
            raise ValueError(
                f"Entry {i} has unsupported measurements: expected exactly one measurement"
            )

        qasm = pl_ops_to_qasm(ops, n_qubits)
        observable = "Z" + "I" * (n_qubits - 1)

        family = item.get("family", "loaded")
        tag = item.get("tag", f"circ_{i + 1:03d}")
        name = f"{i}_{tag}"

        circuits.append(CircuitSpec(
            id=len(circuits) + 1,
            circuit_qasm=qasm,
            observable=observable,
            n_qubits=n_qubits,
            circuit_name=name,
            circuit_conf={
                "source_pickle": str(circuits_pkl),
                "original_id": item.get("id"),
                "family": family,
                "tag": tag,
                "fragment_sizes": item.get("fragment_sizes"),
                "num_cuts": item.get("num_cuts"),
                "total_variations": item.get("total_variations"),
            },
            family=family,
            tag=tag,
            source="valid_circuits.pkl",
        ))

    if not circuits:
        raise ValueError("No circuits loaded.")
    return circuits


# ---------------------------------------------------------------------------
# CSV / serialisation
# ---------------------------------------------------------------------------

_CSV_FIELDNAMES = [
    "circuit_name",
    "circuit_qubits",
    "observable",
    "mode",
    "backend",
    "perf_exp_val",
    "result",
    "absolute_error",
    "distance_to_noisy_vanilla",
    "time_total_s",
    "shots_requested",
    "shots_executed",
    "shots_saved",
    "num_fragments",
    "num_variants",
    "total_allocated_variant_shots",
    "total_executed_variant_shots",
    "min_allocated_shots_per_variant",
    "max_allocated_shots_per_variant",
    "min_executed_shots_per_variant",
    "max_executed_shots_per_variant",
    "executed_shots_per_variant",
]


def summarise_payload(
    payload: Dict[str, Any],
    circuit_name: str,
    circuit_qubits: int,
    observable: str,
    mode: str,
    noisy_vanilla_value: float,
) -> Dict[str, Any]:
    _, backend, value = extract_payload_value(payload)
    absolute_error = extract_payload_absolute_error(payload)
    cut_stats = payload.get("stats", {}).get("cut_statistics", {})
    shots_requested = payload["params"].get("shots", 0)
    shots_executed = payload.get("shots_executed", shots_requested)
    distance = payload.get("distance_to_noisy_vanilla", abs(value - noisy_vanilla_value))

    return {
        "circuit_name": circuit_name,
        "circuit_qubits": circuit_qubits,
        "observable": observable,
        "mode": mode,
        "backend": backend,
        "perf_exp_val": payload["params"].get("perf_exp_val"),
        "result": value,
        "absolute_error": absolute_error,
        "distance_to_noisy_vanilla": distance,
        "time_total_s": payload["times"]["time_total"],
        "shots_requested": shots_requested,
        "shots_executed": shots_executed,
        "shots_saved": shots_requested - shots_executed,
        "num_fragments": cut_stats.get("num_fragments"),
        "num_variants": cut_stats.get("num_variants"),
        "total_allocated_variant_shots": cut_stats.get("total_allocated_shots"),
        "total_executed_variant_shots": cut_stats.get("total_executed_shots"),
        "min_allocated_shots_per_variant": cut_stats.get("min_allocated_shots_per_variant"),
        "max_allocated_shots_per_variant": cut_stats.get("max_allocated_shots_per_variant"),
        "min_executed_shots_per_variant": cut_stats.get("min_executed_shots_per_variant"),
        "max_executed_shots_per_variant": cut_stats.get("max_executed_shots_per_variant"),
        "executed_shots_per_variant": _format_executed_shots_per_variant(payload),
    }


def _circuit_dir(output_dir: Path, spec: CircuitSpec) -> Path:
    d = output_dir / f"{spec.n_qubits}q" / spec.circuit_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def save_mode_result(
    circuit_dir: Path,
    mode: str,
    payload: Dict[str, Any],
    summary_row: Dict[str, Any],
) -> None:
    stem = _safe_filename(mode)
    tmp = circuit_dir / f"{stem}.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(circuit_dir / f"{stem}.json")

    tmp = circuit_dir / f"{stem}.csv.tmp"
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        w.writeheader()
        w.writerow(summary_row)
    tmp.replace(circuit_dir / f"{stem}.csv")

    logger.info("Saved %s -> %s", mode, circuit_dir)


def merge_csv_files(output_dir: Path) -> Path:
    merged_path = output_dir / "summary.csv"
    intermediate = [p for p in sorted(output_dir.rglob("*.csv")) if p != merged_path]
    rows: List[Dict[str, Any]] = []
    for p in intermediate:
        with p.open(newline="") as fh:
            rows.extend(csv.DictReader(fh))
    with merged_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    for p in intermediate:
        try:
            p.unlink()
        except OSError:
            pass
    return merged_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_MODES = [
    "ideal_aer",
    "noisy_vanilla",
    "cut_divided_budget",
    "cut_full_budget_per_variant",
    "cut_incremental_budget",
    "cut_incremental_full_budget_per_variant",
    "cut_qubit_prop",
    "cut_qubit_exp",
    "cut_gate2q_prop",
    "cut_gate2q_exp",
    "cut_incremental_qubit_prop",
    "cut_incremental_qubit_exp",
    "cut_incremental_gate2q_prop",
    "cut_incremental_gate2q_exp",
]

_MODE_TO_ALLOC: Dict[str, Tuple[str, bool]] = {
    "cut_divided_budget": ("divide", False),
    "cut_full_budget_per_variant": ("full", False),
    "cut_incremental_budget": ("divide", True),
    "cut_incremental_full_budget_per_variant": ("full", True),
    "cut_qubit_prop": ("qubit_prop", False),
    "cut_qubit_exp": ("qubit_exp", False),
    "cut_gate2q_prop": ("gate2q_prop", False),
    "cut_gate2q_exp": ("gate2q_exp", False),
    "cut_incremental_qubit_prop": ("qubit_prop", True),
    "cut_incremental_qubit_exp": ("qubit_exp", True),
    "cut_incremental_gate2q_prop": ("gate2q_prop", True),
    "cut_incremental_gate2q_exp": ("gate2q_exp", True),
}


def _available_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / 1024**3
    except ImportError:
        return float("inf")


def _cpu_load_1m() -> float:
    try:
        import psutil
        load1, _, _ = psutil.getloadavg()
        return load1 / psutil.cpu_count(logical=True)
    except Exception:
        return 0.0


def _wait_for_resources(required_ram_gb, max_cpu_load, poll_interval_s=10.0):
    reported = False
    while True:
        if _available_ram_gb() >= required_ram_gb and _cpu_load_1m() <= max_cpu_load:
            return
        if not reported:
            logger.info(
                "Waiting: RAM=%.1f GB (need %.1f), CPU=%.2f (limit %.2f)",
                _available_ram_gb(), required_ram_gb, _cpu_load_1m(), max_cpu_load,
            )
            reported = True
        time.sleep(poll_interval_s)


def validate_incremental_args(args: argparse.Namespace) -> None:
    if args.incremental_batch_shots <= 0:
        raise ValueError("--incremental-batch-shots must be > 0")

    if args.incremental_initial_batch_shots is not None and args.incremental_initial_batch_shots <= 0:
        raise ValueError("--incremental-initial-batch-shots must be > 0")

    if args.incremental_offset <= 0:
        raise ValueError("--incremental-offset must be > 0")

    if args.incremental_stability_k <= 0:
        raise ValueError("--incremental-stability-k must be > 0")

    crit = args.incremental_stopping_criterion
    if crit in {"dma", "ewma"} and args.incremental_window_size <= 0:
        raise ValueError("--incremental-window-size must be > 0")

    if crit == "ewma":
        alpha = args.incremental_ewma_alpha
        if not (0.0 < alpha <= 1.0):
            raise ValueError("--incremental-ewma-alpha must be in (0, 1]")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Benchmark circuits loaded from valid_circuits.pkl.\n"
            "Cutting uses find_and_place_cuts + CutStrategy(min_free_wires=2).\n"
            "Ground truth uses PennyLane default.qubit statevector.\n"
            "Reconstruction uses qcut_processing_fn (tensor contraction)."
        )
    )
    p.add_argument("--circuits-pkl", type=Path, required=True,
                   help="valid_circuits.pkl produced by generate_and_cut.py")
    p.add_argument("--circuit-index", type=int, default=None)
    p.add_argument("--shots", type=int, default=8000, help="Total shot budget B.")
    p.add_argument("--noisy-backend", type=str, default="aer.fake_brisbane")
    p.add_argument("--ideal-backend", type=str, default="aer.perfect")
    p.add_argument("--seed-simulator", type=int, default=None)
    p.add_argument("--modes", nargs="+", default=["all"], choices=ALL_MODES + ["all"])
    p.add_argument("--incremental-batch-shots", type=int, default=50)
    p.add_argument("--incremental-initial-batch-shots", type=int, default=None)
    p.add_argument("--incremental-threshold", type=float, default=0.03)
    p.add_argument("--incremental-offset", type=int, default=2)
    p.add_argument("--incremental-stability-k", type=int, default=3)
    p.add_argument(
        "--incremental-stopping-criterion",
        type=str,
        default="delta",
        choices=["delta", "dma", "ewma"],
        help="Stopping criterion for incremental execution.",
    )
    p.add_argument(
        "--incremental-distance-metric",
        type=str,
        default="tvd",
        choices=["tvd", "hellinger", "js"],
        help="Distance metric used by the stopping criterion.",
    )
    p.add_argument(
        "--incremental-window-size",
        type=int,
        default=3,
        help="Window size w for dma/ewma stopping criteria.",
    )
    p.add_argument(
        "--incremental-ewma-alpha",
        type=float,
        default=0.5,
        help="EWMA smoothing factor alpha in (0,1]; only used for ewma.",
    )
    p.add_argument("--gate2q-alpha", type=float, default=0.8)
    p.add_argument("--output-dir", type=Path, default=Path("benchmark_results"))
    p.add_argument("--mode-time-limit", type=float, default=None, metavar="SECONDS")
    p.add_argument("--mode-memory-limit-gb", type=float, default=None, metavar="GB")
    p.add_argument("--parallel-circuits", type=int, default=1)
    p.add_argument("--min-free-ram-gb", type=float, default=None)
    p.add_argument("--max-cpu-load", type=float, default=2.0)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    validate_incremental_args(args)
    return args


def resolve_modes(requested: Sequence[str]) -> List[str]:
    if not requested or "all" in requested:
        return list(ALL_MODES)
    seen, ordered = set(), []
    for m in requested:
        if m in ALL_MODES and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


# ---------------------------------------------------------------------------
# Run-level outcome tracking
# ---------------------------------------------------------------------------

@dataclass
class ModeOutcome:
    circuit_name: str
    n_qubits: int
    mode: str
    status: str
    elapsed_s: float
    error: str = ""


@dataclass
class RunReport:
    outcomes: List[ModeOutcome] = _field(default_factory=list)
    _lock: threading.Lock = _field(default_factory=threading.Lock, init=False, repr=False)

    def record_ok(self, spec, mode, elapsed_s):
        with self._lock:
            self.outcomes.append(
                ModeOutcome(spec.circuit_name, spec.n_qubits, mode, "ok", elapsed_s)
            )

    def record_fail(self, spec, mode, elapsed_s, exc):
        with self._lock:
            self.outcomes.append(
                ModeOutcome(
                    spec.circuit_name, spec.n_qubits, mode, "failed", elapsed_s,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    @property
    def n_ok(self):
        with self._lock:
            return sum(1 for o in self.outcomes if o.status == "ok")

    @property
    def n_failed(self):
        with self._lock:
            return sum(1 for o in self.outcomes if o.status == "failed")


def _now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _print_mode_start(spec, mode):
    print(
        f"    » [{_now()}]  (#{spec.id}) {spec.n_qubits}q / {spec.circuit_name} / {mode}  starting...",
        flush=True,
    )


def _print_mode_status(spec, mode, status, elapsed_s, error=""):
    icon = "✓" if status == "ok" else "✗"
    timing = f"{elapsed_s:6.1f}s"
    base = f"    {icon} [{timing}]  (#{spec.id}) {spec.n_qubits}q / {spec.circuit_name} / {mode}"
    print(f"{base}  --  {error}" if error else base, flush=True)


# ---------------------------------------------------------------------------
# Sandboxing
# ---------------------------------------------------------------------------

def _apply_memory_limit(limit_bytes):
    if platform.system() == "Windows":
        return
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    new = min(limit_bytes, hard) if hard != resource.RLIM_INFINITY else limit_bytes
    resource.setrlimit(resource.RLIMIT_AS, (new, hard))


def _sandbox_worker(fn, fn_args, memory_limit_bytes):
    if memory_limit_bytes is not None:
        _apply_memory_limit(memory_limit_bytes)
    return fn(*fn_args)


def _run_in_sandbox(fn, fn_args, *, time_limit_s, memory_limit_bytes):
    if time_limit_s is None and memory_limit_bytes is None:
        return fn(*fn_args)
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_sandbox_worker, fn, fn_args, memory_limit_bytes)
        try:
            return future.result(timeout=time_limit_s)
        except FuturesTimeout:
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(f"{fn.__name__} exceeded {time_limit_s:.0f}s")
        except Exception as exc:
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Per-mode dispatch
# ---------------------------------------------------------------------------

def _dispatch_mode(
    mode: str,
    spec: CircuitSpec,
    args: Any,
    ideal_backend: AerSimulator,
    noisy_backend: AerSimulator,
    perf_exp_val: float,
) -> Dict[str, Any]:
    gate2q_alpha = getattr(args, "gate2q_alpha", 0.8)

    if mode == "noisy_vanilla":
        return run_vanilla(
            spec.circuit_qasm, spec.observable, args.shots,
            noisy_backend, args.noisy_backend, perf_exp_val, args.seed_simulator,
        )
    if mode == "ideal_aer":
        return run_vanilla(
            spec.circuit_qasm, spec.observable, args.shots,
            ideal_backend, args.ideal_backend, perf_exp_val, args.seed_simulator,
        )

    if mode not in _MODE_TO_ALLOC:
        raise ValueError(f"Unknown mode {mode!r}")
    allocation_mode, incremental = _MODE_TO_ALLOC[mode]

    if not incremental:
        return run_cutting(
            spec.circuit_qasm, spec.observable, args.shots,
            noisy_backend, args.noisy_backend, perf_exp_val, args.seed_simulator,
            allocation_mode=allocation_mode,
            show_progress=False,
            gate2q_alpha=gate2q_alpha,
        )
    return run_cutting_incremental(
        spec.circuit_qasm, spec.observable, args.shots,
        noisy_backend, args.noisy_backend, perf_exp_val, args.seed_simulator,
        batch_shots=args.incremental_batch_shots,
        threshold=args.incremental_threshold,
        offset=args.incremental_offset,
        stability_k=args.incremental_stability_k,
        initial_batch_shots=getattr(args, "incremental_initial_batch_shots", None),
        allocation_mode=allocation_mode,
        show_progress=False,
        gate2q_alpha=gate2q_alpha,
        stopping_name=args.incremental_stopping_criterion,
        distance_metric=args.incremental_distance_metric,
        window_size=args.incremental_window_size,
        ewma_alpha=args.incremental_ewma_alpha,
    )


def _worker_run_one_mode(
    mode, spec, noisy_backend_name, ideal_backend_name,
    shots, seed_simulator,
    incremental_initial_batch_shots, incremental_batch_shots,
    incremental_threshold, incremental_offset, incremental_stability_k,
    incremental_stopping_criterion, incremental_distance_metric,
    incremental_window_size, incremental_ewma_alpha,
    gate2q_alpha, circuit_dir, perf_exp_val, noisy_value, memory_limit_bytes,
):
    import warnings
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated", category=UserWarning)
    if memory_limit_bytes is not None:
        _apply_memory_limit(memory_limit_bytes)

    _IDEAL_ONLY = {"ideal_aer"}
    ib = make_backend(ideal_backend_name) if mode in _IDEAL_ONLY else None
    nb = make_backend(noisy_backend_name) if mode not in _IDEAL_ONLY else None

    class _Args:
        pass

    args = _Args()
    args.shots = shots
    args.noisy_backend = noisy_backend_name
    args.ideal_backend = ideal_backend_name
    args.seed_simulator = seed_simulator
    args.incremental_initial_batch_shots = incremental_initial_batch_shots
    args.incremental_batch_shots = incremental_batch_shots
    args.incremental_threshold = incremental_threshold
    args.incremental_offset = incremental_offset
    args.incremental_stability_k = incremental_stability_k
    args.incremental_stopping_criterion = incremental_stopping_criterion
    args.incremental_distance_metric = incremental_distance_metric
    args.incremental_window_size = incremental_window_size
    args.incremental_ewma_alpha = incremental_ewma_alpha
    args.gate2q_alpha = gate2q_alpha

    _run_one_mode(mode, spec, args, ib, nb, circuit_dir, perf_exp_val, noisy_value)


def _run_one_mode_sandboxed(
    mode, spec, args, circuit_dir, perf_exp_val, noisy_value,
    time_limit_s, memory_limit_bytes,
    ideal_backend=None, noisy_backend=None,
):
    import warnings
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated", category=UserWarning)
    if time_limit_s is None and memory_limit_bytes is None:
        _IDEAL_ONLY = {"ideal_aer"}
        ib = ideal_backend or (make_backend(args.ideal_backend) if mode in _IDEAL_ONLY else None)
        nb = noisy_backend or (make_backend(args.noisy_backend) if mode not in _IDEAL_ONLY else None)
        _run_one_mode(mode, spec, args, ib, nb, circuit_dir, perf_exp_val, noisy_value)
        return

    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _worker_run_one_mode,
            mode, spec,
            args.noisy_backend, args.ideal_backend,
            args.shots, args.seed_simulator,
            getattr(args, "incremental_initial_batch_shots", None),
            args.incremental_batch_shots, args.incremental_threshold,
            args.incremental_offset, args.incremental_stability_k,
            args.incremental_stopping_criterion,
            args.incremental_distance_metric,
            args.incremental_window_size,
            args.incremental_ewma_alpha,
            getattr(args, "gate2q_alpha", 0.8),
            circuit_dir, perf_exp_val, noisy_value, memory_limit_bytes,
        )
        try:
            future.result(timeout=time_limit_s)
        except FuturesTimeout:
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(f"mode exceeded {time_limit_s:.0f}s")
        except MemoryError:
            raise
        except Exception as exc:
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def _run_one_mode(
    mode, spec, args, ideal_backend, noisy_backend,
    circuit_dir, perf_exp_val, noisy_value,
):
    payload = _dispatch_mode(mode, spec, args, ideal_backend, noisy_backend, perf_exp_val)
    _, _, value = extract_payload_value(payload)
    payload["distance_to_noisy_vanilla"] = abs(value - noisy_value)
    row = summarise_payload(
        payload, spec.circuit_name, spec.n_qubits,
        spec.observable, mode, noisy_value,
    )
    save_mode_result(circuit_dir, mode, payload, row)


# ---------------------------------------------------------------------------
# Per-circuit orchestration
# ---------------------------------------------------------------------------

def _run_circuit(
    spec: CircuitSpec,
    selected_modes: List[str],
    args: argparse.Namespace,
    output_dir: Path,
    report: RunReport,
) -> None:
    circuit_dir = _circuit_dir(output_dir, spec)

    time_limit_s = getattr(args, "mode_time_limit", None)
    memory_limit_bytes = (
        int(args.mode_memory_limit_gb * 1024**3)
        if getattr(args, "mode_memory_limit_gb", None) is not None else None
    )

    if time_limit_s is None and memory_limit_bytes is None:
        pre_ideal = make_backend(args.ideal_backend)
        pre_noisy = make_backend(args.noisy_backend)
    else:
        pre_ideal = pre_noisy = None

    print(
        f"    » [{_now()}]  {spec.n_qubits}q / {spec.circuit_name}"
        f"  observable={spec.observable}  computing ground truth (PennyLane default.qubit)...",
        flush=True,
    )
    t0 = time.perf_counter()
    try:
        perf_exp_val = _run_in_sandbox(
            exact_expectation_value, (spec.circuit_qasm, spec.observable),
            time_limit_s=time_limit_s, memory_limit_bytes=memory_limit_bytes,
        )
        elapsed = time.perf_counter() - t0
        print(f"    · [{elapsed:6.1f}s]  ground_truth = {perf_exp_val:.6f}", flush=True)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"    ✗ [{elapsed:6.1f}s]  ground_truth failed -- {exc}", flush=True)
        for mode in selected_modes:
            report.record_fail(spec, mode, 0.0, RuntimeError("skipped: ground truth failed"))
        return

    noisy_value: float = 0.0
    t0 = time.perf_counter()
    try:
        _print_mode_start(spec, "noisy_vanilla")
        _run_one_mode_sandboxed(
            "noisy_vanilla", spec, args, circuit_dir,
            perf_exp_val, noisy_value=0.0,
            time_limit_s=time_limit_s, memory_limit_bytes=memory_limit_bytes,
            ideal_backend=pre_ideal, noisy_backend=pre_noisy,
        )
        elapsed = time.perf_counter() - t0
        _, _, noisy_value = extract_payload_value(
            json.loads((circuit_dir / "noisy_vanilla.json").read_text())
        )
        if "noisy_vanilla" in selected_modes:
            report.record_ok(spec, "noisy_vanilla", elapsed)
            _print_mode_status(spec, "noisy_vanilla", "ok", elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        report.record_fail(spec, "noisy_vanilla", elapsed, exc)
        _print_mode_status(spec, "noisy_vanilla", "failed", elapsed, str(exc))
        for mode in selected_modes:
            if mode != "noisy_vanilla":
                report.record_fail(spec, mode, 0.0, RuntimeError("skipped: noisy_vanilla failed"))
        return

    for mode in selected_modes:
        if mode == "noisy_vanilla":
            continue
        t0 = time.perf_counter()
        try:
            _print_mode_start(spec, mode)
            _run_one_mode_sandboxed(
                mode, spec, args, circuit_dir,
                perf_exp_val, noisy_value,
                time_limit_s=time_limit_s, memory_limit_bytes=memory_limit_bytes,
                ideal_backend=pre_ideal, noisy_backend=pre_noisy,
            )
            elapsed = time.perf_counter() - t0
            report.record_ok(spec, mode, elapsed)
            _print_mode_status(spec, mode, "ok", elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            report.record_fail(spec, mode, elapsed, exc)
            _print_mode_status(spec, mode, "failed", elapsed, str(exc))
            logger.error("%s / %s failed: %s", spec.circuit_name, mode, exc, exc_info=True)


def _circuit_worker(idx, total, spec, selected_modes, args, report):
    print(
        f"\n[{idx}/{total}] {spec.n_qubits}q / {spec.circuit_name}"
        f"  observable: {spec.observable}",
        flush=True,
    )
    _run_circuit(spec, selected_modes, args, args.output_dir, report)


def _write_run_log(output_dir, report, selected_modes, args=None):
    log_path = output_dir / "run.log"
    by_circuit = defaultdict(list)
    for o in report.outcomes:
        by_circuit[o.circuit_name].append(o)

    lines = [
        "=" * 72,
        "CUTTABLE CIRCUIT BENCHMARK RUN REPORT",
        f"Timestamp : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        "=" * 72,
        "",
        "DESIGN",
        "-" * 72,
        "  Input        : valid_circuits.pkl produced by generate_and_cut.py",
        "  Cut strategy : find_and_place_cuts + CutStrategy(min_free_wires=2)",
        "  Ground truth : PennyLane default.qubit statevector (shots=None)",
        "  Reconstruction: qcut_processing_fn (tensor contraction)",
        "  Observable   : per-circuit Pauli string stored in CircuitSpec.observable",
        "  Error        : |perf_exp_val - result|",
        "",
    ]

    if args is not None:
        lines += ["CONFIGURATION", "-" * 72]
        fields = [
            ("shots", "Shots (B)"),
            ("noisy_backend", "Noisy backend"),
            ("ideal_backend", "Ideal backend"),
            ("seed_simulator", "Simulator seed"),
            ("output_dir", "Output dir"),
            ("gate2q_alpha", "2q-gate alpha"),
            ("incremental_batch_shots", "Incr. batch shots"),
            ("incremental_threshold", "Incr. threshold"),
            ("incremental_offset", "Incr. offset"),
            ("incremental_stability_k", "Incr. stability k"),
            ("incremental_stopping_criterion", "Incr. stopping criterion"),
            ("incremental_distance_metric", "Incr. distance metric"),
            ("incremental_window_size", "Incr. window size"),
            ("incremental_ewma_alpha", "Incr. EWMA alpha"),
        ]
        w = max(len(lbl) for _, lbl in fields) + 2
        for attr, lbl in fields:
            lines.append(f"  {lbl:<{w}}: {getattr(args, attr, None)!r}")
        lines.append(f"  {'Selected modes':<{w}}: {', '.join(selected_modes)}")
        lines.append("")

    lines += [
        "SUMMARY", "-" * 72,
        f"Total: {len(report.outcomes)}  ok: {report.n_ok}  failed: {report.n_failed}",
        "",
        "RESULTS BY CIRCUIT", "-" * 72,
    ]

    for cname, outcomes in by_circuit.items():
        lines.append(f"  {outcomes[0].n_qubits}q / {cname}")
        for o in outcomes:
            icon = "✓" if o.status == "ok" else "✗"
            line = f"    {icon} [{o.elapsed_s:6.1f}s]  {o.mode}"
            if o.error:
                line += f"\n             ERROR: {o.error}"
            lines.append(line)
        lines.append("")

    failed = [o for o in report.outcomes if o.status == "failed"]
    if failed:
        lines += ["-" * 72, "FAILED MODES", "-" * 72]
        for o in failed:
            lines += [f"  {o.n_qubits}q / {o.circuit_name} / {o.mode}", f"    {o.error}"]
        lines.append("")

    lines.append("=" * 72)
    log_path.write_text("\n".join(lines) + "\n")
    return log_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    selected_modes = resolve_modes(args.modes)
    circuits = load_pickled_cuttable_circuits(args.circuits_pkl, args.circuit_index)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    parallel = max(1, args.parallel_circuits)
    min_free_ram = args.min_free_ram_gb or (args.mode_memory_limit_gb or 0.0)
    report = RunReport()
    total = len(circuits)

    if parallel == 1:
        for idx, spec in enumerate(circuits, 1):
            _circuit_worker(idx, total, spec, selected_modes, args, report)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        futures = {}
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for idx, spec in enumerate(circuits, 1):
                if min_free_ram > 0 or args.max_cpu_load < float("inf"):
                    _wait_for_resources(min_free_ram, args.max_cpu_load)
                f = pool.submit(_circuit_worker, idx, total, spec, selected_modes, args, report)
                futures[f] = spec
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    logger.error("Worker error for %s: %s", futures[f].circuit_name, exc, exc_info=True)

    print(f"\n{'=' * 60}", flush=True)
    print(f"  Finished : {report.n_ok:>4}  mode runs", flush=True)
    print(f"  Failed   : {report.n_failed:>4}  mode runs", flush=True)
    print(f"{'=' * 60}", flush=True)

    merged = merge_csv_files(args.output_dir)
    log_path = _write_run_log(args.output_dir, report, selected_modes, args)
    print(f"\nMerged CSV -> {merged}", flush=True)
    print(f"Run log    -> {log_path}", flush=True)
    print(f"Per-mode JSON -> {args.output_dir}/<qubits>q/<circuit>/<mode>.json", flush=True)


if __name__ == "__main__":
    main()