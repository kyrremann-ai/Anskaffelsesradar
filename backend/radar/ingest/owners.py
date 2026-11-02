"""Eiere og aksjonærer.

1. Aksjonærregisteret (Skatteetaten): årlig CSV-fil, utlevert på forespørsel og publisert
   for flere årganger (f.eks. via aksjeeiere.no). Importeres fra fil:
       python -m radar.ingest.owners aksjonaerregisteret2024.csv --year 2024
   Kolonner (semikolon): Orgnr; Selskap; Aksjeklasse; Navn aksjonær; Fødselsår/orgnr;
   Postnr/sted; Landkode; Antall aksjer; Antall aksjer selskap. Overskriftene matches løst.
   Filen er stor (~3 mill. rader, 300 MB). Lag først et kompakt uttrekk (eierposter ≥ 5 %, gzip):
       python -m radar.ingest.owners aksjeeiebok_2025.csv --extract aksjonaer_2025.csv.gz
   Ved import beholdes bare leverandører og oppdragsgivere i kontraktene, og selskapene som eier dem
   (inntil tre ledd), så indirekte eierskap via holdingselskap kan følges. Flere årganger kan ligge side om side.
   Filen inneholder personopplysninger (navn, fødselsår, poststed) og skal ikke ligge ukryptert i repoet.

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


def _open_text(path: Path) -> io.TextIOBase:
    """Åpner CSV (også .gz) med riktig tegnsett. Skatteetaten leverer UTF-8 med BOM, eldre filer latin-1."""
    import gzip
    raw = gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")
    head = raw.peek(20000)[:20000] if hasattr(raw, "peek") else b""
    try:
        head[:-4].decode("utf-8")
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        enc = "latin-1"
    return io.TextIOWrapper(raw, encoding=enc, errors="replace", newline="")


def read_register(path: Path) -> dict[tuple, dict]:
    """Leser aksjonærregister-CSV (Skatteetatens fulle fil eller vårt kompakte uttrekk).
    Summerer aksjeklasser per eier. Gir {(orgnr, navn, id): {antall, total, pct, sted, land}}."""
    with _open_text(path) as f:
        # QUOTE_NONE: navn kan inneholde enkle anførselstegn
        reader = csv.reader(f, delimiter=";", quoting=csv.QUOTE_NONE)
        header = next(reader)
        if [h.strip().lower() for h in header][:3] == ["orgnr", "navn", "id"]:
            return _read_compact(reader)
        cols = _map_header(header)
        missing = {"orgnr", "navn", "id", "antall"} - cols.keys()
        if missing:
            raise ValueError(f"Fant ikke kolonnene {sorted(missing)} i {path.name}")
        h = [c.strip().lower() for c in header]
        sted = next((i for i, c in enumerate(h) if c.startswith("postnr")), None)
        land = next((i for i, c in enumerate(h) if c.startswith("landkode")), None)
        agg: dict[tuple, dict] = {}
        width = max(cols.values())
        for row in reader:
            if len(row) <= width:
                continue
            org = re.sub(r"\D", "", row[cols["orgnr"]])
            ident = re.sub(r"\D", "", row[cols["id"]])
            key = (org, row[cols["navn"]].strip(), ident)
            a = agg.get(key)
            if a is None:
                a = agg[key] = {"antall": 0.0, "total": _num(row[cols["total"]]) if "total" in cols else None,
                                "sted": row[sted].strip() if sted is not None and sted < len(row) else "",
                                "land": row[land].strip() if land is not None and land < len(row) else ""}
            a["antall"] += _num(row[cols["antall"]]) or 0
    for a in agg.values():
        a["pct"] = round(100 * a["antall"] / a["total"], 2) if a["total"] else None
    return agg


def _read_compact(reader) -> dict[tuple, dict]:
    out = {}
    for row in reader:
        if len(row) < 4:
            continue
        org, navn, ident, pct = row[:4]
        out[(org, navn, ident)] = {"pct": _num(pct) if pct else None, "sted": row[4] if len(row) > 4 else "",
                                   "land": row[5] if len(row) > 5 else ""}
    return out


def _clean(v: str) -> str:
    return " ".join(str(v).replace(";", ",").split())


def extract(src: Path, dst: Path, min_pct: float = 5.0, log=print) -> int:
    """Lager et kompakt uttrekk av Skatteetatens fil: eierposter med minst `min_pct` % (summert over aksjeklasser).
    Kolonner: orgnr;navn;id;pct;sted;land. Fullt register er ~3 mill. rader og 300 MB; uttrekket ~600 000 rader.
    Små eierposter (typisk i børsnoterte selskaper) brukes ikke i flaggene, så de tas ikke med (dataminimering)."""
    import gzip
    agg = read_register(src)
    n = 0
    with gzip.open(dst, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_NONE, quotechar=None)
        w.writerow(["orgnr", "navn", "id", "pct", "sted", "land"])
        for (org, navn, ident), a in sorted(agg.items()):
            if a["pct"] is not None and a["pct"] < min_pct:
                continue
            w.writerow([org, _clean(navn), ident, a["pct"] if a["pct"] is not None else "", _clean(a["sted"]), _clean(a["land"])])
            n += 1
    log(f"Uttrekk: {n} av {len(agg)} eierposter (≥ {min_pct:g} %) -> {dst}")
    return n


def _closure(agg: dict[tuple, dict], wanted: set[str] | None, depth: int) -> dict[tuple, dict]:
    """Eierposter for de ønskede selskapene, og for selskapene som eier dem, opp til `depth` ledd."""
    if wanted is None:
        return agg
    by_org: dict[str, list[tuple]] = {}
    for k in agg:
        by_org.setdefault(k[0], []).append(k)
    keep, seen, level = {}, set(), {o for o in wanted if o}
    for _ in range(depth + 1):
        nxt = set()
        for org in level - seen:
            seen.add(org)
            for k in by_org.get(org, []):
                keep[k] = agg[k]
                if len(k[2]) == 9:
                    nxt.add(k[2])
        level = nxt
    return keep


def import_shareholders(con, path: Path, year: int | None, wanted: set[str] | None = None, log=print, depth: int = 3) -> int:
    """Importerer én årgang. Beholder leverandører/oppdragsgivere i `wanted` og selskapene som eier dem (inntil `depth` ledd),
    så indirekte eierskap via holdingselskaper kan følges. Erstatter tidligere import av samme år."""
    agg = _closure(read_register(path), wanted, depth)
    rows = []
    for (org, navn, ident), a in agg.items():
        rows.append((org, navn, ident if len(ident) == 9 else None, int(ident) if len(ident) == 4 else None,
                     a["pct"], "aksjonaerregisteret", year, now()))
    with tx(con):
        con.execute("DELETE FROM owners WHERE source='aksjonaerregisteret' AND (year IS ? OR year=?)", (year, year))
        con.executemany("INSERT INTO owners(orgnr, owner_name, owner_orgnr, owner_birth_year, share_pct, source, year, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", rows)
    log(f"Aksjonærregisteret {year or ''}: {len(rows)} eierposter for {len({r[0] for r in rows})} selskaper".replace("  ", " "))
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
    ap = argparse.ArgumentParser(description="Importer aksjonærregisteret fra Skatteetaten, eller lag et kompakt uttrekk")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--year", type=int)
    ap.add_argument("--all", action="store_true", help="ta med alle selskaper, ikke bare leverandører/oppdragsgivere i kontraktene")
    ap.add_argument("--extract", type=Path, help="skriv kompakt uttrekk (.csv.gz) hit i stedet for å importere")
    ap.add_argument("--min-pct", type=float, default=5.0)
    a = ap.parse_args()
    if a.extract:
        extract(a.csv, a.extract, a.min_pct)
        raise SystemExit
    con = connect(settings.db_path)
    wanted = None
    if not a.all:
        wanted = set()
        for r in con.execute("SELECT buyer_id, winner_ids FROM contracts"):
            wanted.update(json.loads(r["winner_ids"] or "[]"))
            wanted.add(r["buyer_id"])
    import_shareholders(con, a.csv, a.year, wanted)
