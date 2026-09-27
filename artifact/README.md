# Reviewer guide

This directory is the executable front door of the artifact. It adds orchestration and validation around the preserved research code; it does not reimplement or alter the method.

## Recommended evaluation sequence

1. Run `python3 artifact/validate_results.py` to verify completeness, invariants, and the paper-level archived-data claims.
2. Run `python3 artifact/invoke_service.py --request artifact/requests/smoke.json --dry-run` to inspect the service contract and delegated command.
3. Install `requirements.txt` locally or build the `Dockerfile`.
4. Remove `--dry-run` to execute one matched CC-Sub/SS-Sub comparison.
5. Inspect `summary.csv`, `run.log`, and the two per-mode JSON files under the response directory.
6. Only if resources permit, preview and run `artifact/run_campaign.py`.

## Service contract

`requests/smoke.json` models a service invocation with these fields:

- `circuit`: the workload and selected circuit index;
- `maximum_shot_budget`: the governed resource ceiling;
- `backend`: a replaceable execution provider (a local fake backend here);
- `modes`: fixed and adaptive policies to evaluate;
- `adaptive_controller.config`: the independently configurable batch/check/stop policy;
- `response_directory`: the service response and evidence location.

The façade deliberately remains a local CLI. The paper evaluates an execution-control layer suitable for a composed quantum service; it does not claim to implement service discovery, networking, authentication, tenancy, provider billing, or queue management.

## Interpreting a response

For each mode, compare:

- `absolute_error`: absolute distance from the noiseless Pauli-Z expectation;
- `shots_requested`: the invocation budget;
- `shots_executed`: measured backend consumption;
- `shots_saved`: requested minus executed shots;
- `result`: reconstructed observable estimate.

The fixed and adaptive comparisons are matched by allocation policy. Compare `cut_incremental_budget` only with `cut_divided_budget`, and `cut_incremental_qubit_prop` only with `cut_qubit_prop`.

An adaptive run is not expected to save shots for every circuit. A successful artifact execution means the command completes, writes internally consistent evidence, and demonstrates that adaptive execution never exceeds its assigned maximum—not that every single invocation must stop early.
