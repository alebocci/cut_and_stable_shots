# Artifact paper handoff: ICSoC 2026

This file is a factual brief for drafting the required six-page artifact paper with ChatGPT or another writing assistant. It is not itself the submission paper. Replace every placeholder and independently check the final manuscript against the official call.

## Submission constraints from the official call

- Venue: ICSoC 2026 Artifact Evaluation track.
- Artifact-paper deadline: **28 September 2026, 23:59 AoE**.
- Format: Springer LNCS, English, **exactly six pages**.
- Required title: **Artifact for 'Adaptive Shot Management for Quantum Circuit-Cutting Services'**.
- Review: single-blind; the artifact does not need anonymization.
- Persistent repository: public DOI/permanent link strongly encouraged; Zenodo is recommended.
- Required content: step-by-step reproduction, execution and result interpretation, hardware/software setup, data origin and collection, licensing, and any special resources or accounts.
- Containers are encouraged. A simplified integrity/executability path is appropriate when the full campaign is impractical.
- Official source: <https://icsoc2026.it.p.lodz.pl/call-artifacts.html>.

## Bibliographic facts

- Main-paper title: **Adaptive Shot Management for Quantum Circuit-Cutting Services**.
- Authors: Alessandro Bocci (corresponding), Giuseppe Bisicchia, Ernesto Pimentel, Antonio Brogi.
- Corresponding email: `alessandro.bocci@unipi.it`.
- Main replication repository named in the accepted paper: <https://github.com/alebocci/cut_and_stable_shots>.
- Persistent artifact DOI: **[TODO: create a versioned Zenodo release and insert DOI]**.
- Artifact license: **MIT**, matching <https://github.com/GBisi/stableshots>.

## One-paragraph artifact characterization

The artifact packages the implementation, inputs, raw outputs, aggregate data, figures, and orchestration used to evaluate StableShots as an adaptive execution-control layer for circuit-cutting services. It exposes the evaluated composition as `Cut → allocate upper bounds → StableShots batch/check/stop → backend execution → Sew`. The controller consumes a circuit execution unit, maximum shot allocation, backend, and stopping configuration; it returns an empirical estimate plus explicit resource-accounting evidence. The package deliberately does not claim to be a complete SOQ platform or a networked microservice: it demonstrates a modular, governed execution stage that can sit between existing cutting and sewing components.

## Artifact badges/characteristics to request

- **Open Artifacts** after the MIT-licensed repository is deposited in a public immutable archive.
- **Verified Artifacts** based on the standard-library archived-results validator, the executable smoke invocation, full raw evidence, and optional complete campaign path.
- Available: all source, generated circuits, configurations, results, logs, aggregate CSV, analysis notebook, plots, accepted paper, Dockerfile, and checksums.
- Reusable: declarative invocation, configurable controller, safe parameterized campaign runner, and local fake backends with no cloud credentials.
- Reproducible scope: the paper’s tested observable, circuits, fake-backend models, budgets, modes, seed, and StableShots setting.

## Claims and evidence mapping

| Main-paper claim | Evidence | Verification action |
|---|---|---|
| 70 circuits, 4 fake backends, 3 budgets, 5 modes (4,200 observations) | `analysis/data/paper_results.csv`; `results/new/stable_shots3/` | `python3 artifact/validate_results.py` checks balanced coverage, uniqueness, and 84 complete campaign cells |
| Adaptive execution does not exceed its maximum allocation | aggregate `shots_executed` and per-variant raw JSON | Validator checks `0 < shots_executed <= budget`; inspect any adaptive JSON `cut_statistics` block |
| More than 25% average shot savings | aggregate CSV | Validator recomputes savings from budget and executed shots |
| Savings grow at higher budgets (about 7%, 32%, 56%) | aggregate CSV and lower paper plot | Validator prints mean savings by budget |
| SS-Sub/SS-Qub errors remain close to matched CC baselines in evaluated groups | aggregate CSV and upper paper plot | Validator prints errors and matched 21-group summaries; notebook contains the paper analysis |
| Peak grouped savings around 67% | grouped aggregate data | Validator identifies the maximum qubit/budget/policy group |
| Exact runner configuration | raw JSON and `run.log` files | Inspect `results/new/stable_shots3/<budget>_shots/<backend>/<q>_qubits/` |

