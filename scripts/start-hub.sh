#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-auto}"
case "$mode" in auto|cpu|gpu) ;; *) echo 'Usage: start-hub.sh [auto|cpu|gpu]' >&2; exit 2;; esac
base=(docker compose -f compose.yaml)
gpu=(docker compose -f compose.yaml -f compose.gpu.yaml)
# Build failures are reported; hardware fallback must not conceal a broken image.
if [[ "$mode" != cpu ]] && command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  "${gpu[@]}" build
  if "${gpu[@]}" run --rm --no-deps hub python -c 'import torch; assert torch.cuda.is_available(); print(torch.ones(1, device="cuda").item())'; then
    if "${gpu[@]}" up -d --no-build; then
      echo 'Hub started with GPU access; application can fall back to CPU.'
      exit 0
    fi
  fi
  if [[ "$mode" == gpu ]]; then echo 'GPU deployment failed.' >&2; exit 1; fi
  echo 'GPU container could not start. Starting CPU deployment.' >&2
elif [[ "$mode" == gpu ]]; then
  echo 'GPU driver is unavailable.' >&2; exit 1
fi
"${base[@]}" up -d --build
