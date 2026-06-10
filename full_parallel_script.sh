#!/usr/bin/env bash
set -uo pipefail

QUBITS=(10 11 12 13 14 15 16)

SHOTS_LIST=(5000 10000 20000)

BACKENDS=(
  "aer.fake_torino"
  "aer.fake_sherbrooke"
  "aer.fake_kawasaki"
  "aer.fake_kyoto"
)

MAX_JOBS=84   # max number of parallel jobs

SEED=42
CONFIG="configs/stable_shots.json"

ROOT_OUT="results/new/stable_shots_runs_01"

mkdir -p logs

run_one() {
  local q="$1"
  local shots="$2"
  local backend="$3"

  local backend_label="${backend##*.}"
  backend_label="${backend_label#fake_}"

  local out_dir="${ROOT_OUT}/${shots}_shots/${backend_label}/${q}_qubits"

  mkdir -p "$out_dir"

  echo "[$(date)] Starting ${q} qubits | shots=${shots} | backend=${backend}"

  python main_new_new.py \
    --circuits-pkl "only_cliffordt/clif${q}.pkl" \
    --shots "$shots" \
    --noisy-backend "$backend" \
    --seed-simulator "$SEED" \
    --modes cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
    --incremental-config "$CONFIG" \
    --parallel-circuits 1 \
    --output-dir "$out_dir"

  echo "[$(date)] Finished ${q} qubits | shots=${shots} | backend=${backend}"
}

running=0
failed=0

for shots in "${SHOTS_LIST[@]}"; do
  for backend in "${BACKENDS[@]}"; do
    for q in "${QUBITS[@]}"; do

      backend_label="${backend##*.}"
      backend_label="${backend_label#fake_}"
      log_file="logs/clif${q}_${shots}_${backend_label}.log"

      run_one "$q" "$shots" "$backend" > "$log_file" 2>&1 &

      ((running++))

      if (( running >= MAX_JOBS )); then
        wait -n || failed=1
        ((running--))
      fi

    done
  done
done

while (( running > 0 )); do
  wait -n || failed=1
  ((running--))
done

if (( failed != 0 )); then
  echo "At least one job failed. Skipping git add/commit/push."
  exit 1
fi

echo "All jobs finished successfully."

# Git add, commit and push new results/logs
git add "$ROOT_OUT" logs

if git diff --cached --quiet; then
  echo "No new files to commit."
else
  git commit -m "Add stable shots runs for multiple shots and backends"
  git push
fi
