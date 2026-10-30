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

## Camera-ready numerical-repeatability check (29 October 2026)

Two fresh executions of the Level B smoke request were supplied for comparison. Each used circuit `0_Clifford+T_001`, backend `aer.fake_torino`, seed 42, and a 5,000-shot maximum. The same numerical values and per-variant measurement counts were observed in both executions; only timing metadata changed.

| Mode | Fresh result (both runs) | Fresh absolute error (both runs) | Executed shots | Archived June 2026 absolute error |
|---|---:|---:|---:|---:|
| VAN | -0.866400 | 0.133600 | 5,000 | 0.331200 |
| CC-Sub | -0.9883928608818366 | 0.01160713911816269 | 5,000 | 0.2738450441169692 |
| SS-Sub | -0.9257329447187358 | 0.07426705528126354 | 5,000 | 0.32801186782078473 |

The historical values are from `results/new/stable_shots3/5000_shots/torino/10_qubits/summary.csv`. Fresh runs are numerically repeatable within the tested environment, but their results are not identical to the historical campaign. These differences exceed fifth/sixth-decimal rounding. The original campaign software environment is not available, and no specific cause for the discrepancy has been verified.

Run `artifact/compare_smoke.py` with `--reference` and `--observed` to report matched numeric and shot differences. With no `--atol`, the comparison is diagnostic only. `--atol 0` passed on the two fresh runs in the tested environment; no cross-environment numerical tolerance is established. Level A validates the archived campaign's reported descriptive results; Level B validates execution, response completeness, finite numeric outputs, and shot accounting.

**Release status:** This section records an additional author-supplied validation after the frozen 27 September release. It does not replace that release's measurements or imply that the complete Level C campaign was rerun.

## v1.0.1 release-candidate validation (30 October 2026)

This section records the revalidation of the camera-ready artifact corrections prepared in pull request #1, using commit `547a966f09ec5a540f1644d8c05c8f1a54dcf792` on branch `artifact/camera-ready-review-fixes-20261029`.

The corrections improve standalone archive integrity verification, Level B response validation, numerical comparison tooling, and reviewer documentation. The StableShots algorithm, original experimental inputs, archived results, and frozen v1.0.0 release remain unchanged.

### Validation environment

| Item | Value |
|---|---|
| Validation date | 30 October 2026 |
| Host OS | Ubuntu 22.04.5 LTS (Jammy), x86-64 |
| Host Python | 3.12.12 |
| Docker | 29.7.2, build a7dcaa6 |
| Container base image | `python:3.11-slim` |
| Container Python | Python 3.11 (exact patch version not recorded) |
| Tested source commit | `547a966f09ec5a540f1644d8c05c8f1a54dcf792` |
| Docker build | PASS (113.9 s) |

### Validation results

| Gate | Command / scope | Result |
|---|---|---|
| Level A, host | `python3 artifact/validate_results.py` | PASS |
| Level A, container | Archived-result validation in Docker | PASS |
| Level B, first execution | Docker smoke request, `smoke-1` | PASS |
| Level B, second execution | Docker smoke request, `smoke-2` | PASS |
| Repeated-run comparison | `compare_smoke.py --atol 0` | PASS |
| Historical numerical comparison | Comparison against archived June campaign | DIAGNOSTIC COMPLETED |
| Manifest, Git checkout | `generate_manifest.py --check` | PASS |
| GNU SHA-256 verification | `sha256sum -c MANIFEST.sha256` | PASS |
| Manifest, standalone ZIP | Verification without Git metadata | PASS |
| Tampering detection | Intentional file modification in extracted ZIP | PASS |

The integrity-check outcomes are author-reported. The standalone ZIP was generated from the committed source available at the time of testing. The complete final release archive must be rechecked after the release-candidate changes are committed.

### Level A — archived-result verification

Level A completed successfully on both the host and inside the rebuilt Docker container. Both executions returned `StableShots ICSoC 2026 artifact validation: PASS`.

The validator confirmed 4,200 observations covering 70 circuits, four simulated noisy backends, three shot budgets, five execution modes, and 84 balanced campaign cells.

The reproduced aggregate values were:

| Metric | Value |
|---|---:|
| Mean absolute error, VAN | 0.126322 |
| Mean absolute error, CC-Sub | 0.080566 |
| Mean absolute error, CC-Qub | 0.077187 |
| Mean absolute error, SS-Sub | 0.081003 |
| Mean absolute error, SS-Qub | 0.081725 |
| Overall adaptive shot savings | 31.81% |
| Savings at 5,000 shots | 7.30% |
| Savings at 10,000 shots | 32.36% |
| Savings at 20,000 shots | 55.77% |
| Peak grouped savings | 66.92% |

