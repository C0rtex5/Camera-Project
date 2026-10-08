#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Foreground process: keep this terminal open, or run under a service manager.
export SENTINEL_ENABLE_DEMO="${SENTINEL_ENABLE_DEMO:-true}"
exec .venv/bin/python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
