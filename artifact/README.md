# ICSoC 2026 artifact reviewer guide

This directory is the executable front door for the artifact accompanying **“Adaptive Shot Management for Quantum Circuit-Cutting Services.”** It wraps the preserved experiment code with validation and orchestration; it does not reimplement the method or change the archived numerical results.

The recommended evaluation is **Level A plus Level B**. Level A verifies the archived campaign with only the Python standard library. Level B exercises one complete fixed/adaptive comparison. Level C is optional because it recomputes the full 84-cell campaign and may take multiple days.

No quantum computer, IBM Quantum account, API key, paid service, or execution-time network connection is required. Simulation uses local Qiskit Aer fake backends.

## Service-like artifact boundary

```text
request + maximum shot budget
              |
              v
       Cut -> allocate
              |
              v
 StableShots batch/check/stop <-> local backend
              |
              v
             Sew
              |
              v
 estimate + error + resource-accounting evidence
```

`requests/smoke.json` is the declarative service contract. It identifies the circuit execution unit, maximum shot allocation, replaceable backend, selected policy modes, adaptive-controller configuration, seed, and response directory. The façade is intentionally a local command-line interface: the paper presents a modular execution-control layer, not a production web service or complete Service-Oriented Quantum platform.

## Prerequisites

- Linux x86-64 is the tested platform.
- For Level A and dry runs: Python 3.11 or another recent Python 3.
- For native Level B/C: Python 3.11, a C/C++-compatible package environment, and the dependencies in `requirements.txt`.
- For the container route: Docker with enough local storage to build the image.
- GPU: not required.
- Memory for Level C: budget approximately 8 GB of available RAM per concurrent job.

Run every command below from the repository root.

## Level A — verify archived evidence

```bash
python3 artifact/validate_results.py
```

Expected result: exit status 0 and a first line ending in `validation: PASS`. The validator checks:

- exactly 4,200 unique observations;
- 70 circuits, with 10 circuits at each size from 10 through 16 qubits;
- four fake backends, three budgets, five modes, and 84 balanced campaign cells;
- the Pauli-Z-on-qubit-0 observable;
- the exact `stable_shots3` controller configuration;
- positive shot use that never exceeds the maximum budget;
- full-budget consumption by the fixed policies;
- the reported savings ranges and matched CC-Sub/SS-Sub and CC-Qub/SS-Qub summaries.

This route reads `analysis/data/paper_results.csv` and does not need third-party Python packages.

## Level B — basic executability

First inspect the request and delegated command without importing the experiment dependencies:

```bash
python3 artifact/invoke_service.py \
  --request artifact/requests/smoke.json \
  --dry-run
```

The command should print `Cut -> Allocate -> StableShots batch/check/stop -> Execute -> Sew` and a delegated `src/main.py` invocation. The supplied request selects circuit 0 from the 10-qubit dataset, a 5,000-shot maximum, `aer.fake_torino`, seed 42, and the matched CC-Sub/SS-Sub policies.

### Native environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python artifact/invoke_service.py --request artifact/requests/smoke.json
```

`.venv/` is ignored and must not be committed or archived. The exact container-resolved snapshot is in `environment/requirements-lock.txt`; optional notebook dependencies are separate in `environment/requirements-analysis.txt`.

### Docker environment

```bash
docker build -t stableshots-icsoc26 .
docker run --rm --entrypoint python stableshots-icsoc26 \
  artifact/validate_results.py
mkdir -p reproduced
docker run --rm \
  -v "$PWD/reproduced:/artifact-output" \
  stableshots-icsoc26 \
  --request artifact/requests/smoke.json \
  --output /artifact-output/smoke
