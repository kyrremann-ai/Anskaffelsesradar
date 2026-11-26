#!/usr/bin/env bash
# Bygger den offentlige demoen (vikingsea.no/radar/) med fiktive demodata: syntetiske kontrakter, selskaper og
# personer fra radar.synthetic. Ingen innlogging, ingen saker i D1, ingen AI-vurderinger, ingen ekte data.
#
#   deploy/build_offentlig.sh ut-offentlig/
#
# Kopier ut-offentlig/ til radar/ i vikingsea.no-mappen og publiser den på Netlify.
set -euo pipefail
OUT=${1:-ut-offentlig}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$OUT"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
DATA=$(mktemp -d)  # egen database, så live-dataene i backend/data aldri blandes inn
trap 'rm -rf "$DATA"' EXIT
cd "$ROOT/backend"
RADAR_MODE=demo RADAR_DATA_DIR="$DATA" python -m radar.pipeline --demo
RADAR_MODE=demo RADAR_DATA_DIR="$DATA" python -m radar.export_static "$OUT"
cd "$ROOT/frontend"
# Relativ base: siden ligger under /radar/. Klienten bruker HashRouter og relative datafiler.
VITE_STATIC=1 npx vite build --base ./ --outDir "$OUT/.dist" --emptyOutDir
cp -r "$OUT/.dist/." "$OUT/"
rm -rf "$OUT/.dist"
BANNER='<div role="note" style="background:#0f2a33;color:#fff;font:500 14px/1.5 system-ui,sans-serif;padding:10px 16px;text-align:center">Offentlig demo med fiktive data: kontrakter, selskaper og personer er laget for demonstrasjon. Saker lagres bare i din nettleser. <a href="mailto:contact@vikingsea.no?subject=Anskaffelsesradar" style="color:#9fe3d6">Be om tilgang til den ekte radaren</a></div>'
python - "$OUT/index.html" "$BANNER" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text(encoding="utf-8")
assert t.count("<body>") == 1, "fant ikke <body>"
p.write_text(t.replace("<body>", "<body>" + sys.argv[2], 1), encoding="utf-8")
PY
# Sjekk at det er demodata som publiseres, ikke live
python - "$OUT/meta.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))["meta"]
assert m.get("mode") == "demo", f"meta.mode er {m.get('mode')!r}, ikke demo"
print("Demodata OK")
PY
du -sh "$OUT"/*.json | tail -3
