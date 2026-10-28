"""Setter sammen artifact-siden fra et Vite-bygg (VITE_STATIC=1).

    python deploy/artifact_page.py frontend/dist-static ut/index.html

JS og CSS legges inline (artifact-sider kan bare laste skript fra godkjente CDN-er).
Datafilene (meta.json, contracts-N.json, cards-N.json) publiseres ved siden av siden.
"""
import sys
from pathlib import Path

dist, out = Path(sys.argv[1]), Path(sys.argv[2])
js = next((dist / "assets").glob("*.js")).read_text(encoding="utf-8").replace("</script", "<\\/script")
css = next((dist / "assets").glob("*.css")).read_text(encoding="utf-8")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(f"""<title>Anskaffelsesradar</title>
<style>{css}</style>
<div id="root"></div>
<script type="module">{js}</script>
""", encoding="utf-8")
print(f"Skrev {out} ({out.stat().st_size / 1e6:.1f} MB)")
