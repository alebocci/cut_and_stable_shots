# cut_and_stable_shots

This repository contains experimental code for generating cuttable quantum circuits and benchmarking different execution strategies for noisy quantum simulation.

The main workflow is:

1. **Create valid cuttable circuits** with `new_create_circ_CL.py`.
2. **Run benchmark experiments** with `main.py`.
3. **Analyse and compare the results** with `analyser.ipynb`.

The experiments compare standard noisy circuit execution against different circuit-cutting strategies, including fixed shot-allocation policies and incremental/stable-shot execution.

---

## Repository structure

```text
cut_and_stable_shots/
├── circuits/                 # Generated circuit datasets and summary CSV files
├── configs/                  # JSON configuration files for incremental/stable-shot runs
├── logs/                     # Logs produced by batch/scripted executions
├── results/                  # Experimental results
│   ├── new/                  # Current result organization
│   └── old/                  # Older result organization / previous experiments
├── analyser.ipynb            # Notebook used to aggregate, compare, and plot results
├── main.py                   # Main benchmark runner
├── main_new.py               # Alternative / older benchmark variant
├── main_new_new.py           # Alternative / older benchmark variant
├── new_create_circ_CL.py     # Circuit generation and validation script
├── parallel_script.sh        # Helper script for parallel runs
├── full_parallel_script.sh   # Helper script for full batch runs
└── requirements.txt
```

---

## Installation

Create and activate a Python environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the main dependencies used by the scripts:

```bash
pip install pennylane qiskit qiskit-aer qiskit-ibm-runtime kahypar networkx numpy pandas matplotlib
```

The circuit cutting step uses PennyLane’s `qcut` tools and requires `kahypar` for automatic graph partitioning.

---

## 1. Creating circuits: `new_create_circ_CL.py`

`new_create_circ_CL.py` is the circuit-generation script. It creates quantum circuits, tries to cut them automatically, validates the produced fragments, and saves only the circuits that satisfy the repository constraints.

Although the file name is `new_create_circ_CL.py`, it is the script that plays the role of the “create circuit” stage.

### What it does

The script:

1. Builds candidate PennyLane circuits.
2. Converts the circuit into a graph representation used by PennyLane’s circuit cutting tools.
3. Runs automatic cut placement.
4. Splits the circuit into fragments.
5. Checks whether the fragments satisfy the required constraints.
6. Saves the valid circuits to a pickle file.
7. Writes a CSV summary describing the generated circuits.

### Circuit families

The script contains builders for several circuit families, including:

* QFT
* QAOA
* Hardware-efficient ansatz
* Clifford+T
* Trotter-Ising
* Quantum Volume

In the current version, the active family list is focused on **Clifford+T** circuits.

### Validation constraints

A generated circuit is accepted only if:

* it has at least two fragments;
* every fragment has at least two qubits;
* every fragment has fewer qubits than the original full circuit;
* every fragment respects the `--max_frag_qubits` limit;
* the total number of cutting variations, computed as `4 ** num_cuts`, does not exceed `--max_variations`.

This is important because circuit cutting can quickly become expensive as the number of cuts grows.

### Example usage

```bash
python new_create_circ_CL.py \
  --n 11 \
  --k 10 \
  --seed 42 \
  --max_frag_qubits 30 \
  --max_variations 30 \
  --timeout 30 \
  --outdir circuits \
  --output_file 11q.pkl
```

### Main arguments

| Argument            | Meaning                                           |
| ------------------- | ------------------------------------------------- |
| `--n`               | Number of qubits in the generated circuits        |
| `--k`               | Number of valid circuits to generate              |
| `--seed`            | Random seed for reproducibility                   |
| `--max_frag_qubits` | Maximum allowed number of qubits in each fragment |
| `--max_variations`  | Maximum allowed value of `4 ** num_cuts`          |
| `--timeout`         | Maximum time allowed for a single cut attempt     |
| `--outdir`          | Output folder for generated circuits              |
| `--output_file`     | Name of the pickle file to save                   |

### Output files

The script writes files such as:

```text
circuits/11q.pkl
circuits/11q_summary.csv
```

The `.pkl` file contains the valid circuit objects used later by `main.py`.

The summary CSV contains one row per valid circuit, with fields such as:

* circuit id;
* circuit family;
* number of qubits;
* total number of gates;
* number of two-qubit gates;
* number of fragments;
* fragment sizes;
* number of cuts;
* total cutting variations;
* cutting time.

---

## 2. Running experiments: `main.py`

`main.py` is the main benchmark runner. It loads circuits generated by `new_create_circ_CL.py`, executes them with different strategies, computes errors, and writes detailed result files.

### What `main.py` does

At a high level, `main.py`:

