"""OpenSanctions (sanksjoner og PEP). Forenklet CSV for samlingen «default»:
    https://data.opensanctions.org/datasets/latest/default/targets.simple.csv

Kolonner: id, schema, name, aliases, birth_date, countries, addresses, identifiers, sanctions,
phones, emails, program_ids, dataset, first_seen, last_seen, last_change. Flere verdier skilles med «;».
Lisens: CC BY-NC 4.0. Gratis for ikke-kommersiell bruk, lisens kreves ved salg.
Vi beholder bare rader med Norge i countries eller et 9-sifret norsk orgnr i identifiers.
"""
from __future__ import annotations

import csv
import io
import re

import httpx

from ..db import norm_name, tx

URL = "https://data.opensanctions.org/datasets/latest/default/targets.simple.csv"


def parse_lines(lines) -> list[dict]:
    out = []
    for r in csv.DictReader(lines):
        countries = (r.get("countries") or "").lower().split(";")
        ids = r.get("identifiers") or ""
        orgnr = next((x for x in re.findall(r"\b\d{9}\b", ids)), None)
        if "no" not in countries and not orgnr:
            continue
        bd = (r.get("birth_date") or "")[:4]
        out.append({"id": r["id"], "schema": r.get("schema"), "name": r.get("name"), "name_norm": norm_name(r.get("name")),
                    "birth_year": int(bd) if bd.isdigit() else None, "orgnr": orgnr,
                    "is_sanction": int(bool((r.get("sanctions") or "").strip())), "datasets": r.get("dataset") or ""})
        for alias in (r.get("aliases") or "").split(";"):
            if alias.strip():
                out.append(out[-1] | {"id": f"{r['id']}#{norm_name(alias)}", "name": alias, "name_norm": norm_name(alias)})
    return out


def store(con, rows: list[dict]) -> None:
    with tx(con):
        con.execute("DELETE FROM sanctions")
        con.executemany("INSERT OR REPLACE INTO sanctions(id, schema, name, name_norm, birth_year, orgnr, is_sanction, datasets) "
                        "VALUES (:id,:schema,:name,:name_norm,:birth_year,:orgnr,:is_sanction,:datasets)", rows)


def refresh(con, url: str = URL, api_key: str = "", log=print) -> int:
    headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
    with httpx.stream("GET", url, headers=headers, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        rows = parse_lines(io.TextIOWrapper(_Iter(r.iter_bytes()), encoding="utf-8"))
    store(con, rows)
    log(f"OpenSanctions: {len(rows)} norske oppføringer")
    return len(rows)


class _Iter(io.RawIOBase):
    """Gjør en byte-iterator om til en fil, så CSV kan strømmes uten å lagre hele filen."""

    def __init__(self, it):
        self.it, self.buf = it, b""

    def readable(self):
        return True

    def readinto(self, b):
        while not self.buf:
            try:
                self.buf = next(self.it)
            except StopIteration:
                return 0
        n = min(len(b), len(self.buf))
        b[:n], self.buf = self.buf[:n], self.buf[n:]
        return n
