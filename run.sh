#!/bin/sh
# First run sets up Python dependencies, then starts DeployTrack at http://127.0.0.1:8000
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PY="$(command -v python3.12 || command -v python3)"
  "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
