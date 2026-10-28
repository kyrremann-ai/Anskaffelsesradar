"""Regnskapsregisteret (Brønnøysund): siste innsendte årsregnskap per orgnr.

GET https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}   (åpent, uten nøkkel)
Svaret er en liste med regnskap. Feltene leses defensivt.
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


def parse(orgnr: str, body) -> dict | None:
    items = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    items = [i for i in items if isinstance(i, dict)]
    if not items:
        return None
    r = max(items, key=lambda i: str(_get(i, "regnskapsperiode", "tilDato") or ""))
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


def store(con, rows: list[dict]) -> None:
    cols = ["orgnr", "aar", "driftsinntekter", "driftsresultat", "aarsresultat", "egenkapital", "gjeld", "eiendeler"]
    with tx(con):
        con.executemany(f"INSERT OR REPLACE INTO regnskap({','.join(cols)}, fetched_at) VALUES ({','.join('?' * len(cols))}, ?)",
                        [[r.get(c) for c in cols] + [now()] for r in rows])


def refresh(con, user_agent: str, log=print, workers: int = 4) -> int:
    wanted = set()
    for r in con.execute("SELECT winner_ids FROM contracts WHERE source != 'demo'"):
        wanted.update(o for o in json.loads(r["winner_ids"] or "[]") if o and o.isdigit() and len(o) == 9)
    have = {r["orgnr"] for r in con.execute("SELECT orgnr FROM regnskap WHERE fetched_at >= date('now', '-90 day')")}
    todo = sorted(wanted - have)
    log(f"Regnskap: {len(todo)} orgnr å hente")

    def one(client, o):
        try:
            r = client.get(URL.format(orgnr=o), timeout=20)
            return parse(o, r.json()) if r.status_code == 200 else {"orgnr": o}
        except (httpx.HTTPError, ValueError):
            return None

    rows = []
    with httpx.Client(headers={"Accept": "application/json", "User-Agent": user_agent}) as c, ThreadPoolExecutor(workers) as ex:
        rows = [r for r in ex.map(lambda o: one(c, o), todo) if r]
    store(con, rows)
    return len(rows)
