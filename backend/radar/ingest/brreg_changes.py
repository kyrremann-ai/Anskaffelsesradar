"""Brregs endringslogg: når endret adresse, antall ansatte og roller seg (data.brreg.no, åpent).

- GET /enhetsregisteret/api/oppdateringer/enheter?dato=..&organisasjonsnummer=..&includeChanges=true
  JSON Patch per endring. Eldre endringer (før ca. 2026) kommer uten `endringer`, og lagres som felt «ukjent».
- GET /enhetsregisteret/api/oppdateringer/roller?afterTime=..&organisasjonsnummer=..
  Bare tidspunkt for rolleendring, ikke hva som endret seg.

Brukes til å avgjøre om en adresse eller rolle var den samme på tildelingstidspunktet som i dag.
Hentes bare for leverandører som har et felles-adresse-, felles-rolle- eller ingen-ansatte-flagg.

Fusjon, fisjon og omdanning (samme virksomhet under nytt orgnr) finnes ikke i det åpne API-et.
De leses fra fil: data/import/endringer*.csv med kolonnene fra_orgnr;til_orgnr;type;dato.
"""
from __future__ import annotations

import csv
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from ..db import now, tx
from .brreg import BASE, _get

ADDRESS_PATHS = ("/forretningsadresse", "/beliggenhetsadresse")


def classify(item: dict) -> list[tuple[str, str | None]]:
    """Én oppdatering -> [(felt, ny verdi)]. felt: adresse | ansatte | slettet | ny | annet | ukjent."""
    kind = item.get("endringstype")
    if kind in ("Sletting", "Fjernet"):
        return [("slettet", None)]
    if kind == "Ny":
        return [("ny", None)]
    ops = item.get("endringer")
    if ops is None:
        return [("ukjent", None)]
    out = []
    for op in ops:
        path = str(op.get("path") or "")
        if path.startswith(ADDRESS_PATHS):
            out.append(("adresse", json.dumps(op.get("value"), ensure_ascii=False)))
        elif path == "/antallAnsatte":
            v = op.get("value")
            out.append(("ansatte", None if op.get("op") == "remove" or v is None else str(v)))
    return out or [("annet", None)]


def fetch(client: httpx.Client, orgnr: str, since: str, max_pages: int = 20) -> list[dict] | None:
    """Endringer for én enhet siden `since` (ISO-dato). None ved feil."""
    rows = []
    for page in range(max_pages):
        st, body = _get(client, f"{BASE}/oppdateringer/enheter?dato={since}T00:00:00.000Z&organisasjonsnummer={orgnr}"
                                f"&includeChanges=true&size=100&page={page}")
        if st != 200:
            return None
        for it in (body or {}).get("_embedded", {}).get("oppdaterteEnheter", []):
            rows += [{"orgnr": orgnr, "at": str(it.get("dato"))[:10], "felt": f, "verdi": v} for f, v in classify(it)]
        pg = (body or {}).get("page") or {}
        if page + 1 >= (pg.get("totalPages") or 0):
            break
    st, body = _get(client, f"{BASE}/oppdateringer/roller?afterTime={since}T00:00:00.000Z&organisasjonsnummer={orgnr}&size=1000")
    if st != 200:
        return None
    rows += [{"orgnr": orgnr, "at": str(ev.get("time"))[:10], "felt": "roller", "verdi": None}
             for ev in body or [] if isinstance(ev, dict)]
    return rows


def store(con, orgnr: str, since: str, rows: list[dict]) -> None:
    with tx(con):
        con.execute("DELETE FROM brreg_changes WHERE orgnr=?", (orgnr,))
        con.executemany("INSERT INTO brreg_changes(orgnr, at, felt, verdi) VALUES (?,?,?,?)",
                        [(r["orgnr"], r["at"], r["felt"], r["verdi"]) for r in rows])
        con.execute("INSERT OR REPLACE INTO brreg_changes_fetched(orgnr, since, fetched_at) VALUES (?,?,?)", (orgnr, since, now()))


def refresh(con, orgnrs: set[str], since: str, user_agent: str, max_age_days: int = 7, workers: int = 4, log=print) -> int:
    """Henter endringsloggen for `orgnrs` som ikke er hentet fra `since`, eller er eldre enn `max_age_days`."""
    fresh = {r["orgnr"] for r in con.execute(
        "SELECT orgnr FROM brreg_changes_fetched WHERE since <= ? AND fetched_at >= datetime('now', ?)", (since, f"-{max_age_days} day"))}
    todo = sorted(o for o in orgnrs - fresh if re.fullmatch(r"\d{9}", o or ""))
    log(f"Brreg endringslogg: {len(todo)} enheter å hente")
    done = 0
    with httpx.Client(headers={"Accept": "application/json", "User-Agent": user_agent}) as client, ThreadPoolExecutor(workers) as ex:
        for o, rows in zip(todo, ex.map(lambda o: fetch(client, o, since), todo)):
            if rows is not None:
                store(con, o, since, rows)
                done += 1
    return done


def import_successors(con, path: Path) -> int:
    """fra_orgnr;til_orgnr;type;dato (type: fusjon | fisjon | omdanning | annet). Skilletegn ; eller ,."""
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";" if ";" in text.split("\n", 1)[0] else ","))
    vals = []
    for r in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        fra, til = re.sub(r"\D", "", r.get("fra_orgnr", "")), re.sub(r"\D", "", r.get("til_orgnr", ""))
        if len(fra) == 9 and len(til) == 9 and fra != til:
            vals.append((fra, til, r.get("type") or "annet", r.get("dato") or None, path.name))
    with tx(con):
        con.executemany("INSERT OR REPLACE INTO brreg_successors(fra_orgnr, til_orgnr, type, dato, kilde) VALUES (?,?,?,?,?)", vals)
    return len(vals)
