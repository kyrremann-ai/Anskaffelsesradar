"""Eiere og aksjonærer.

1. Aksjonærregisteret (Skatteetaten): årlig CSV-fil, utlevert på forespørsel og publisert
   for flere årganger (f.eks. via aksjeeiere.no). Importeres fra fil:
       python -m radar.ingest.owners aksjonaerregisteret2024.csv --year 2024
   Kolonner (semikolon): Orgnr; Selskap; Aksjeklasse; Navn aksjonær; Fødselsår/orgnr;
   Postnr/sted; Landkode; Antall aksjer; Antall aksjer selskap. Overskriftene matches løst.
   Filen er stor (millioner rader), så vi beholder bare selskaper som finnes i kontraktene.

2. Proff API (valgfritt, betalt): krever avtale med Proff. Vi skraper ikke proff.no,
   fordi det bryter vilkårene og databasevernet (åndsverkloven § 24).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from pathlib import Path

import httpx

from ..db import connect, now, tx

# ------------------------------------------------------------ aksjonærregisteret

_COLS = {
    "orgnr": ["orgnr", "organisasjonsnummer"],
    "selskap": ["selskap"],
    "navn": ["navn aksjonær", "navn aksjonaer", "aksjonær", "aksjonaer"],
    "id": ["fødselsår/orgnr", "fodselsar/orgnr", "fødselsår", "orgnr aksjonær"],
    "antall": ["antall aksjer"],
    "total": ["antall aksjer selskap"],
}


def _map_header(header: list[str]) -> dict[str, int]:
    h = [c.strip().lower() for c in header]
    out = {}
    for key, cands in _COLS.items():
        # mest spesifikke treff først (f.eks. «antall aksjer selskap» før «antall aksjer»)
        for cand in sorted(cands, key=len, reverse=True):
            idx = [i for i, c in enumerate(h) if c == cand] or [i for i, c in enumerate(h) if c.startswith(cand)]
            idx = [i for i in idx if i not in out.values()]
            if idx:
                out[key] = idx[0]
                break
    return out


def _num(s) -> float | None:
    try:
        return float(str(s).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def import_shareholders(con, path: Path, year: int | None, wanted: set[str] | None = None, log=print) -> int:
    """Leser aksjonærregister-CSV. Summerer aksjeklasser per eier."""
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace") if b"\xc3" in raw[:20000] else raw.decode("latin-1")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    cols = _map_header(next(reader))
    missing = {"orgnr", "navn", "id", "antall"} - cols.keys()
    if missing:
        raise ValueError(f"Fant ikke kolonnene {sorted(missing)} i {path.name}")
    agg: dict[tuple, dict] = {}
    for row in reader:
        if len(row) <= max(cols.values()):
            continue
        org = re.sub(r"\D", "", row[cols["orgnr"]])
        if wanted is not None and org not in wanted:
            continue
        ident = re.sub(r"\D", "", row[cols["id"]])
        key = (org, row[cols["navn"]].strip(), ident)
        a = agg.setdefault(key, {"antall": 0.0, "total": _num(row[cols["total"]]) if "total" in cols else None})
        a["antall"] += _num(row[cols["antall"]]) or 0
    rows = []
    for (org, navn, ident), a in agg.items():
        rows.append((org, navn, ident if len(ident) == 9 else None, int(ident) if len(ident) == 4 else None,
                     round(100 * a["antall"] / a["total"], 2) if a["total"] else None, "aksjonaerregisteret", year, now()))
    with tx(con):
        con.execute("DELETE FROM owners WHERE source='aksjonaerregisteret'")
        con.executemany("INSERT INTO owners(orgnr, owner_name, owner_orgnr, owner_birth_year, share_pct, source, year, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", rows)
    log(f"Aksjonærregisteret: {len(rows)} eierposter for {len({r[0] for r in rows})} selskaper")
    return len(rows)


# ------------------------------------------------------------ Proff API (valgfritt)

def proff_owners(body: dict) -> list[dict]:
    """Leser eiere fra et Proff-svar. Feltnavn varierer mellom API-versjoner, så vi prøver flere."""
    out = []
    for key in ("shareholders", "owners", "shareholdersInfo", "ownership"):
        items = body.get(key)
        if isinstance(items, dict):
            items = items.get("shareholders") or items.get("items")
        for it in items or []:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("shareholderName") or it.get("ownerName")
            org = re.sub(r"\D", "", str(it.get("organisationNumber") or it.get("orgnr") or it.get("companyId") or ""))
            share = it.get("share") or it.get("sharePercentage") or it.get("percentage") or it.get("ownershipPercentage")
            if name:
                out.append({"owner_name": name, "owner_orgnr": org if len(org) == 9 else None,
                            "owner_birth_year": it.get("birthYear"), "share_pct": _num(share) if share is not None else None})
        if out:
            break
    return out


def refresh_proff(con, api_key: str, base_url: str, auth_scheme: str = "Token", log=print) -> int:
    if not api_key:
        return 0
    wanted = set()
    for r in con.execute("SELECT winner_ids FROM contracts WHERE source != 'demo'"):
        wanted.update(o for o in json.loads(r["winner_ids"] or "[]") if o and o.isdigit())
    done = {r["orgnr"] for r in con.execute("SELECT DISTINCT orgnr FROM owners WHERE source='proff' AND fetched_at >= date('now','-90 day')")}
    rows = []
    with httpx.Client(headers={"Authorization": f"{auth_scheme} {api_key}", "Accept": "application/json"}) as c:
        for o in sorted(wanted - done):
            r = c.get(f"{base_url.rstrip('/')}/companies/register/NO/{o}", timeout=30)
            if r.status_code == 401:
                raise RuntimeError("Proff avviste nøkkelen (401)")
            if r.status_code == 200:
                rows += [{"orgnr": o, **x} for x in proff_owners(r.json())]
    with tx(con):
        con.executemany("INSERT INTO owners(orgnr, owner_name, owner_orgnr, owner_birth_year, share_pct, source, year, fetched_at) "
                        "VALUES (?,?,?,?,?,'proff',NULL,?)",
                        [(r["orgnr"], r["owner_name"], r["owner_orgnr"], r["owner_birth_year"], r["share_pct"], now()) for r in rows])
    log(f"Proff: {len(rows)} eierposter")
    return len(rows)


if __name__ == "__main__":
    from ..config import settings
    ap = argparse.ArgumentParser(description="Importer aksjonærregisteret fra Skatteetaten")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--year", type=int)
    ap.add_argument("--all", action="store_true", help="ta med alle selskaper, ikke bare leverandører i kontraktene")
    a = ap.parse_args()
    con = connect(settings.db_path)
    wanted = None
    if not a.all:
        wanted = set()
        for r in con.execute("SELECT winner_ids FROM contracts"):
            wanted.update(json.loads(r["winner_ids"] or "[]"))
    import_shareholders(con, a.csv, a.year, wanted)
