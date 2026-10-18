#!/bin/sh
# Start a pipeline run that survives closing the terminal/app and keeps the Mac awake while it runs.
#   scripts/run_detached.sh <run-id> <input.csv> [extra pipeline args...]
# Follow it:  .venv/bin/python -m pipeline watch --run-id <run-id>     Stop gracefully: scripts/stop_run.sh <run-id>
set -e
cd "$(dirname "$0")/.."
RUN="$1"; IN="$2"; shift 2
mkdir -p runs
nohup caffeinate -ims .venv/bin/python -m pipeline run --run-id "$RUN" --input "$IN" "$@" >> "runs/$RUN.console.log" 2>&1 &
echo "started run $RUN (log: runs/$RUN.console.log)"
