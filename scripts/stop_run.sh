#!/bin/sh
# Graceful stop: in-flight batches finish and are saved, checkpoint_before.json is written, then the process exits.
cd "$(dirname "$0")/.."
PID=$(cat "runs/$1/pid") && kill -TERM "$PID" && echo "stop requested for run $1 (pid $PID)"
