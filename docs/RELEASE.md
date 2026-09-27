# Release and archive metadata

This file records the exact release text and the metadata to copy into GitHub and Zenodo. It intentionally contains no DOI until Zenodo assigns one.

## GitHub release

- Tag: `icsoc26-artifact-v1.0.0`
- Title: `ICSoC 2026 Artifact v1.0.0`
- Target: the annotated tag above (the frozen, validated commit)

Suggested release body:

> Frozen artifact for “Adaptive Shot Management for Quantum Circuit-Cutting Services” (ICSoC 2026). The recommended reviewer path is Level A (`python3 artifact/validate_results.py`) plus Level B (`artifact/requests/smoke.json` via the native or Docker route). Level C recomputes 84 campaign cells and may take multiple days. No quantum hardware, IBM Quantum account, API key, or paid service is required; execution uses local Qiskit Aer fake backends. See `artifact/README.md` for exact commands, `artifact/VALIDATION.md` for release-host results, `MANIFEST.sha256` for integrity, and `LICENSES.md` for the split license mapping.

## Zenodo deposit metadata

- Upload type: Software
- Title: `Adaptive Shot Management for Quantum Circuit-Cutting Services — ICSoC 2026 Artifact`
- Version: `1.0.0`
- Publication date: use the actual Zenodo publication date; do not backdate it
- Creators, in order:
  1. Alessandro Bocci
  2. Giuseppe Bisicchia
  3. Ernesto Pimentel
  4. Antonio Brogi
- Description: `Frozen replication artifact for the ICSoC 2026 paper “Adaptive Shot Management for Quantum Circuit-Cutting Services”. It includes source code, generated circuit inputs, archived raw and aggregate results, validation and campaign entry points, a service-like invocation contract, and a Docker environment. The recommended evaluation combines archived-data validation with one end-to-end local fake-backend smoke run.`
- Keywords: `quantum computing`, `circuit cutting`, `adaptive shots`, `service-oriented computing`, `reproducibility`, `artifact evaluation`
- Related identifier: `https://github.com/alebocci/cut_and_stable_shots`, relation `isSupplementTo`
- License field: choose `MIT` for the software record only if Zenodo permits a single license; state prominently in the description that data/results are CC BY 4.0 and publication-controlled/third-party material follows `LICENSES.md`
- Access: Open

After Zenodo assigns the version DOI, add it to the GitHub release notes and the artifact paper. Do not move the version tag; if repository metadata must be updated, create a later release and preserve this frozen version.
