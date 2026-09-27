# StableShots for circuit-cutting services

This is the replication artifact for **“Adaptive Shot Management for Quantum Circuit-Cutting Services”** (ICSoC 2026). It evaluates an adaptive execution-control layer that runs cut-circuit execution units in small shot batches and stops each unit when its empirical distribution stabilizes.

The artifact preserves a service-oriented boundary:

```text
Circuit request + maximum budget
              |
              v
     [ Cut and allocate ]
              |
              v
 [ StableShots execution controller ] <--> [ noisy backend service ]
       batch / check / stop
              |
              v
            [ Sew ]
              |
              v
 estimate + error + shots consumed/saved
```

StableShots is the controller between cutting and sewing. It does not change the cut-selection or reconstruction procedures and is not presented as a complete Service-Oriented Quantum platform. The included service façade expresses one invocation declaratively and delegates to the preserved experiment runner.

## What can be reproduced

The paper evaluates 70 seeded synthetic Clifford+T circuits (10 circuits for each size from 10 to 16 qubits), four Qiskit fake backends, three maximum budgets, and five modes: 4,200 circuit–backend–budget–mode observations. The primary claims are:

- adaptive modes remain close in mean absolute error to their matched full-budget cutting baselines for the evaluated Pauli-Z observable;
- adaptive execution saves more than 25% of shots on average;
- mean savings increase from roughly 7% at 5,000 shots to 32% at 10,000 and 56% at 20,000, with grouped savings peaking around 67%.

The claims apply to the tested observable and configurations. They do not establish equivalence for every circuit, preservation of the complete reconstructed distribution, or provider-level latency/cost savings.

## Reviewer routes

### 1. Validate the archived results (seconds, Python standard library only)

```bash
python3 artifact/validate_results.py
```

Expected: `StableShots ICSoC 2026 artifact validation: PASS`, followed by coverage, error, and shot-saving summaries. This is the recommended first check when compute time is limited.

### 2. Inspect a service invocation without executing it

```bash
python3 artifact/invoke_service.py \
  --request artifact/requests/smoke.json \
  --dry-run
```

The JSON request exposes the circuit input, backend service, maximum shot budget, selected fixed/adaptive policies, controller configuration, and response directory.

### 3. Execute one fixed/adaptive comparison locally

