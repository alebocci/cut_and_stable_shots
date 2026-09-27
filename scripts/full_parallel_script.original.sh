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

# -------------------------------------------------------------------
# Parallelism
# -------------------------------------------------------------------
# Start conservatively. Increase only after checking RAM usage in logs.
MAX_JOBS=10

# -------------------------------------------------------------------
# Resource gate: tune these values for your VM
# -------------------------------------------------------------------
# Keep this much memory available for the OS, sshd, filesystem cache, etc.
MIN_FREE_RAM_GB=4

# Estimated RAM needed before starting one more job.
# The script starts a new job only if:
# MemAvailable >= MIN_FREE_RAM_GB + MEM_RESERVE_PER_JOB_GB
MEM_RESERVE_PER_JOB_GB=7

# Hard-ish per-job virtual memory limit.
# This is inherited by each Python process.
# If too low, Python may fail with MemoryError or allocation errors.
MEM_LIMIT_PER_JOB_GB=7

MAX_LOAD_PER_CORE_CENTI=75      # 90 means 0.90 load per CPU core
RESOURCE_CHECK_INTERVAL=30      # seconds between checks
RESOURCE_LOG_INTERVAL=120       # seconds between "waiting" messages

# Make these jobs preferred OOM-killer victims compared with system daemons.
# User processes can usually increase their own oom_score_adj without root.
# Range: -1000 to 1000. Higher means more likely to be killed.
OOM_SCORE_ADJ=800

SEED=42
CONFIG="configs/stable_shots3.json"

ROOT_OUT="results/new/stable_shots3"

mkdir -p logs

# -------------------------------------------------------------------
# Derived values
# -------------------------------------------------------------------
MIN_FREE_RAM_KB=$(( MIN_FREE_RAM_GB * 1024 * 1024 ))
MEM_RESERVE_PER_JOB_KB=$(( MEM_RESERVE_PER_JOB_GB * 1024 * 1024 ))
MEM_LIMIT_PER_JOB_KB=$(( MEM_LIMIT_PER_JOB_GB * 1024 * 1024 ))

get_cpu_count() {
  local n=1

  if command -v nproc >/dev/null 2>&1; then
    n="$(nproc)"
  elif command -v getconf >/dev/null 2>&1; then
    n="$(getconf _NPROCESSORS_ONLN)"
  fi

  if [[ ! "$n" =~ ^[0-9]+$ ]] || (( n <= 0 )); then
    n=1
  fi

  echo "$n"
}

CPU_COUNT="$(get_cpu_count)"
MAX_LOAD_CENTI=$(( CPU_COUNT * MAX_LOAD_PER_CORE_CENTI ))

# -------------------------------------------------------------------
# Make this script and its children easier to kill under OOM
# -------------------------------------------------------------------
set_oom_score_adj() {
  if [[ -w "/proc/$$/oom_score_adj" ]]; then
    echo "$OOM_SCORE_ADJ" > "/proc/$$/oom_score_adj" 2>/dev/null || true
  fi
}

set_oom_score_adj

# -------------------------------------------------------------------
# Resource helpers
# -------------------------------------------------------------------
mem_available_kb() {
  local key value unit

  if [[ ! -r /proc/meminfo ]]; then
    echo 0
    return
  fi

  while read -r key value unit; do
    if [[ "$key" == "MemAvailable:" ]]; then
      echo "$value"
      return
    fi
  done < /proc/meminfo

  echo 0
}

