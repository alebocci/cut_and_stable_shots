# Release validation record

This record documents the checks performed for the `icsoc26-artifact-v1.0.0` release candidate on 27 September 2026 (UTC). Generated smoke outputs were written under `/tmp` and are not part of the release archive.

## Validation environment

| Item | Value |
|---|---|
| Host OS | Ubuntu 24.04.4 LTS, Linux 6.8.0-134-generic, x86-64 |
| CPU | 2 × Intel Xeon Gold 5120 at 2.20 GHz; 32 logical CPUs total |
| Memory | 31 GiB RAM; 4 GiB swap |
| Host Python | 3.12.3 (used for standard-library validation and dry runs) |
| Container Python | 3.11 (`python:3.11-slim`) |
| Docker | 29.3.0, build 5927d80 |
| Git | 2.43.0 |
| Final image ID | `sha256:e97add96061b64563ad5a259444b7cf744bbc65bc5294a8730dd98dfb5021769` |

The image was rebuilt from the Dockerfile and `environment/requirements-lock.txt`. The final clean dependency build completed in 9 min 25.67 s; download throughput dominated that measurement. `python -m pip check` and the release gates below were run after the corrective dependency pin.

## Results

| Gate | Command/scope | Result | Measured time / evidence |
|---|---|---|---|
| Python syntax | Compile all four artifact entry points | PASS | No syntax errors |
| Citation metadata | Parse `CITATION.cff` as YAML | PASS | CFF 1.2.0; four authors |
| Level A, host | `python3 artifact/validate_results.py` | PASS | 0.15 s wall; 24,320 KiB max RSS |
| Level A, container | Same validator in `stableshots-icsoc26` | PASS | Same 4,200-row summaries |
| Level B dry run | Smoke JSON through `invoke_service.py --dry-run` | PASS | Correct service stages, inputs, backend, seed, modes, and config |
| Level B real run | Docker smoke request, bind-mounted temporary output | PASS | 9 min 10.58 s wall; all requested modes completed; response validation PASS |
| Level C dry run | `python3 artifact/run_campaign.py --dry-run` | PASS | Exactly 84 campaign cells / 84 delegated commands |
| Container cleanliness | Search image for `.venv` and `__pycache__` directories | PASS | Neither present |
| Git/repository hygiene | Credential, absolute-path, ignored-cache, and tracked-venv checks | PASS | No credential or active developer-path finding; no tracked environment |
| Manifest | `python3 artifact/generate_manifest.py --check` | PASS | Run after final repository freeze; entry count printed by the tool |

Level C was deliberately not executed: it performs 4,200 mode evaluations and can require multiple days. Its complete command plan was generated and checked instead.

## Archived-data observations reproduced by Level A

- Coverage: 70 circuits × 4 backends × 3 budgets × 5 modes = 4,200 rows; 84 balanced campaign cells.
- Mean absolute error: VAN 0.126322, CC-Sub 0.080566, CC-Qub 0.077187, SS-Sub 0.081003, SS-Qub 0.081725.
- Mean adaptive shot savings: 31.81% overall; 7.30% at 5,000, 32.36% at 10,000, and 55.77% at 20,000 shots.
- Peak grouped savings: 66.92% for SS-Qub at 16 qubits and a 20,000-shot budget.
- Matched summaries: SS-Sub/CC-Sub median error delta +0.0004 and median savings 34.2%; SS-Qub/CC-Qub median error delta +0.0053 and median savings 32.7%.
- Configuration: delta stopping, TVD, batch 50, lookback offset 3, threshold 0.05, stability count 5; simulator seed evidence is 42.

These descriptive checks do not establish statistical equivalence or claims beyond the tested observable/configuration.

## Level B response evidence

The final smoke used circuit `0_Clifford+T_001` (10 qubits), `aer.fake_torino`, seed 42, and a maximum allocation of 5,000 shots.

| Mode | Role | Mode time | Requested | Executed | Saved | Absolute error |
|---|---|---:|---:|---:|---:|---:|
| `noisy_vanilla` | Auxiliary VAN baseline | 4.43 s | 5,000 | 5,000 | 0 | 0.133600 |
| `cut_divided_budget` | CC-Sub requested fixed policy | 114.67 s | 5,000 | 5,000 | 0 | 0.011607 |
| `cut_incremental_budget` | SS-Sub requested adaptive policy | 417.84 s | 5,000 | 5,000 | 0 | 0.074267 |

The adaptive policy legitimately used its full cap in this individual smoke run. Early stopping is not required for every circuit. The gate verifies successful end-to-end execution and the invariant `0 < shots_executed <= maximum_shot_budget`; campaign-level savings are verified from the archived 4,200-row dataset.

## Preparation issue caught by the gates

An initial image upgraded setuptools beyond the declared `<81` compatibility constraint, causing the preserved Qiskit provider dependency to fail when importing `pkg_resources`. The Dockerfile was corrected to install the lock without upgrading setuptools independently, and `setuptools==80.9.0` was added to the exact lock. The service façade was also hardened to return nonzero when the runner produces an incomplete response. The final image, validation, and smoke measurements above are from the corrected configuration.