1. Loads a pickle file containing valid circuits.
2. Converts the stored PennyLane operations into executable circuit representations.
3. Computes the exact reference expectation value using an ideal PennyLane statevector simulation.
4. Runs one or more benchmark modes.
5. Executes noisy simulations using Aer backends or fake IBM backends.
6. For cutting-based modes, cuts the circuit into fragments, executes the fragments, and sews the results back together.
7. Computes the absolute error between the estimated result and the exact reference value.
8. Saves per-circuit and per-mode JSON files.
9. Merges the run outputs into a `summary.csv`.
10. Writes a `run.log`.

The benchmark observable used by the script is a Pauli-Z observable on the first qubit, equivalent to:

```text
Z ⊗ I ⊗ I ⊗ ...
```

The main error metric is:

```text
absolute_error = |exact_expectation_value - estimated_expectation_value|
```

### Execution modes

The relevant modes are:

| Mode                         | Description                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------- |
| `noisy_vanilla`              | Executes the original uncut circuit on a noisy backend.                                           |
| `cut_divided_budget`         | Cuts the circuit and divides the total shot budget across the required fragment executions.       |
| `cut_qubit_prop`             | Cuts the circuit and allocates shots proportionally to fragment size.                             |
| `cut_incremental_budget`     | Uses circuit cutting with incremental/stable-shot execution and a budget-based allocation policy. |
| `cut_incremental_qubit_prop` | Uses circuit cutting with incremental/stable-shot execution and qubit-proportional allocation.    |

The incremental modes are designed to avoid using the full shot budget when the estimated fragment distributions become stable.

### Incremental / stable-shot logic

The incremental execution logic runs fragment circuits in batches. After each batch, it compares the current empirical distribution with previous distributions.

Supported stability criteria include:

* `delta`
* `dma`
* `ewma`

Supported distance metrics include:

* total variation distance, `tvd`;
* Hellinger distance;
* Jensen-Shannon distance.

The incremental configuration controls parameters such as:

* batch size;
* threshold;
* offset;
* window size;
* stability count;
* EWMA alpha.

These parameters can be passed manually through the command line or stored in JSON configuration files inside `configs/`.

### Example usage

```bash
python main.py \
  --circuits-pkl circuits/11q.pkl \
  --shots 20000 \
  --noisy-backend aer.fake_torino \
  --seed-simulator 42 \
  --modes noisy_vanilla cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
  --incremental-stopping-criterion ewma \
  --incremental-distance-metric tvd \
  --incremental-threshold 0.01 \
  --incremental-offset 3 \
  --incremental-window-size 3 \
  --incremental-ewma-alpha 0.5 \
  --incremental-stability-k 3 \
  --incremental-batch-shots 50 \
  --parallel-circuits 5 \
  --output-dir results/new/example_run/11_qubits
```

### Backend names

Examples of backend names used in the repository include:

```text
aer.perfect
aer.fake_torino
aer.fake_sherbrooke
aer.fake_kyoto
aer.fake_kawasaki
```

`aer.perfect` is useful for ideal or baseline simulations, while the fake backends simulate noisy IBM-style devices.

### Output of `main.py`

A typical run creates:

```text
results/new/example_run/11_qubits/
├── 11q/                 # Per-circuit and per-mode detailed outputs
├── run.log              # Human-readable run log
└── summary.csv          # Merged table used by the analyser
```

The detailed JSON outputs contain information such as:

* selected mode;
* exact expectation value;
* estimated expectation value;
* absolute error;
* number of shots executed;
* backend used;
* circuit statistics;
* timing information;
* incremental stopping information, when applicable.

The `summary.csv` file is the most important file for post-processing because it is what `analyser.ipynb` reads to compare modes.

---

## 3. Configuration files: `configs/`

The `configs/` folder contains JSON presets for stable-shot and incremental-shot experiments.

Examples include:

```text
configs/stable_shots.json
configs/stable_shots2.json
configs/stable_shots3.json
configs/ewma_tvd_001.json
configs/ewma_tvd_002.json
configs/ewma_tvd_003.json
configs/small.json
configs/large.json
```

These files define parameters such as:

```json
{
  "stopping_criterion": "ewma",
  "distance_metric": "tvd",
  "threshold": 0.01,
  "offset": 3,
  "window_size": 3,
  "stability_k": 3,
  "batch_shots": 50,
  "ewma_alpha": 0.5
}
```

Use these files to keep experiment settings reproducible across different runs.

---

## 4. Analysing results: `analyser.ipynb`

`analyser.ipynb` is the notebook used to read, aggregate, compare, and plot the results produced by `main.py`.

### What the analyser does

The analyser:

