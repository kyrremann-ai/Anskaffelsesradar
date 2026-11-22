"""Engangsverifisering av tilleggskildene mot live-tjenestene (der tilgang finnes).

    python -m radar.verify_sources            # skriver rapport, avslutter med feil hvis en kilde feiler
    python -m radar.verify_sources --json ut.json

Kjøres fra Actions → «Kildesjekk» (GitHub-runnerne har nett; utviklingsmiljøet har det ikke alltid).
Henter et lite utvalg fra hver kilde, kjører den vanlige parseren og skjemavalideringen (ingest.validate),
og viser antall rader, advarsler og et eksempel på feltene, så en formatendring oppdages før den gir falske null-treff.
OpenSanctions sjekkes bare når OPENSANCTIONS_LISENS er satt (se README), Proff og bemanning bare med nøkkel/URL.
"""
from __future__ import annotations

import argparse
import json
import os
import traceback

import httpx

from .config import settings
from .ingest import registers, regnskap, sanctions, owners, validate

# Store selskaper som alltid har regnskap i Regnskapsregisteret (Equinor, DNB Bank, Norsk Hydro, Telenor)
SAMPLE_ORGNR = ["923609016", "984851006", "914778271", "982463718"]


def _regnskap() -> dict:
    rows, years, failed = [], [], []
    with httpx.Client(headers={"Accept": "application/json", "User-Agent": settings.brreg_user_agent}) as c:
        for o in SAMPLE_ORGNR:
            res = regnskap.fetch_one(c, o)  # samme kall som produksjonen, med nye forsøk ved 429/5xx
            if res is None:
                failed.append(o)
                continue
            rows.append(res[0])
            years += res[1]
    if failed:
        validate.warn(f"Regnskapsregisteret: ingen gyldig respons for {', '.join(failed)} etter nye forsøk (5xx/429)")
    if not rows:
        raise RuntimeError("Regnskapsregisteret svarte ikke for noen av utvalgsselskapene")
    ok = validate.check("Regnskapsregisteret", rows, validate.RegnskapRow, core=("aar", "driftsinntekter", "egenkapital"))
    if any(r.get("driftsinntekter") is None for r in ok):
        validate.warn("Regnskapsregisteret: driftsinntekter mangler for et storselskap. Sjekk feltstien i regnskap._row.")
    validate.check("Regnskapsregisteret (alle år)", years, validate.RegnskapAarRow)
    return {"rows": len(ok), "example": ok[0] if ok else None}


def _register(name: str, url: str) -> dict:
    r = httpx.get(url, timeout=180, follow_redirects=True)
    r.raise_for_status()
    rows = validate.check(f"Register {name}", registers.parse(r.content), validate.RegisterRow,
                          min_rows=registers.MIN_ROWS.get(name, 0))
    statuses = sorted({x["status"] for x in rows})[:10]
    return {"rows": len(rows), "statuses": statuses, "example": rows[0] if rows else None}


def _sanctions() -> dict:
    # Bare de første ~20 000 linjene: nok til å se format og norske treff uten å laste hele filen
    headers = {"Authorization": f"ApiKey {settings.opensanctions_api_key}"} if settings.opensanctions_api_key else {}
    lines = []
    with httpx.stream("GET", settings.opensanctions_url or sanctions.URL, headers=headers, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        for i, line in enumerate(r.iter_lines()):
            lines.append(line)
            if i > 20000:
                break
    missing = {"id", "name", "countries", "identifiers", "sanctions"} - set(lines[0].split(","))
    if missing:
        validate.warn(f"OpenSanctions: kolonner mangler ({', '.join(sorted(missing))})")
    rows = validate.check("OpenSanctions", sanctions.parse_lines(lines), validate.SanctionRow)
    return {"rows_in_sample": len(rows), "example": rows[0] if rows else None}


def _proff() -> dict:
    with httpx.Client(headers={"Authorization": f"{settings.proff_auth_scheme} {settings.proff_api_key}",
                               "Accept": "application/json"}) as c:
        r = c.get(f"{settings.proff_base_url.rstrip('/')}/companies/register/NO/{SAMPLE_ORGNR[0]}", timeout=30)
        r.raise_for_status()
        body = r.json()
    rows = validate.check("Proff API", [{"orgnr": SAMPLE_ORGNR[0], **x} for x in owners.proff_owners(body)], validate.OwnerRow)
    if not rows:
        validate.warn(f"Proff: ingen eiere funnet. Toppnøkler i svaret: {sorted(body)[:15]}")
    return {"rows": len(rows), "top_keys": sorted(body)[:15]}


def run() -> dict[str, dict]:
    checks = {"Regnskapsregisteret": _regnskap}
    if settings.renhold_register_url:
        checks["Renholdsregisteret"] = lambda: _register("renhold", settings.renhold_register_url)
    if settings.bemanning_register_url:
        checks["Bemanningsregisteret"] = lambda: _register("bemanning", settings.bemanning_register_url)
    if settings.opensanctions_license:
        checks["OpenSanctions"] = _sanctions
    if settings.proff_api_key:
        checks["Proff API"] = _proff
    out = {}
    for name, fn in checks.items():
        validate.current = []
        try:
            res = fn()
            out[name] = {"status": "advarsel" if validate.current else "ok", **res, "warnings": validate.current}
        except Exception as e:  # noqa: BLE001
            out[name] = {"status": "feil", "error": f"{type(e).__name__}: {e}"[:400], "warnings": validate.current}
            traceback.print_exc()
        validate.current = None
    return out


def report(res: dict[str, dict]) -> str:
    lines = ["## Kildesjekk mot live-tjenestene", "", "| Kilde | Status | Rader | Merknad |", "|---|---|---|---|"]
    for k, v in res.items():
        n = v.get("rows", v.get("rows_in_sample", ""))
        note = v.get("error") or "; ".join(v.get("warnings") or []) or ""
        lines.append(f"| {k} | {v['status']} | {n} | {note} |")
    for k, v in res.items():
        if v.get("example"):
            lines += ["", f"<details><summary>{k}: eksempelrad</summary>", "", "```json",
                      json.dumps(v["example"], ensure_ascii=False, indent=1, default=str)[:1500], "```", "</details>"]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str)
    a = ap.parse_args()
    res = run()
    text = report(res)
    print(text)
    if p := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(p, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    if any(v["status"] == "feil" for v in res.values()):
        raise SystemExit(1)
