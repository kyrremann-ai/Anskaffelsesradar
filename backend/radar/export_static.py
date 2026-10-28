"""Eksporterer databasen til JSON-filer for den statiske artifact-versjonen av klienten.

    python -m radar.export_static ut/        # skriver contracts.json, cards.json, meta.json

Klienten (bygget med VITE_STATIC=1) leser filene og regner ut oversikt, filtre og
leverandørlister selv. Saker lagres i artifactens database, ikke her.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import api, db, pipeline
from .scoring import FLAG_LABELS, feature_frame

FULL = ["url", "cpv", "nature", "accelerated", "justification", "tender_duration_days", "contract_months", "winner_size",
        "repeat_pair_count", "prev_contract", "winner_market_share_pct", "ml_lr", "ml_rf", "ml_gb"]


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


def export(out: Path, log=print) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    df = api.frame()
    bundle = pipeline.bundle()
    X = feature_frame(df)
    journal = {r["contract_id"]: json.loads(r["items"]) for r in api.con.execute("SELECT contract_id, items FROM einnsyn")}
    rows = []
    for i, r in df.iterrows():
        o = api.row_out(r, full=True)
        o.pop("case_id", None)
        o.pop("case_status", None)
        o["winner"] = r.winner if isinstance(r.winner, str) else None
        o["drivers"] = bundle.drivers(X.loc[i])
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
    meta = api.meta() | {"refresh": {"running": False, "step": None, "log": [], "error": None}, "llm": "av", "doffin": False}
    files = {
        "contracts.json": rows,
        "cards.json": cards,
        "meta.json": {"meta": meta, "model": api.model(), "flag_labels": {k: v[0] for k, v in FLAG_LABELS.items()}},
    }
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