```

The image contains only the final paper inputs and aggregate validation data, not the complete archived `results/` tree. On ARM machines, use an amd64-compatible container environment if the pinned wheels are unavailable.

### Expected Level B evidence

Inspect the response directory:

- `reproduced/smoke/summary.csv`: one row per requested mode plus the auxiliary VAN baseline used for distance comparisons, including absolute error and requested/executed/saved shots;
- `reproduced/smoke/run.log`: effective configuration and execution status;
- nested per-mode JSON files: parameters, timing, fragment/variant structure, reconstructed result, and shot accounting.

The response validator requires CC-Sub, SS-Sub, and the auxiliary VAN row, and checks every row's shot accounting. The adaptive mode must not exceed its 5,000-shot ceiling. It need not stop early for every circuit; successful execution means the request completes and its evidence is internally consistent. Measured release-host runtimes and checks are recorded in `artifact/VALIDATION.md`.

## Level C — complete recomputation

Preview all commands without execution:

```bash
python3 artifact/run_campaign.py --dry-run
```

Expected first line: `Campaign cells: 84`.

Run sequentially:

```bash
python3 artifact/run_campaign.py --jobs 1
```

Outputs default to `reproduced/paper-campaign/`. Each cell contains 10 circuits and five modes, yielding 4,200 mode evaluations overall. The full run is CPU- and memory-intensive and can take multiple days. Begin with one job; increase `--jobs` only when approximately 8 GB of free RAM is available per job. The artifact runner performs no Git operation.

For provenance only, `scripts/full_parallel_script.original.sh` preserves the original laboratory launcher and includes Git operations. Reviewers should not execute it; use `artifact/run_campaign.py`.

## Interpreting modes and results

| Repository mode | Paper label | Interpretation |
|---|---|---|
| `noisy_vanilla` | VAN | Uncut noisy execution |
| `cut_divided_budget` | CC-Sub | Fixed allocation divided uniformly among execution units |
| `cut_qubit_prop` | CC-Qub | Fixed allocation weighted by fragment qubit count |
| `cut_incremental_budget` | SS-Sub | Adaptive execution bounded by the CC-Sub allocation |
| `cut_incremental_qubit_prop` | SS-Qub | Adaptive execution bounded by the CC-Qub allocation |

Compare SS-Sub only with CC-Sub, and SS-Qub only with CC-Qub. `absolute_error` is the distance from the noiseless statevector expectation; `shots_executed` is measured backend consumption; `shots_saved` is the maximum budget minus consumption. Sewing uses PennyLane's `qcut_processing_fn` for the selected Pauli-Z observable, not a complete reconstructed probability distribution.

## Integrity check

Verify that every intended release file matches the frozen manifest:

```bash
python3 artifact/generate_manifest.py --check
```

Equivalent GNU command:

```bash
sha256sum -c MANIFEST.sha256
```

The manifest is sorted and deliberately excludes itself, Git metadata, ignored virtual environments and bytecode caches, and transient `reproduced/` or `artifact-output/` directories. Maintainers regenerate it only after all release files are final:

```bash
python3 artifact/generate_manifest.py
```

## Known limitations

- Cutting is deliberately forced as an experimental control; the evaluated circuits fit the selected fake backends.
- Results cover one Pauli-Z observable, not complete output distributions.
- Local execution-unit stability does not guarantee global stability after sewing.
- Fake-backend simulations do not reproduce hardware queues, drift, provider billing, or end-to-end service latency.
- Shot savings must not be presented as proportional monetary or latency savings.
- The study's non-significant paired tests are not proof of statistical equivalence.

## Licensing and citation

Software and software documentation are MIT-licensed. Author-generated circuit data and experimental result evidence are CC BY 4.0. The accepted manuscript and publication-controlled figures retain their publication-agreement terms; third-party components retain their original licenses. The authoritative path mapping is `LICENSES.md`. Citation metadata is in `CITATION.cff`.

## Troubleshooting

- Run from the repository root; wrappers resolve repository inputs independently, but output examples assume that location.
- If `python3` is not Python 3.11, use `python3.11` for native Level B/C.
- If native package installation fails, use the Docker route.
- If a Docker smoke run cannot write output, confirm that `reproduced/` exists and that Docker may write to the bind-mounted directory.
- Do not treat exploratory notebook output as the primary validator; use Level A for deterministic claim checks.