Do not phrase “close” as statistical equivalence or universal accuracy preservation. The accepted paper reports Wilcoxon tests on seven qubit-size aggregates; failure to reject a zero median difference is not proof of equivalence.

## Experimental design facts

- Workload: 70 seeded synthetic Clifford+T circuits; 10 circuits at each size 10–16 qubits.
- Structure: bipartite circuits with a mix of intra- and cross-partition CNOT gates.
- Cut selection: PennyLane automatic KaHyPar-based cutting; instances retained only when they produce valid multi-fragment decompositions under configured constraints.
- Forced cutting: deliberate experimental control. The circuits can fit the selected fake backends; the study does not claim cutting is required for them.
- Backends: `aer.fake_torino`, `aer.fake_sherbrooke`, `aer.fake_kawasaki`, `aer.fake_kyoto`.
- Budgets: 5,000; 10,000; 20,000 shots.
- Seed: 42.
- Observable: Pauli Z on qubit 0 and identity on all other qubits.
- Reference: noiseless PennyLane statevector expectation.
- Error: absolute difference between the estimated and exact expectation values.
- StableShots paper configuration: delta stopping criterion, TVD distance, batch size 50, lookback offset 3, threshold 0.05, stability count 5. The config file is `configs/stable_shots3.json`.
- Modes: VAN (`noisy_vanilla`), CC-Sub (`cut_divided_budget`), CC-Qub (`cut_qubit_prop`), SS-Sub (`cut_incremental_budget`), SS-Qub (`cut_incremental_qubit_prop`).
- Pairing rule: compare SS-Sub with CC-Sub and SS-Qub with CC-Qub.
- Sewing: PennyLane `qcut_processing_fn`, yielding the selected observable rather than a complete reconstructed probability distribution.

## Reproduction levels and expected effort

### Level A — archived-data verification

Command: `python3 artifact/validate_results.py`.

Dependencies: Python 3 standard library only. Expected time: seconds. This validates coverage, row uniqueness, budget invariants, and recomputes the principal descriptive claims.

### Level B — basic executability and integrity

Install `requirements.txt` or build the Docker image. Run the JSON smoke request with `artifact/invoke_service.py`. It selects one 10-qubit circuit, one fake backend, one 5,000-shot budget, and a matched fixed/adaptive policy pair. Expected time: minutes, highly CPU-dependent. The end-to-end check in the preparation environment completed the fixed mode in 88.2 seconds and the adaptive mode in 319.5 seconds (about 6.9 minutes total excluding setup); record the final clean-host specification before quoting this in the paper. Evidence appears in `reproduced/smoke/`.

### Level C — complete recomputation

Command: `python artifact/run_campaign.py --jobs 1`. This schedules 84 cells and 4,200 mode evaluations. It is CPU- and memory-intensive and can require multiple days. Increase concurrency only with roughly 8 GB free RAM per job. No special quantum hardware or paid/cloud account is required.

The paper should explicitly tell reviewers that Level A plus Level B is the reasonable evaluation path and that Level C exists for full recomputation.

## Data provenance and collection

The dataset is generated by `src/new_create_circ_CL.py`, stored as PennyLane-operation pickles plus CSV summaries in `data/circuits/paper/`. The campaign runner loads these fixed inputs, creates the noiseless reference, executes fixed/adaptive modes against local Qiskit Aer fake-backend noise models, performs sewing, and writes one JSON per circuit/mode plus a summary and run log. The flattened `analysis/data/paper_results.csv` contains all balanced final campaign observations. There are no personal data, human subjects, or proprietary cloud results.

