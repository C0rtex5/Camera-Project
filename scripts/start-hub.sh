#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-auto}"
case "$mode" in auto|cpu|gpu) ;; *) echo 'Usage: start-hub.sh [auto|cpu|gpu]' >&2; exit 2;; esac
env_file="${SENTINEL_ENV_FILE:-.env.production}"
if [[ ! -f "$env_file" ]]; then
  echo "Copy .env.production.example to $env_file and configure the VPN address first." >&2
  exit 2
fi
base=(docker compose --env-file "$env_file" -f compose.yaml)
gpu=(docker compose --env-file "$env_file" -f compose.yaml -f compose.gpu.yaml)
# Use the Docker server architecture, not the laptop running this launcher.
arch=$(docker info --format '{{.Architecture}}')
case "$arch" in
  aarch64|arm64)
    base+=(-f compose.spark.yaml)
    gpu+=(-f compose.spark.yaml)
    echo 'ARM64 detected: using the DGX Spark CUDA 13 image.'
    ;;
esac
# Validate effective values without sourcing shell code from the environment file.
"${base[@]}" config --format json | python3 scripts/validate-deploy.py
# Build failures are errors, rather than hardware fallback.
if [[ "$mode" != cpu ]] && command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  "${gpu[@]}" build sentinel
  if "${gpu[@]}" run --rm --no-deps sentinel python -c 'from src.hub.service import DeviceManager; d=DeviceManager("cuda"); d.initialize(); assert d.device.startswith("cuda"), d.status(); print(d.status())'; then
    if "${gpu[@]}" up -d --no-build --wait --wait-timeout 120; then
      echo 'Camera hub started with GPU access.'
      exit 0
    fi
  fi
  if [[ "$mode" == gpu ]]; then echo 'GPU deployment failed.' >&2; exit 1; fi
  echo 'GPU container could not start. Starting CPU deployment.' >&2
elif [[ "$mode" == gpu ]]; then
  echo 'GPU driver is unavailable.' >&2; exit 1
fi
# Override cuda from the env file when CPU was selected or GPU startup failed.
SENTINEL_DEVICE=cpu "${base[@]}" up -d --build --wait --wait-timeout 120