1. Loads `summary.csv` files from the `results/` folders.
2. Combines results across qubit counts, shot budgets, backends, and execution modes.
3. Computes aggregate statistics for each mode.
4. Compares modes by absolute error.
5. Compares modes by number of shots executed.
6. Counts which mode “wins” under different criteria.
7. Produces tables and plots for the final comparison.

### Modes analysed

The notebook defines the main modes as:

```python
MODES = [
    "noisy_vanilla",
    "cut_divided_budget",
    "cut_qubit_prop",
    "cut_incremental_budget",
    "cut_incremental_qubit_prop",
]
```

### Typical metrics

The notebook compares modes using quantities such as:

* mean absolute error;
* median absolute error;
* maximum absolute error;
* mean number of shots executed;
* shot savings;
* per-budget comparisons;
* per-backend comparisons;
* per-qubit comparisons.

### Stable-shot analysis

The notebook contains a `STABLE_SHOTS` flag. This controls whether the analysis follows the stable-shot result layout or the standard EWMA/budget/backend layout.

When analysing new runs, update the paths in the notebook so that they point to the correct folders inside `results/new/`.

---

## 5. Result folders

The `results/` directory stores the outputs of the benchmark experiments.

```text
results/
├── new/
└── old/
```

### `results/new/`

`results/new/` contains the current experiment organization.

Examples of folders found there include:

```text
results/new/ewma_20k_torino_01/
results/new/ewma_10k_kyoto_01/
results/new/ewma_5k_sherbrooke_01/
results/new/stable_shots_02/
results/new/stable_shots_runs_vanilla_allq/
```

The EWMA folders usually encode:

```text
<strategy>_<shot-budget>_<backend>_<run-id>
```

For example:

```text
ewma_20k_torino_01
```

means an EWMA-based incremental run with a 20k shot budget on the Torino fake backend.

Inside these folders, results are commonly grouped by qubit count:

```text
results/new/ewma_20k_torino_01/
├── 05_qubits/
├── 07_qubits/
├── 09_qubits/
├── 10_qubits/
├── 11_qubits/
├── 12_qubits/
├── 13_qubits/
├── 14_qubits/
├── 15_qubits/
└── 16_qubits/
```

A leaf folder typically contains:

```text
05_qubits/
├── 5q/
├── run.log
└── summary.csv
```

The `summary.csv` file is the main input for the analyser.

### Stable-shot result folders

Stable-shot experiments may be organized first by shot budget and then by backend:

```text
results/new/stable_shots_02/
├── 5000_shots/
├── 10000_shots/
└── 20000_shots/
```

Inside a shot-budget folder, results can be grouped by backend:

```text
5000_shots/
├── kawasaki/
├── kyoto/
├── sherbrooke/
└── torino/
```

This structure makes it easier to compare the same shot budget across multiple fake backends.

### `results/old/`

`results/old/` contains older experiments and previous folder layouts.

Examples include:

```text
results/old/20k_delta/
results/old/20k_dma/
results/old/20k_ewma_*/
results/old/benchmark_results_one/
results/old/benchmark_results_two/
results/old/benchmark_results_three/
```

These folders are useful for comparison or historical reference, but new analyses should usually focus on `results/new/`.

### `logs/`

The `logs/` folder stores raw logs from scripted runs.

Log file names usually encode:

* circuit family;
* number of qubits;
* shot budget;
* backend.

For example:

```text
clif10_20000_torino.log
```

This indicates a Clifford+T experiment with 10 qubits, 20,000 shots, and the Torino fake backend.

---

## 6. Suggested workflow

### Step 1: Generate circuits

```bash
python new_create_circ_CL.py \
  --n 11 \
  --k 10 \
  --seed 42 \
  --outdir circuits \
  --output_file 11q.pkl
```

This creates:

```text
circuits/11q.pkl
circuits/11q_summary.csv
```

### Step 2: Run benchmarks

```bash
python main.py \
  --circuits-pkl circuits/11q.pkl \
  --shots 20000 \
  --noisy-backend aer.fake_torino \
  --modes noisy_vanilla cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
  --output-dir results/new/example_run/11_qubits
```

This creates:

```text
results/new/example_run/11_qubits/summary.csv
results/new/example_run/11_qubits/run.log
```

plus detailed per-circuit outputs.

### Step 3: Analyse the results

Open:

```text
analyser.ipynb
```

Then update the paths in the notebook to point to the desired folders inside `results/new/`.

---

## 7. Notes

* Circuit cutting can become expensive very quickly because the number of variations scales as `4 ** num_cuts`.
* Use `--max_variations` during circuit generation to avoid circuits that are too expensive to benchmark.
* Use fixed seeds for reproducible circuit generation and simulation.
* Use smaller qubit counts and shot budgets for testing before launching large batch runs.
* The `env/` folder appears to be a local Python environment snapshot. For reproducible work, it is better to create a fresh virtual environment instead of relying on this folder.
