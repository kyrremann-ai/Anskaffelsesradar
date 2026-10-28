"""KOFA-avgjørelser (Klagenemnda for offentlige anskaffelser).

KOFA har ingen åpen API. Avgjørelsene importeres fra CSV eller JSON (eksport fra kofa.no-søk
eller manuelt kuratert), med kolonnene:
    saksnr; dato; innklaget; innklaget_orgnr; leverandor; tittel; brudd (ja/nei); ulovlig_direkte (ja/nei); url

    python -m radar.ingest.kofa kofa.csv

Sakene kobles til kontrakter (samme oppdragsgiver, dato innen 3 år før avgjørelsen,
og lik tittel eller samme leverandør). Koblede saker blir ekte fasit for modellene,
se scoring.ModelBundle(labels=...).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..db import connect, norm_name, tx

YES = {"ja", "j", "1", "true", "x", "brudd", "yes"}


def parse(text: str) -> list[dict]:
    if text.lstrip().startswith("["):
        items = json.loads(text)
    else:
        delim = ";" if text.count(";") > text.count(",") else ","
        items = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    out = []
    for it in items:
        it = {str(k).strip().lower(): (str(v).strip() if v is not None else "") for k, v in it.items()}
        if not it.get("saksnr"):
            continue
        org = re.sub(r"\D", "", it.get("innklaget_orgnr", ""))
        out.append({"saksnr": it["saksnr"], "dato": it.get("dato", "")[:10], "innklaget": it.get("innklaget", ""),
                    "innklaget_orgnr": org if len(org) == 9 else None, "leverandor": it.get("leverandor", ""),
                    "tittel": it.get("tittel", ""), "brudd": int(it.get("brudd", "").lower() in YES),
                    "ulovlig_direkte": int(it.get("ulovlig_direkte", "").lower() in YES), "url": it.get("url") or None})
    return out


def store(con, rows: list[dict]) -> None:
    cols = ["saksnr", "dato", "innklaget", "innklaget_orgnr", "leverandor", "tittel", "brudd", "ulovlig_direkte", "url"]
    with tx(con):
        con.executemany(f"INSERT OR REPLACE INTO kofa_cases({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r[c] for c in cols] for r in rows])


def _tokens(s: str) -> set[str]:
    return {w for w in norm_name(s).split() if len(w) > 3}


def match(con, window_years: int = 3) -> int:
    """Kobler KOFA-saker til kontrakter. Returnerer antall koblinger."""
    contracts = [dict(r) for r in con.execute("SELECT id, buyer_id, buyer, title, pub_date, winner_names FROM contracts")]
    by_buyer: dict[str, list[dict]] = {}
    for c in contracts:
        by_buyer.setdefault(c["buyer_id"], []).append(c)
        by_buyer.setdefault("name:" + norm_name(c["buyer"]), []).append(c)
    out = []
    for k in con.execute("SELECT * FROM kofa_cases"):
        try:
            d = datetime.strptime(k["dato"], "%Y-%m-%d")
        except ValueError:
            continue
        cands = by_buyer.get(k["innklaget_orgnr"] or "", []) or by_buyer.get("name:" + norm_name(k["innklaget"]), [])
        kt, kl = _tokens(k["tittel"]), norm_name(k["leverandor"])
        for c in cands:
            try:
                cd = datetime.strptime(c["pub_date"][:10], "%Y-%m-%d")
            except (TypeError, ValueError):
                continue
            if not (d - timedelta(days=365 * window_years) <= cd <= d):
                continue
            ct = _tokens(c["title"] or "")
            sim = len(kt & ct) / max(1, len(kt | ct))
            same_sup = bool(kl) and any(norm_name(w) == kl for w in json.loads(c["winner_names"] or "[]"))
            score = sim + (0.5 if same_sup else 0)
            if score >= 0.35:
                out.append((k["saksnr"], c["id"], round(score, 2)))
    with tx(con):
        con.execute("DELETE FROM kofa_matches")
        con.executemany("INSERT OR REPLACE INTO kofa_matches(saksnr, contract_id, score) VALUES (?,?,?)", out)
    return len(out)


def labels(con) -> dict[str, int]:
    """contract_id -> 1 (brudd) / 0 (ikke brudd). Beste kobling per sak."""
    rows = con.execute("""SELECT m.contract_id, k.brudd FROM kofa_matches m JOIN kofa_cases k USING (saksnr)
                          WHERE m.score = (SELECT MAX(score) FROM kofa_matches m2 WHERE m2.saksnr = m.saksnr)""")
    out: dict[str, int] = {}
    for r in rows:
        out[r["contract_id"]] = max(out.get(r["contract_id"], 0), r["brudd"])
    return out


if __name__ == "__main__":
    from ..config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    a = ap.parse_args()
    con = connect(settings.db_path)
    store(con, parse(a.file.read_text(encoding="utf-8-sig")))
    print(f"Koblet {match(con)} saker til kontrakter. Kjør «Hent nye data» for å trene på nytt.")
