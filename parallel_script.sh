#!/usr/bin/env bash
set -uo pipefail

QUBITS=(10 11 12 13 14 15 16)
MAX_JOBS=7   # use 7 to run all at once; lower it, e.g. 3, if RAM/CPU is tight

SHOTS=10000
BACKEND="aer.fake_kyoto"
SEED=42
CONFIG="configs/ewma_tvd_002.json"
ROOT_OUT="results/new/ewma_10k_kyoto_01"

mkdir -p logs

run_one() {
  local q="$1"
  local out_dir="${ROOT_OUT}/${q}_qubits"

  mkdir -p "$out_dir"

  echo "[$(date)] Starting ${q} qubits"

  python main_new_new.py \
    --circuits-pkl "only_cliffordt/clif${q}.pkl" \
    --shots "$SHOTS" \
    --noisy-backend "$BACKEND" \
    --seed-simulator "$SEED" \
    --modes noisy_vanilla cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
    --incremental-config "$CONFIG" \
    --parallel-circuits 1 \
    --output-dir "$out_dir"

  echo "[$(date)] Finished ${q} qubits"
}

running=0

for q in "${QUBITS[@]}"; do
  run_one "$q" > "logs/clif${q}.log" 2>&1 &

  ((running++))

  if (( running >= MAX_JOBS )); then
    wait -n
    ((running--))
  fi
done

wait

echo "All jobs finished."