Peak grouped savings occurred for SS-Qub at 16 qubits and a 20,000-shot budget.

Matched-policy summaries reported median error differences of +0.0004 for SS-Sub/CC-Sub and +0.0053 for SS-Qub/CC-Qub, with corresponding median shot savings of 34.2% and 32.7%.

The preserved controller configuration was confirmed: delta stopping, TVD distance, batch size 50, lookback offset 3, threshold 0.05, and stability count 5.

### Level B — end-to-end execution

Two independent executions of the supplied Level B smoke request completed successfully.

Both used circuit `0_Clifford+T_001` (10 qubits), backend `aer.fake_torino`, simulator seed 42, a maximum allocation of 5,000 shots, and the matched CC-Sub/SS-Sub policies. Each response also included the auxiliary VAN baseline.

| Execution | Completed requested modes | Failed modes | Response validation |
|---|---:|---:|---|
| `smoke-1` | 2 | 0 | PASS |
| `smoke-2` | 2 | 0 | PASS |

Measured execution times were:

| Mode | First execution | Second execution |
|---|---:|---:|
| CC-Sub | 70.9 s | 67.3 s |
| SS-Sub | 284.8 s | 243.1 s |

Both executions returned `Service response validation: PASS`, confirming complete responses, finite numerical outputs, and consistent requested, executed, and saved shot accounting.

Dependency deprecation and partition-attempt warnings were emitted but did not prevent completion.

### Level B — numerical repeatability

The two independently generated response summaries were compared using:

```bash
python3 artifact/compare_smoke.py \
  --reference reproduced/smoke-1/summary.csv \
  --observed reproduced/smoke-2/summary.csv \
  --atol 0
```

| Mode | Absolute result difference | Absolute error difference | Executed-shot difference |
|---|---:|---:|---:|
| VAN | 0 | 0 | 0 |
| CC-Sub | 0 | 0 | 0 |
| SS-Sub | 0 | 0 | 0 |

**Comparison: PASS (3 matched rows, atol=0, shots equal).**

The two executions produced exactly matching numerical results, absolute errors, and shot counts under the tested execution conditions.

### Comparison with the historical campaign

The first smoke execution was also compared against the corresponding archived June 2026 campaign results using `artifact/compare_smoke.py` without an acceptance tolerance.

| Mode | Absolute result difference | Absolute error difference | Executed-shot difference |
|---|---:|---:|---:|
| VAN | 0.197600000 | 0.197600000 | 0 |
| CC-Sub | 0.262237905 | 0.262237905 | 0 |
| SS-Sub | 0.253744813 | 0.253744813 | 0 |

**Comparison: DIAGNOSTIC (3 matched rows; no acceptance tolerance applied).**

These differences substantially exceed fifth- or sixth-decimal rounding, although the executed-shot counts agree.

The original campaign software environment is unavailable, and no specific cause for the discrepancies has been verified. No cross-environment numerical acceptance tolerance is asserted.

Level A validates the archived aggregate numerical results. Level B validates end-to-end executability, response completeness, finite numerical outputs, and resource accounting. Exact repeatability within the tested environment does not establish numerical identity with the archived campaign.

### Manifest and archive integrity

The corrected `artifact/generate_manifest.py` supports SHA-256 verification in both Git checkouts and standalone archive extractions without Git metadata.

The author reports successful completion of the following checks:

- Manifest regeneration followed by `generate_manifest.py --check`.
- GNU verification using `sha256sum -c MANIFEST.sha256`.
- Standalone ZIP extraction and manifest verification without Git metadata.
- Negative tampering test confirming that modifying `artifact/README.md` causes manifest verification to fail.

The manifest must be regenerated after the final documentation changes, and the committed release archive must undergo a final integrity check before publication.

### Release status

**VALIDATION COMPLETED — ALL REQUIRED CHECKS PASSED.**

Level A passed on both the host and Docker. Both Level B executions completed successfully, and the repeated-run comparison confirmed exact numerical agreement (`--atol 0`) across all three modes.

The historical comparison was completed as a diagnostic and confirmed previously documented numerical discrepancies. Their cause remains unverified; they do not constitute a failure of the Level B executability check.

The SHA-256 manifest was verified successfully against 23,914 files. Verification also passed on a standalone ZIP extraction without Git metadata, and intentional file modification was correctly detected.

The validated release candidate is ready for GitHub pull-request review, merging, tagging, and publication as `icsoc26-artifact-v1.0.1`, followed by a new Zenodo deposit version.

The original `icsoc26-artifact-v1.0.0` release remains unchanged.