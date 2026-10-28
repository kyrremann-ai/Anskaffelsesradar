#!/usr/bin/env bash
# Bygger artifact-versjonen: henter data, scorer, eksporterer JSON og bygger klienten.
# Resultatet i $OUT (index.html + meta.json, contracts-N.json, cards-N.json) publiseres som artifact.
#   RADAR_MODE=live deploy/build_artifact.sh ut/
set -euo pipefail
OUT=${1:-artifact-out}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "$ROOT/backend"
python -m radar.pipeline ${RADAR_DEMO:+--demo}
python -m radar.export_static "$OUT"
cd "$ROOT/frontend"
VITE_STATIC=1 npx vite build --outDir "$OUT/.dist" --emptyOutDir
python "$ROOT/deploy/artifact_page.py" "$OUT/.dist" "$OUT/index.html"
rm -rf "$OUT/.dist"
