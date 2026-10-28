"""Regnskapsregisteret (Brønnøysund): siste innsendte årsregnskap per orgnr.

GET https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}   (åpent, uten nøkkel)
Svaret er en liste med regnskap (flere år, og både SELSKAP og KONSERN for morselskaper).
Feltene leses defensivt. `regnskap` holder siste selskapsregnskap, `regnskap_aar` alle år og typer,
slik at kontraktsverdien kan sammenlignes med omsetningen i tildelingsåret.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..db import now, tx

URL = "https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}"


def _get(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _items(body) -> list[dict]:
    items = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    return [i for i in items if isinstance(i, dict)]


def _type(r: dict) -> str:
    return str(r.get("regnskapstype") or "SELSKAP").upper()


def parse(orgnr: str, body) -> dict | None:
    """Siste selskapsregnskap (konsernregnskap bare hvis det er alt som finnes)."""
    items = _items(body)
    if not items:
        return None
    r = max(items, key=lambda i: (str(_get(i, "regnskapsperiode", "tilDato") or ""), _type(i) == "SELSKAP"))
    return _row(orgnr, r)


def parse_years(orgnr: str, body) -> list[dict]:
    """Alle år og typer: [{orgnr, aar, type, driftsinntekter, egenkapital}]."""
    out = {}
    for r in _items(body):
        row = _row(orgnr, r)
        if row["aar"]:
            out[(row["aar"], _type(r))] = {"orgnr": orgnr, "aar": row["aar"], "type": _type(r),
                                          "driftsinntekter": row["driftsinntekter"], "egenkapital": row["egenkapital"]}
    return sorted(out.values(), key=lambda x: (x["aar"], x["type"]))


def _row(orgnr: str, r: dict) -> dict:
    res = r.get("resultatregnskapResultat") or {}
    ek = r.get("egenkapitalGjeld") or {}
    til = str(_get(r, "regnskapsperiode", "tilDato") or "")
    return {
        "orgnr": orgnr, "aar": int(til[:4]) if til[:4].isdigit() else None,
        "driftsinntekter": _get(res, "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
        "driftsresultat": _get(res, "driftsresultat", "driftsresultat"),
        "aarsresultat": res.get("aarsresultat"),
        "egenkapital": _get(ek, "egenkapital", "sumEgenkapital"),
        "gjeld": _get(ek, "gjeldOversikt", "sumGjeld"),
        "eiendeler": _get(r, "eiendeler", "sumEiendeler"),
    }


def store(con, rows: list[dict], years: list[dict] | None = None) -> None:
    cols = ["orgnr", "aar", "driftsinntekter", "driftsresultat", "aarsresultat", "egenkapital", "gjeld", "eiendeler"]
    with tx(con):
        con.executemany(f"INSERT OR REPLACE INTO regnskap({','.join(cols)}, fetched_at) VALUES ({','.join('?' * len(cols))}, ?)",
                        [[r.get(c) for c in cols] + [now()] for r in rows])
        con.executemany("INSERT OR REPLACE INTO regnskap_aar(orgnr, aar, type, driftsinntekter, egenkapital) VALUES (?,?,?,?,?)",
                        [(y["orgnr"], y["aar"], y["type"], y["driftsinntekter"], y["egenkapital"]) for y in years or []])


def refresh(con, user_agent: str, log=print, workers: int = 4) -> int:
    wanted = set()
    for r in con.execute("SELECT winner_ids FROM contracts WHERE source != 'demo'"):
        wanted.update(o for o in json.loads(r["winner_ids"] or "[]") if o and o.isdigit() and len(o) == 9)
    # Enheter hentet før regnskap_aar fantes hentes på nytt, så alle år blir lagret
    have = {r["orgnr"] for r in con.execute("""SELECT orgnr FROM regnskap WHERE fetched_at >= date('now', '-90 day')
                                               AND (driftsinntekter IS NULL OR orgnr IN (SELECT orgnr FROM regnskap_aar))""")}
    todo = sorted(wanted - have)
    log(f"Regnskap: {len(todo)} orgnr å hente")

    def one(client, o):
        try:
            r = client.get(URL.format(orgnr=o), timeout=20)
            if r.status_code != 200:
                return {"orgnr": o}, []
            body = r.json()
            return parse(o, body) or {"orgnr": o}, parse_years(o, body)
        except (httpx.HTTPError, ValueError):
            return None

    with httpx.Client(headers={"Accept": "application/json", "User-Agent": user_agent}) as c, ThreadPoolExecutor(workers) as ex:
        res = [r for r in ex.map(lambda o: one(c, o), todo) if r]
    store(con, [r for r, _ in res], [y for _, ys in res for y in ys])
    return len(res)
