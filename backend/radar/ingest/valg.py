"""Kandidatlister fra Valgdirektoratet (valg.no, «Valglister og kandidater», CSV/XLSX).

Importeres fra nedlastet fil:
    python -m radar.ingest.valg kandidater-2023.csv --valg "Kommunestyrevalget 2023" [--valgte valgte.csv]

Overskriftene matches løst: navn, fødselsår, kommunenummer/kommune, parti.
Personvern: vi lagrer bare normalisert navn, fødselsår, kommune og parti. Treff vises som
«mulig kobling» og må alltid verifiseres manuelt (navnelikhet). Gjør en DPIA før produksjon.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
from pathlib import Path

from ..db import connect, norm_name, tx


def _find(h: list[str], *keys) -> int | None:
    for k in keys:
        for i, c in enumerate(h):
            if k in c:
                return i
    return None


def parse(text: str) -> list[dict]:
    delim = ";" if text.count(";") > text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    h = [c.strip().lower() for c in rows[0]]
    i_navn = _find(h, "navn", "kandidat")
    i_for = _find(h, "fornavn")
    i_etter = _find(h, "etternavn")
    i_aar = _find(h, "fødselsår", "fodselsar", "født", "fodt")
    i_knr = _find(h, "kommunenummer", "kommunenr")
    i_kom = _find(h, "kommunenavn", "kommune")
    i_parti = _find(h, "partinavn", "parti")
    i_valgt = _find(h, "valgt", "innvalgt")
    out = []
    for r in rows[1:]:
        if not r:
            continue
        g = lambda i: r[i].strip() if i is not None and i < len(r) else ""  # noqa: E731
        navn = f"{g(i_for)} {g(i_etter)}".strip() if i_for is not None and i_etter is not None else g(i_navn)
        aar = re.sub(r"\D", "", g(i_aar))[:4]
        if not navn or len(aar) != 4:
            continue
        knr = re.sub(r"\D", "", g(i_knr)).zfill(4) if g(i_knr) else None
        out.append({"navn_norm": norm_name(navn), "fodselsaar": int(aar), "kommunenr": knr, "kommune": g(i_kom),
                    "parti": g(i_parti), "valgt": int(g(i_valgt).lower() in ("ja", "1", "true", "x", "valgt"))})
    return out


def store(con, rows: list[dict], valg: str) -> None:
    with tx(con):
        con.execute("DELETE FROM politicians WHERE valg=?", (valg,))
        con.executemany("INSERT INTO politicians(navn_norm, fodselsaar, kommunenr, kommune, parti, valgt, valg) VALUES (?,?,?,?,?,?,?)",
                        [(r["navn_norm"], r["fodselsaar"], r["kommunenr"], r["kommune"], r["parti"], r["valgt"], valg) for r in rows])


if __name__ == "__main__":
    from ..config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--valg", default="Kommunestyrevalget 2023")
    a = ap.parse_args()
    raw = a.csv.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = parse(text)
    store(connect(settings.db_path), rows, a.valg)
    print(f"Importerte {len(rows)} kandidater")