## Software and hardware description

- Recommended host: Linux x86-64, Python 3.11, multi-core CPU.
- Core dependencies: declared in `requirements.txt`; the verified container snapshot is `environment/requirements-lock.txt` (PennyLane 0.36, Qiskit/Aer, PennyLane-Qiskit, KaHyPar, NetworkX, NumPy, and supporting packages).
- Optional notebook dependencies: `environment/requirements-analysis.txt`.
- Container: root `Dockerfile`; local fake-backend execution only.
- RAM: approximately 8 GB per concurrent campaign cell; the original launcher enforced a 7 GB per-job virtual-memory limit and retained 4 GB for the OS.
- GPU: not required.
- Network at execution time: not required after dependencies/image are installed.
- IBM Quantum account/API key: not required.

## Service-oriented framing to preserve in the artifact paper

Use these points:

1. The invocation owns a maximum resource budget; the adaptive controller may consume less but never more.
2. Cut selection, controller, backend, and sewing are separately named roles, supporting composability.
3. The controller observes measurement outcomes and uses no provider internals, supporting backend interoperability at the evaluated interface.
4. Configuration, consumed shots, and stopping evidence are explicit governance information.
5. Each execution unit stops autonomously within its assigned cap, but local stability does not imply global post-sewing stability.
6. The experiment measures shots directly. It does not claim proportional savings in latency, queue time, or money.

Avoid saying the artifact implements a production web service, complete SOQ platform, distributed scheduler, real-hardware integration, or provider billing model.

## Suggested six-page structure

1. **Introduction and artifact scope (0.5 page):** link to main paper, state claims and badges, insert repository DOI.
2. **Service-oriented architecture (0.75 page):** diagram the five stages and explain component boundaries/governance.
3. **Artifact contents and provenance (0.75 page):** map source, inputs, raw/aggregate results, logs, figures, and checksums.
4. **Setup (1 page):** native and Docker paths, software/hardware requirements, no IBM credentials.
5. **Evaluation instructions (2 pages):** Levels A/B/C, exact commands, expected files, result interpretation, pairwise comparisons.
6. **Claims, limitations, and reuse (0.75 page):** evidence table, forced cutting, one observable, fake backends, runtime scope.
7. **Licensing/availability and conclusion (0.25 page):** DOI, licenses, contact.

Adjust for LNCS references and figure space while keeping the final PDF exactly six pages.

## Mandatory actions before deposit/submission

1. Build and run the Docker image on a clean x86-64 host; record the tested Docker and host versions.
2. Run `python3 artifact/validate_results.py` and the smoke invocation from a fresh clone.
3. Generate and verify `MANIFEST.sha256` after all final files are frozen.
4. Create a version tag/release, upload the release to Zenodo, reserve/publish the DOI, and replace the DOI placeholder.
5. Confirm the repository and Zenodo record are public and accessible without authentication.
6. Insert measured Level A and Level B runtimes from the clean test host.
7. Ensure the artifact paper’s title matches the required form exactly and the final PDF is exactly six pages.

## Prompt seed for the paper-writing model

> Draft an exactly six-page Springer LNCS artifact paper titled “Artifact for 'Adaptive Shot Management for Quantum Circuit-Cutting Services'”. Use only facts in this handoff and the repository README; do not invent a DOI, runtime, hardware measurement, or result. State that the artifact uses the MIT License. Present the artifact as a modular adaptive execution-control layer between cut and sew, not as a complete SOQ platform. Include a compact architecture figure, an artifact inventory table, three reproduction levels, exact commands, expected evidence, data provenance, limitations, and availability/licensing. Preserve the matched comparisons SS-Sub/CC-Sub and SS-Qub/CC-Qub. State that local subcircuit stability does not guarantee complete post-sewing distribution stability and that the evaluation reports one observable, not the full distribution. Mark unresolved fields visibly as TODO.