Python 3.11 on Linux is recommended:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python artifact/invoke_service.py --request artifact/requests/smoke.json
```

The smoke request compares `cut_divided_budget` (CC-Sub) with `cut_incremental_budget` (SS-Sub) on one 10-qubit circuit and the local `aer.fake_torino` simulator. It requires no IBM account, network service, API key, or quantum hardware. Runtime depends strongly on the CPU and can be several minutes.

Inspect:

- `reproduced/smoke/summary.csv` for error and shot counts;
- `reproduced/smoke/run.log` for the complete configuration and status;
- `reproduced/smoke/10q/.../*.json` for per-mode parameters, timings, fragment structure, and per-variant allocations.

### 4. Re-run the paper campaign

Preview all 84 campaign cells:

```bash
python artifact/run_campaign.py --dry-run
```

Run them sequentially:

```bash
python artifact/run_campaign.py --jobs 1
```

The output defaults to `reproduced/paper-campaign/`. A full run is CPU- and memory-intensive and can take multiple days. The original campaign used a per-job virtual-memory limit of 7 GB; use approximately 8 GB of available RAM per concurrent `--jobs` slot. Start with one job. This safe runner never commits or pushes results.

## Container route

Build the executable environment:

```bash
docker build -t stableshots-icsoc26 .
```

Validate archived results:

```bash
docker run --rm --entrypoint python stableshots-icsoc26 \
  artifact/validate_results.py
```

Run the smoke request and retain its response:

```bash
mkdir -p reproduced
docker run --rm \
  -v "$PWD/reproduced:/artifact-output" \
  stableshots-icsoc26 \
  --request artifact/requests/smoke.json \
  --output /artifact-output/smoke
```

The container uses only local Qiskit Aer fake backends. On ARM hosts, package-wheel availability may require an `amd64` container or a native x86-64 machine.

## Execution modes and paper names

| Repository mode | Paper name | Role |
|---|---|---|
| `noisy_vanilla` | VAN | Uncut noisy execution |
| `cut_divided_budget` | CC-Sub | Fixed budget divided uniformly among execution units |
| `cut_qubit_prop` | CC-Qub | Fixed budget weighted by fragment qubit count |
| `cut_incremental_budget` | SS-Sub | StableShots bounded by the CC-Sub allocation |
| `cut_incremental_qubit_prop` | SS-Qub | StableShots bounded by the CC-Qub allocation |

The paper configuration is `configs/stable_shots3.json`: batch size 50, lookback offset 3, TVD threshold 0.05, and five consecutive stable checks. The simulator seed is 42.

## Repository map

| Path | Purpose |
|---|---|
| `artifact/` | Reviewer entry points, service request, validation, and safe campaign orchestration |
| `src/main.py` | Preserved cut/execute/adapt/sew benchmark implementation |
| `src/new_create_circ_CL.py` | Preserved synthetic circuit generator and cutability filter |
| `environment/requirements-lock.txt` | Exact dependency snapshot from the verified Python 3.11 container |
| `configs/` | StableShots controller configurations; `stable_shots3.json` is the paper setting |
| `data/circuits/paper/` | The 70 generated Clifford+T circuits and provenance summaries |
| `analysis/data/paper_results.csv` | Flattened 4,200-row dataset used for claim validation |
| `analysis/notebooks/analyser.ipynb` | Preserved exploratory/figure notebook; run from the repository root |
| `analysis/figures/` | Paper plots in PNG and PDF formats |
| `results/new/stable_shots3/` | Complete final per-run JSON, summaries, and logs |
| `results/` elsewhere | Calibration, earlier, and split campaign outputs retained for provenance |
| `results/campaign-logs/` | Original batch launcher logs |
| `paper/` | Accepted manuscript supplied with the repository |
| `scripts/full_parallel_script.original.sh` | Preserved original launcher; retained for provenance, not recommended because it performs Git operations |
| `docs/legacy/README.original.MD` | Unmodified pre-artifact README |
| `docs/legacy/gitignore.original` | Unmodified pre-artifact ignore rules |

All pre-existing files were moved without content edits. New wrappers point to the reorganized locations.

## Recreate figures interactively

Install the optional analysis dependencies and start Jupyter from the repository root so the preserved relative paths resolve:

```bash
source .venv/bin/activate
python -m pip install -r environment/requirements-analysis.txt
jupyter lab analysis/notebooks/analyser.ipynb
```

The notebook includes exploratory cells and stored outputs. For a fast, deterministic paper-claim check, prefer `artifact/validate_results.py`.

## Data origin and provenance

The inputs are synthetic, seeded Clifford+T circuits with a bipartite structure and intra-/cross-partition CNOT gates. PennyLane’s KaHyPar-based pipeline retains circuits that admit valid multi-fragment decompositions under the generator constraints. Cutting is deliberately forced for controlled evaluation; these 10–16-qubit circuits do not require cutting to fit the selected fake backends. Qiskit fake backends provide local noise models for Torino, Sherbrooke, Kawasaki, and Kyoto. No human, personal, or externally licensed dataset is used.

Per-circuit JSON files retain the OpenQASM circuit, parameters, backend, timings, fragment statistics, result, allocated/executed shots, and stopping behavior. `run.log` records each campaign cell’s effective configuration.

## Integrity, licensing, and publication

Use `MANIFEST.sha256` to check the core submission materials with `sha256sum -c MANIFEST.sha256`. The artifact is distributed under the [MIT License](LICENSE), matching the StableShots repository.

The ICSoC 2026 artifact call requests a persistent public repository (Zenodo recommended), clear execution and interpretation instructions, setup and data provenance, licensing, and disclosure of special resources. It encourages a container and permits a simplified verification route when full reproduction is expensive. See the [official Call for Artifacts](https://icsoc2026.it.p.lodz.pl/call-artifacts.html) and `docs/ARTIFACT-PAPER-HANDOFF.md`.

## Original paper

Alessandro Bocci, Giuseppe Bisicchia, Ernesto Pimentel, and Antonio Brogi. “Adaptive Shot Management for Quantum Circuit-Cutting Services.” Accepted at ICSoC 2026. The manuscript is retained at `paper/StableShotsCC_to_ICSOC26.pdf`.
