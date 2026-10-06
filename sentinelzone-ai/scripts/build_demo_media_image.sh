#!/usr/bin/env bash
# Build the media-complete runtime image from the restored original assets.
#
# The normal image build ignores data/ on purpose (the runtime image stays slim
# and receives state through a volume). This helper streams an explicit, verified
# tar context so the original videos and datasets are baked into a separate image
# tag, leaving the base image untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_IMAGE="${BASE_IMAGE:-sentinelzone-ai:sec01-candidate}"
MEDIA_IMAGE="${MEDIA_IMAGE:-sentinelzone-ai:demo-media}"
SKIP_VERIFY="${SKIP_VERIFY:-0}"

if [ "$SKIP_VERIFY" != "1" ]; then
  python scripts/verify_original_assets.py
fi

for required in data/real_videos data/test_videos data/roboflow_downloaded data/construction_safety; do
  if [ ! -d "$required" ]; then
    echo "missing required asset directory: $required" >&2
    exit 1
  fi
done

DETECTOR_WEIGHTS="${DETECTOR_WEIGHTS:-yolov8n.pt}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Building $MEDIA_IMAGE from $BASE_IMAGE with the original media and datasets..."
# Stage a minimal context: the verified data tree plus the detector weights used
# for the calibrated overlay, with the media Dockerfile renamed to "Dockerfile"
# (BuildKit resolves -f relative to the context).
mkdir -p "$STAGE/models"
cp Dockerfile.demo-media "$STAGE/Dockerfile"
cp -a data "$STAGE/data"
if [ -f "$DETECTOR_WEIGHTS" ]; then
  cp "$DETECTOR_WEIGHTS" "$STAGE/models/detector.pt"
  echo "  detector weights: $DETECTOR_WEIGHTS"
else
  echo "  warning: $DETECTOR_WEIGHTS not found; the media overlay will report no weights" >&2
fi

tar -cf - -C "$STAGE" Dockerfile data models \
  | docker build \
      --build-arg "BASE_IMAGE=$BASE_IMAGE" \
      -t "$MEDIA_IMAGE" \
      -

echo "Built $MEDIA_IMAGE"
