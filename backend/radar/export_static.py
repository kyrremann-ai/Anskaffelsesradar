"""Eksporterer databasen til JSON-filer for den statiske artifact-versjonen av klienten.

    python -m radar.export_static ut/        # skriver contracts-N.json, cards-N.json, meta.json

Klienten (bygget med VITE_STATIC=1) leser filene og regner ut oversikt, filtre og
leverandørlister selv. Saker lagres i artifactens database, ikke her.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pandas as pd

from . import api, db, llm, pipeline
from .scoring import FLAG_LABELS

# Artifacten tillater 16 MB per fil. Deler gir god margin og laster parallelt.
CONTRACTS_PER_FILE = 2500
CARDS_PER_FILE = 2500

FULL = ["url", "cpv", "nature", "accelerated", "justification", "tender_duration_days", "contract_months", "winner_size",
        "repeat_pair_count", "prev_contract", "winner_market_share_pct"]


def _clean(v):
    """NaN/inf er ikke gyldig JSON og stopper klienten. Ødelagte tegn avvises av artifacten."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, str):
        return v.replace("\ufffd", "?")  # tegn TED ikke klarte å kode
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def build_info() -> dict:
    """Hvilken kode som regnet ut prioriteten, og når dataene ble hentet. Vises i bunnteksten og i arbeidspapiret."""
    commit = os.environ.get("GITHUB_SHA")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                                    cwd=Path(__file__).parent).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = None
    return {"commit": commit, "built_at": db.now(), "data_updated_at": db.get_meta(api.con, "updated_at"),
            "sources": {k: v.get("at") for k, v in (db.get_meta(api.con, "sources") or {}).items()}}


def export(out: Path, log=print) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    df = api.frame()
    journal = {r["contract_id"]: json.loads(r["items"]) for r in api.con.execute("SELECT contract_id, items FROM einnsyn")}
    rows = []
    for i, r in df.iterrows():
        o = api.row_out(r, full=True)
        o.pop("case_id", None)
        o.pop("case_status", None)
        o["winner"] = r.winner if isinstance(r.winner, str) else None
        if r.id in journal:
            o["einnsyn"] = journal[r.id]
        rows.append(o)
    bi = api.brreg_index()
    orgs = sorted({o for ws in df.winner_ids for o in ws} | set(df.buyer_id))
    cards = {}
    for o in orgs:
        c = api._supplier_card(bi, o)
        # Personnøkler (hash) trengs ikke i klienten
        c["roles"] = [{k: v for k, v in r.items() if k != "person_key"} for r in c["roles"]]
        keys = bi.people.get(o, set())
        links = []
        if keys:
            q = f"SELECT DISTINCT orgnr, rolle_navn, navn FROM brreg_roles WHERE person_key IN ({','.join('?' * len(keys))}) AND orgnr != ?"
            for r in api.con.execute(q, (*keys, o)):
                e = bi.ent.get(r["orgnr"]) or {}
                links.append({"orgnr": r["orgnr"], "name": e.get("navn") or r["orgnr"], "via": r["navn"], "rolle": r["rolle_navn"],
                              "is_buyer": r["orgnr"] in set(df.buyer_id)})
        c["links"] = links
        cards[o] = c
    meta = api.meta() | {"refresh": {"running": False, "step": None, "log": [], "error": None}, "llm": "av", "doffin": False,
                         "build": build_info()}
    files: dict[str, object] = {}
    keys = list(cards)
    for i in range(0, max(len(rows), 1), CONTRACTS_PER_FILE):
        files[f"contracts-{i // CONTRACTS_PER_FILE + 1}.json"] = rows[i:i + CONTRACTS_PER_FILE]
    for i in range(0, max(len(keys), 1), CARDS_PER_FILE):
        files[f"cards-{i // CARDS_PER_FILE + 1}.json"] = {k: cards[k] for k in keys[i:i + CARDS_PER_FILE]}
    parts = {"contracts": [f for f in files if f.startswith("contracts-")], "cards": [f for f in files if f.startswith("cards-")]}
    files["meta.json"] = {"meta": meta, "model": api.model(), "flag_labels": {k: v[0] for k, v in FLAG_LABELS.items()}, "files": parts,
                         # Samme instruks som serverens AI-vurdering, så artifacten ber Claude om det samme
                         "llm_system": llm.SYSTEM}
    # Gamle filer fra forrige eksport fjernes, så publiseringen ikke tar med utdaterte deler
    for old in [*out.glob("contracts*.json"), *out.glob("cards*.json")]:
        if old.name not in files:
            old.unlink()
    sizes = {}
    for name, data in files.items():
        s = json.dumps(_clean(data), ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=lambda v: None if pd.isna(v) else str(v))
        (out / name).write_text(s, encoding="utf-8")
        sizes[name] = len(s)
    log(f"Eksporterte {len(rows)} kontrakter og {len(cards)} virksomheter til {out}")
    return sizes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    if db.get_meta(api.con, "updated_at") is None:
        pipeline.run(api.con)
    print(export(a.out))

