#!/usr/bin/env bash
# Bygger Cloudflare Pages-versjonen (radar.vikingsea.no): samme klient og JSON-filer som artifacten,
# men saker lagres i D1 via Pages Functions, og innlogging går gjennom Cloudflare Access.
#   RADAR_MODE=live deploy/build_pages.sh ut-pages/      # eller RADAR_DEMO=1 for demodata
#   python deploy/cf_radar.py deploy ut-pages/
set -euo pipefail
OUT=${1:-pages-out}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$OUT"
mkdir -p "$OUT/public"
OUT=$(cd "$OUT" && pwd)
cd "$ROOT/backend"
python -m radar.pipeline ${RADAR_DEMO:+--demo}
python -m radar.export_static "$OUT/public"
cd "$ROOT/frontend"
VITE_STATIC=1 npx vite build --outDir "$OUT/.dist" --emptyOutDir
cp -r "$OUT/.dist/." "$OUT/public/"
rm -rf "$OUT/.dist"
cp -r "$ROOT/deploy/cloudflare/public/." "$OUT/public/"
cp -r "$ROOT/deploy/cloudflare/functions" "$OUT/functions"
# shim.js må lastes før klienten (moduler kjøres etter vanlige skript)
sed -i 's#</head>#  <script src="/shim.js"></script>\n  </head>#' "$OUT/public/index.html"
du -sh "$OUT/public"/*.json