loadavg_1m_centi() {
  local load rest whole frac

  if [[ ! -r /proc/loadavg ]]; then
    echo 0
    return
  fi

  read -r load rest < /proc/loadavg

  whole="${load%.*}"
  frac="${load#*.}"
  frac="${frac}00"
  frac="${frac:0:2}"

  echo $(( 10#$whole * 100 + 10#$frac ))
}

format_gb_from_kb() {
  local kb="$1"
  local tenths=$(( kb * 10 / 1024 / 1024 ))
  printf "%d.%d" "$(( tenths / 10 ))" "$(( tenths % 10 ))"
}

format_centi() {
  local c="$1"
  printf "%d.%02d" "$(( c / 100 ))" "$(( c % 100 ))"
}

resources_ok() {
  local mem_kb
  local load_centi
  local required_kb

  mem_kb="$(mem_available_kb)"
  load_centi="$(loadavg_1m_centi)"

  required_kb=$(( MIN_FREE_RAM_KB + MEM_RESERVE_PER_JOB_KB ))

  (( mem_kb >= required_kb && load_centi <= MAX_LOAD_CENTI ))
}

last_resource_log=0

log_resource_wait() {
  local now
  local mem_kb
  local load_centi
  local required_kb

  now="$(date +%s)"

  if (( now - last_resource_log < RESOURCE_LOG_INTERVAL )); then
    return
  fi

  mem_kb="$(mem_available_kb)"
  load_centi="$(loadavg_1m_centi)"
  required_kb=$(( MIN_FREE_RAM_KB + MEM_RESERVE_PER_JOB_KB ))

  echo "[$(date)] Waiting before starting a new job: MemAvailable=$(format_gb_from_kb "$mem_kb")GB / required=$(format_gb_from_kb "$required_kb")GB, reserve_os=${MIN_FREE_RAM_GB}GB, reserve_job=${MEM_RESERVE_PER_JOB_GB}GB, load1=$(format_centi "$load_centi") / max=$(format_centi "$MAX_LOAD_CENTI")"

  last_resource_log="$now"
}

# -------------------------------------------------------------------
# Process cleanup helpers
# -------------------------------------------------------------------
list_descendants() {
  local parent="$1"
  local child

  pgrep -P "$parent" 2>/dev/null | while read -r child; do
    list_descendants "$child"
    echo "$child"
  done
}

kill_descendants() {
  local signal="$1"
  local pids=()

  mapfile -t pids < <(list_descendants "$$" | sort -rn)

  if (( ${#pids[@]} > 0 )); then
    kill "-$signal" "${pids[@]}" 2>/dev/null || true
  fi
}

cleanup() {
  echo "Interrupted. Killing running background jobs..."

  trap - INT TERM

  kill_descendants TERM
  sleep 5
  kill_descendants KILL

  exit 130
}

trap cleanup INT TERM

# -------------------------------------------------------------------
# Job management
# -------------------------------------------------------------------
running=0
failed=0

reap_one_job() {
  if wait -n; then
    :
  else
    failed=1
  fi

  ((running--))
}

wait_for_slot_and_resources() {
  while true; do
    while (( running >= MAX_JOBS )); do
      reap_one_job
    done

    if resources_ok; then
      return
    fi

    log_resource_wait
    sleep "$RESOURCE_CHECK_INTERVAL"
  done
}

# -------------------------------------------------------------------
# Benchmark job
# -------------------------------------------------------------------
run_one() {
  local q="$1"
  local shots="$2"
  local backend="$3"

  local backend_label="${backend##*.}"
  backend_label="${backend_label#fake_}"

  local out_dir="${ROOT_OUT}/${shots}_shots/${backend_label}/${q}_qubits"

  mkdir -p "$out_dir"

  echo "[$(date)] Starting ${q} qubits | shots=${shots} | backend=${backend}"
  echo "[$(date)] Per-job memory limit: ${MEM_LIMIT_PER_JOB_GB}GB virtual memory"

  # Make child processes from this job preferred OOM victims too.
  if [[ -w "/proc/$$/oom_score_adj" ]]; then
    echo "$OOM_SCORE_ADJ" > "/proc/$$/oom_score_adj" 2>/dev/null || true
  fi

  # Avoid hidden CPU oversubscription from BLAS/OpenMP libraries.
  export OMP_NUM_THREADS=1
  export OPENBLAS_NUM_THREADS=1
  export MKL_NUM_THREADS=1
  export NUMEXPR_NUM_THREADS=1
  export VECLIB_MAXIMUM_THREADS=1

  # Limit memory for this job and its Python children.
  if ! ulimit -v "$MEM_LIMIT_PER_JOB_KB"; then
    echo "[$(date)] ERROR: failed to set ulimit -v ${MEM_LIMIT_PER_JOB_KB}"
    return 1
  fi

  if [[ -x /usr/bin/time ]]; then
    /usr/bin/time -v python main.py \
      --circuits-pkl "only_cliffordt/clif${q}.pkl" \
      --shots "$shots" \
      --noisy-backend "$backend" \
      --seed-simulator "$SEED" \
      --modes noisy_vanilla cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
      --incremental-config "$CONFIG" \
      --parallel-circuits 1 \
      --output-dir "$out_dir"
  else
    python main.py \
      --circuits-pkl "only_cliffordt/clif${q}.pkl" \
      --shots "$shots" \
      --noisy-backend "$backend" \
      --seed-simulator "$SEED" \
      --modes noisy_vanilla cut_divided_budget cut_qubit_prop cut_incremental_budget cut_incremental_qubit_prop \
      --incremental-config "$CONFIG" \
      --parallel-circuits 1 \
      --output-dir "$out_dir"
  fi

  local status="$?"

  if (( status == 0 )); then
    echo "[$(date)] Finished ${q} qubits | shots=${shots} | backend=${backend}"
  else
    echo "[$(date)] FAILED ${q} qubits | shots=${shots} | backend=${backend} | exit_status=${status}"
  fi

  return "$status"
}

# -------------------------------------------------------------------
# Main loop
# -------------------------------------------------------------------
echo "CPU_COUNT=${CPU_COUNT}"
echo "MAX_JOBS=${MAX_JOBS}"
echo "MIN_FREE_RAM_GB=${MIN_FREE_RAM_GB}"
echo "MEM_RESERVE_PER_JOB_GB=${MEM_RESERVE_PER_JOB_GB}"
echo "MEM_LIMIT_PER_JOB_GB=${MEM_LIMIT_PER_JOB_GB}"
echo "MAX_LOAD_PER_CORE=$(format_centi "$MAX_LOAD_PER_CORE_CENTI")"
echo "MAX_LOAD_TOTAL=$(format_centi "$MAX_LOAD_CENTI")"

for shots in "${SHOTS_LIST[@]}"; do
  for backend in "${BACKENDS[@]}"; do
    for q in "${QUBITS[@]}"; do

      wait_for_slot_and_resources

      backend_label="${backend##*.}"
      backend_label="${backend_label#fake_}"
      log_file="logs/clif${q}_${shots}_${backend_label}.log"

      run_one "$q" "$shots" "$backend" > "$log_file" 2>&1 &

      ((running++))

      echo "[$(date)] Launched job: q=${q}, shots=${shots}, backend=${backend}, running=${running}, log=${log_file}"

    done
  done
done

while (( running > 0 )); do
  reap_one_job
done

if (( failed != 0 )); then
  echo "At least one job failed. Skipping git add/commit/push."
  exit 1
fi

echo "All jobs finished successfully."

git add "$ROOT_OUT" logs

if git diff --cached --quiet; then
  echo "No new files to commit."
else
  git commit -m "Add stable shots runs for multiple shots and backends"
  git push
fi
