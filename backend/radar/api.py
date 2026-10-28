"""FastAPI-backend for Anskaffelsesradar.

Start lokalt:  uvicorn radar.api:app --reload
"""
from __future__ import annotations

import asyncio
import html
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, llm, pipeline
from .config import settings
from .ingest import doffin
from .ingest.brreg import ROLE_CODES, BrregIndex, entity_summary
from .scoring import FEATURES, FLAG_LABELS, contracts_frame, feature_frame, model_info

con = db.connect(settings.db_path)
CASE_STATUSES = ["Ny", "Under vurdering", "Fulgt opp", "Avsluttet – ingen funn", "Avsluttet – funn"]


async def _scheduler():
    while settings.refresh_hours > 0:
        await asyncio.sleep(settings.refresh_hours * 3600)
        pipeline.run_in_background(con)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not con.execute("SELECT 1 FROM scores LIMIT 1").fetchone():
        pipeline.run_in_background(con)  # første oppstart: last data og scor
    task = asyncio.create_task(_scheduler())
    yield
    task.cancel()


app = FastAPI(title="Anskaffelsesradar", version="2.0", lifespan=lifespan)

# ---------------------------------------------------------------- data i minnet

_cache: dict = {"key": None, "df": None, "brreg": None}


def frame() -> pd.DataFrame:
    """Kontrakter + scorer + saksstatus som én DataFrame. Basen bygges på nytt bare når data er oppdatert."""
    key = db.get_meta(con, "updated_at")
    if _cache["key"] != key or _cache["df"] is None:
        df = contracts_frame(con)
        if not df.empty:
            s = pd.read_sql_query("SELECT * FROM scores", con).rename(columns={"contract_id": "id"})
            s = s.drop(columns=[c for c in ("repeat_pair_count", "prev_contract", "winner_market_share_pct", "award_over_estimate_pct") if c in s])
            df = df.merge(s, on="id", how="inner")
            df["flags"] = df["flags"].map(lambda x: json.loads(x or "[]"))
            df["flag_keys"] = df["flags"].map(lambda fl: {f["key"] for f in fl})
        _cache.update(key=key, df=df, brreg=BrregIndex(con))
    df = _cache["df"]
    if df.empty:
        return df
    # Saksstatus endres ofte og hentes ferskt (liten tabell)
    cases = pd.read_sql_query("SELECT contract_id AS id, id AS case_id, status AS case_status FROM cases", con)
    return df.merge(cases, on="id", how="left")


def brreg_index() -> BrregIndex:
    frame()
    return _cache["brreg"] or BrregIndex(con)


def scope(df: pd.DataFrame, buyers: str | None) -> pd.DataFrame:
    if buyers:
        ids = {b for b in buyers.split(",") if b}
        df = df[df.buyer_id.isin(ids)]
    return df


def clean(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, set):
        return sorted(v)
    return v


def row_out(r: pd.Series, full: bool = False) -> dict:
    keys = ["id", "title", "pub_date", "buyer_id", "buyer", "region", "sector", "method", "num_bidders", "est_value",
            "award_value", "award_over_estimate_pct", "priority", "tier", "percentile", "ml_score", "cri", "anomaly",
            "winner_names", "winner_ids", "flags", "data_coverage", "framework", "case_status", "source"]
    if full:
        keys += ["url", "cpv", "nature", "accelerated", "justification", "tender_duration_days", "contract_months",
                 "winner_size", "repeat_pair_count", "prev_contract", "winner_market_share_pct", "ml_lr", "ml_rf", "ml_gb", "case_id"]
    return {k: clean(r.get(k)) for k in keys}


def pct(a, b) -> float | None:
    return round(100 * a / b, 1) if b else None


# ---------------------------------------------------------------- meta / refresh

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {"mode": db.get_meta(con, "mode", settings.mode), "updated_at": db.get_meta(con, "updated_at"),
            "refresh": pipeline.status, "llm": "mistral" if settings.mistral_api_key else "mal",
            "doffin": bool(settings.doffin_api_key), "case_statuses": CASE_STATUSES,
            "flag_labels": {k: v[0] for k, v in FLAG_LABELS.items()}}


@app.post("/api/refresh")
def refresh():
    if not pipeline.run_in_background(con):
        raise HTTPException(409, "En oppdatering kjører allerede")
    return {"started": True}


@app.get("/api/buyers")
def buyers():
    df = frame()
    if df.empty:
        return []
    g = df.groupby(["buyer_id", "buyer"]).size().reset_index(name="n").sort_values("buyer")
    return [{"id": r.buyer_id, "name": r.buyer, "n": int(r.n)} for r in g.itertuples()]


# ---------------------------------------------------------------- oversikt

def _stats(d: pd.DataFrame) -> dict:
    known = d.num_bidders.notna()
    return {"n": len(d), "n_high": int((d.tier == "Høy").sum()), "n_elevated": int((d.tier == "Forhøyet").sum()),
            "single_bid_pct": pct(int((d.num_bidders[known] == 1).sum()), int(known.sum())),
            "direct_pct": pct(int((d.method == "Direkte tildeling").sum()), len(d)),
            "total_value": float(d.award_value.sum()), "avg_priority": round(float(d.priority.mean()), 1) if len(d) else None,
            "coverage_pct": pct(int((d.est_value.notna() & d.award_value.notna()).sum()), len(d))}


@app.get("/api/summary")
def summary(buyers: str | None = None):
    all_ = frame()
    if all_.empty:
        return {"n": 0, "loading": pipeline.status["running"]}
    df = scope(all_, buyers)
    out = _stats(df) | {"national": _stats(all_) if buyers else None, "n_buyers": int(df.buyer_id.nunique()),
                        "period": [df.pub_date.min(), df.pub_date.max()] if len(df) else [None, None]}
    m = df.assign(month=df.pub_date.str[:7], flagged=df.tier.isin(["Høy", "Forhøyet"]),
                  direct=df.method.eq("Direkte tildeling"))
    out["monthly"] = [{"month": k, "n": int(g.shape[0]), "flagged": int(g.flagged.sum()), "direct": int(g.direct.sum())}
                      for k, g in m.groupby("month")]
    counts: dict[str, list] = {}
    for fl in df["flags"]:
        for f in fl:
            counts.setdefault(f["key"], [f["label"].split(" (")[0], 0])[1] += 1
    out["flag_counts"] = sorted(({"key": k, "label": v[0], "n": v[1]} for k, v in counts.items()), key=lambda d: -d["n"])
    out["by_sector"] = _group(df, "sector")
    out["by_region"] = _group(df, "region")
    out["by_buyer"] = _group(df, "buyer_id", min_n=1 if buyers else 8, name_col="buyer")
    out["cases"] = {s: int(n) for s, n in df.case_status.value_counts().items()}
    return out


def _group(df: pd.DataFrame, col: str, min_n: int = 1, name_col: str | None = None) -> list[dict]:
    rows = []
    for k, g in df.groupby(col):
        if len(g) < min_n:
            continue
        st = _stats(g)
        rows.append({"name": g[name_col].iloc[0] if name_col else k, "buyer_id": k if name_col else None, "n": len(g),
                     "avg_score": st["avg_priority"], "single_bid_pct": st["single_bid_pct"], "direct_pct": st["direct_pct"],
                     "n_flagged": st["n_high"] + st["n_elevated"], "n_high": st["n_high"], "total_value": st["total_value"],
                     "high_share": st["n_high"] / len(g)})
    return sorted(rows, key=lambda d: -d["n"])


# ---------------------------------------------------------------- kontrakter

def _filter(df, q, tier, method, sector, region, flag, supplier, case_status):
    if q:
        ql = q.lower()
        hay = (df.title.fillna("") + " " + df.buyer.fillna("") + " " + df.winner_names.map(" ".join)).str.lower()
        df = df[hay.str.contains(ql, regex=False)]
    for col, v in (("tier", tier), ("method", method), ("sector", sector), ("region", region)):
        if v:
            df = df[df[col] == v]
    if flag:
        df = df[df.flag_keys.map(lambda s: flag in s)]
    if supplier:
        df = df[df.winner_ids.map(lambda w: supplier in w)]
    if case_status == "uten":
        df = df[df.case_status.isna()]
    elif case_status == "med":
        df = df[df.case_status.notna()]
    elif case_status:
        df = df[df.case_status == case_status]
    return df


SORTABLE = {"priority", "pub_date", "num_bidders", "award_value", "award_over_estimate_pct", "cri", "ml_score"}


@app.get("/api/contracts")
def contracts(buyers: str | None = None, q: str = "", tier: str = "", method: str = "", sector: str = "",
              region: str = "", flag: str = "", supplier: str = "", case_status: str = "",
              sort: str = "priority", dir: str = "desc", page: int = 1, limit: int = Query(50, le=500)):
    df = frame()
    if df.empty:
        return {"rows": [], "total": 0}
    df = _filter(scope(df, buyers), q, tier, method, sector, region, flag, supplier, case_status)
    sort = sort if sort in SORTABLE else "priority"
    df = df.sort_values(sort, ascending=dir == "asc", na_position="last")
    page_df = df.iloc[(page - 1) * limit: page * limit]
    return {"rows": [row_out(r) for _, r in page_df.iterrows()], "total": len(df)}


@app.get("/api/contracts/export.csv")
def export(buyers: str | None = None, q: str = "", tier: str = "", method: str = "", sector: str = "",
           region: str = "", flag: str = "", supplier: str = "", case_status: str = ""):
    df = _filter(scope(frame(), buyers), q, tier, method, sector, region, flag, supplier, case_status)
    out = df.assign(flagg=df["flags"].map(lambda fl: "; ".join(f["label"] for f in fl)),
                    leverandor=df.winner_names.map("; ".join))
    cols = ["id", "pub_date", "title", "buyer", "leverandor", "method", "num_bidders", "est_value", "award_value",
            "award_over_estimate_pct", "priority", "tier", "ml_score", "cri", "anomaly", "flagg", "case_status", "url"]
    buf = io.StringIO()
    out.sort_values("priority", ascending=False)[cols].to_csv(buf, index=False, sep=";", decimal=",")
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=anskaffelsesradar.csv"})


def _contract(cid: str) -> pd.Series:
    df = frame()
    m = df[df.id == cid]
    if m.empty:
        raise HTTPException(404, "Fant ikke kontrakten")
    return m.iloc[0]


def _case(cid: str) -> dict | None:
    c = con.execute("SELECT * FROM cases WHERE contract_id=?", (cid,)).fetchone()
    if not c:
        return None
    ev = con.execute("SELECT at, actor, field, old, new FROM case_events WHERE case_id=? ORDER BY id DESC", (c["id"],)).fetchall()
    return dict(c) | {"events": [dict(e) for e in ev]}


@app.get("/api/contracts/{cid}")
def contract_detail(cid: str):
    df = frame()
    r = _contract(cid)
    out = row_out(r, full=True)
    x = feature_frame(pd.DataFrame([r])).iloc[0]
    out["drivers"] = pipeline.bundle().drivers(x)
    same = df[(df.buyer_id == r.buyer_id) & (df.winner == r.winner) & (df.id != cid)].sort_values("pub_date", ascending=False)
    out["sameSupplier"] = [{"id": s.id, "title": s.title, "pub_date": s.pub_date, "tier": s.tier} for s in same.head(10).itertuples()]
    bi = brreg_index()
    out["suppliers"] = [_supplier_card(bi, o) for o in r.winner_ids]
    out["case"] = _case(cid)
    out["assessments"] = [dict(a) for a in con.execute(
        "SELECT id, at, provider, model, text FROM assessments WHERE contract_id=? ORDER BY id DESC", (cid,))]
    return out


@app.post("/api/contracts/{cid}/assess")
def assess(cid: str):
    r = _contract(cid)
    c = row_out(r, full=True)
    try:
        inp, text = llm.assess(llm.provider(settings.mistral_api_key, settings.mistral_model), c)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI-vurdering feilet: {e}") from e
    p = llm.provider(settings.mistral_api_key, settings.mistral_model)
    with db.tx(con):
        cur = con.execute("INSERT INTO assessments(contract_id, at, provider, model, input, text) VALUES (?,?,?,?,?,?)",
                          (cid, db.now(), p.name, p.model, json.dumps(inp, ensure_ascii=False, default=str), text))
    return {"id": cur.lastrowid, "at": db.now(), "provider": p.name, "model": p.model, "text": text, "input": inp}


# ---------------------------------------------------------------- leverandører (Brreg)

def _supplier_card(bi: BrregIndex, orgnr: str) -> dict:
    e = bi.ent.get(orgnr)
    roles = [dict(r) for r in con.execute(
        "SELECT rolle, rolle_navn, type, navn, person_key FROM brreg_roles WHERE orgnr=? ORDER BY rolle", (orgnr,))]
    return {"orgnr": orgnr, "brreg": e, "roles": roles, "brreg_url": f"https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"}


@app.get("/api/suppliers")
def suppliers(buyers: str | None = None, q: str = "", only_flagged: bool = False, limit: int = Query(200, le=2000)):
    df = scope(frame(), buyers)
    if df.empty:
        return {"rows": [], "total": 0}
    ex = df.explode("winner_ids").dropna(subset=["winner_ids"])
    bi = brreg_index()
    names = {o: n for ws, ns in zip(df.winner_ids, df.winner_names) for o, n in zip(ws, ns) if n}
    rows = []
    for orgnr, g in ex.groupby("winner_ids"):
        bflags = sorted({f["label"] for fl in g["flags"] for f in fl if f.get("source") == "brreg"})
        e = bi.ent.get(orgnr) or {}
        rows.append({"orgnr": orgnr, "name": e.get("navn") or names.get(orgnr) or orgnr,
                     "n": len(g), "n_buyers": int(g.buyer_id.nunique()), "total_value": float(g.award_value.sum()),
                     "avg_priority": round(float(g.priority.mean()), 1), "n_high": int((g.tier == "Høy").sum()),
                     "brreg_flags": bflags, "orgform": e.get("orgform"), "stiftet": e.get("stiftet"),
                     "ansatte": e.get("ansatte"), "in_brreg": bool(e)})
    if q:
        rows = [r for r in rows if q.lower() in (r["name"] or "").lower() or q in r["orgnr"]]
    if only_flagged:
        rows = [r for r in rows if r["brreg_flags"]]
    rows.sort(key=lambda r: (-len(r["brreg_flags"]), -r["avg_priority"]))
    return {"rows": rows[:limit], "total": len(rows)}


@app.get("/api/suppliers/{orgnr}")
def supplier_detail(orgnr: str, buyers: str | None = None):
    bi = brreg_index()
    card = _supplier_card(bi, orgnr)
    df = scope(frame(), buyers)
    mine = df[df.winner_ids.map(lambda w: orgnr in w)].sort_values("priority", ascending=False)
    # Andre virksomheter som deler kontrollroller med denne leverandøren
    keys = bi.people.get(orgnr, set())
    links = []
    if keys:
        for r in con.execute(f"SELECT DISTINCT orgnr, rolle_navn, navn FROM brreg_roles WHERE person_key IN ({','.join('?' * len(keys))}) AND orgnr != ?",
                             (*keys, orgnr)):
            e = bi.ent.get(r["orgnr"]) or {}
            links.append({"orgnr": r["orgnr"], "name": e.get("navn") or r["orgnr"], "via": r["navn"], "rolle": r["rolle_navn"],
                          "is_buyer": bool((frame().buyer_id == r["orgnr"]).any())})
    return card | {"contracts": [row_out(r) for _, r in mine.iterrows()], "links": links}


# ---------------------------------------------------------------- Doffin

@app.get("/api/doffin/latest")
def doffin_latest(q: str = "", page: int = 1):
    stats = {g["buyer_id"]: g for g in _group(frame(), "buyer_id", name_col="buyer")} if not frame().empty else {}
    try:
        return doffin.latest(settings.doffin_api_key, q, page, buyer_stats=stats)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Doffin svarte ikke: {e}") from e


# ---------------------------------------------------------------- modell

@app.get("/api/model")
def model():
    info = model_info(con) or pipeline.bundle().info
    return info | {"weights": db.get_meta(con, "weights"), "flags": [{"key": k, "label": v[0], "weight": v[1]} for k, v in FLAG_LABELS.items()],
                   "n_features": len(FEATURES)}


# ---------------------------------------------------------------- saker

class CaseIn(BaseModel):
    contractId: str
    status: str = "Ny"
    assignee: str = ""
    note: str = ""


class CasePatch(BaseModel):
    status: str | None = None
    assignee: str | None = None
    note: str | None = None


def actor(req: Request) -> str:
    return req.headers.get("x-remote-user") or req.headers.get("remote-user") or "revisor"


@app.get("/api/cases")
def list_cases(buyers: str | None = None):
    df = scope(frame(), buyers)
    ids = set(df.id)
    out = []
    for c in con.execute("SELECT * FROM cases ORDER BY updated_at DESC"):
        if c["contract_id"] in ids:
            r = df[df.id == c["contract_id"]].iloc[0]
            out.append(dict(c) | {"contractId": c["contract_id"], "priority": clean(r.priority), "tier": r.tier})
    return out


@app.post("/api/cases")
def create_case(body: CaseIn, req: Request):
    r = _contract(body.contractId)
    t = db.now()
    with db.tx(con):
        cur = con.execute("INSERT OR IGNORE INTO cases(contract_id, title, buyer, score, status, assignee, note, created_at, updated_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (body.contractId, r.title, r.buyer, float(r.priority), body.status, body.assignee, body.note, t, t))
        if cur.rowcount:
            con.execute("INSERT INTO case_events(case_id, at, actor, field, old, new) VALUES (?,?,?,?,?,?)",
                        (cur.lastrowid, t, actor(req), "opprettet", None, body.status))
    return _case(body.contractId)


@app.patch("/api/cases/{case_id}")
def update_case(case_id: int, body: CasePatch, req: Request):
    c = con.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Fant ikke saken")
    if body.status and body.status not in CASE_STATUSES:
        raise HTTPException(422, f"Ugyldig status. Gyldige: {CASE_STATUSES}")
    t = db.now()
    with db.tx(con):
        for field, new in body.model_dump(exclude_none=True).items():
            if new != c[field]:
                con.execute(f"UPDATE cases SET {field}=?, updated_at=? WHERE id=?", (new, t, case_id))
                con.execute("INSERT INTO case_events(case_id, at, actor, field, old, new) VALUES (?,?,?,?,?,?)",
                            (case_id, t, actor(req), field, c[field], new))
    return _case(c["contract_id"])


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: int):
    with db.tx(con):
        con.execute("DELETE FROM case_events WHERE case_id=?", (case_id,))
        con.execute("DELETE FROM cases WHERE id=?", (case_id,))
    return {"deleted": case_id}


@app.get("/api/cases/{case_id}/arbeidspapir", response_class=HTMLResponse)
def workpaper(case_id: int):
    """Utskriftsvennlig arbeidspapir (lagre som PDF fra nettleseren)."""
    c = con.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Fant ikke saken")
    d = contract_detail(c["contract_id"])
    e = html.escape
    fmt = lambda v: "–" if v is None else (f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else e(str(v)))  # noqa: E731
    flags = "".join(f"<li>{e(f['label'])}{' – ' + e(f['why']) if f.get('why') else ''} <small>(kilde: {e(f['source'])})</small></li>" for f in d["flags"]) or "<li>Ingen</li>"
    events = "".join(f"<tr><td>{e(v['at'])}</td><td>{e(v['actor'] or '')}</td><td>{e(v['field'])}</td><td>{e(str(v['old'] or ''))}</td><td>{e(str(v['new'] or ''))}</td></tr>" for v in d["case"]["events"])
    ai = "".join(f"<h3>AI-vurdering {e(a['at'])} ({e(a['provider'])}/{e(a['model'])})</h3><pre>{e(a['text'])}</pre>" for a in d["assessments"])
    sup = "".join(f"<li>{e((s['brreg'] or {}).get('navn') or s['orgnr'])} – orgnr {e(s['orgnr'])}, stiftet {e(str((s['brreg'] or {}).get('stiftet') or '–'))}</li>" for s in d["suppliers"])
    body = f"""<!doctype html><html lang="nb"><meta charset="utf-8"><title>Arbeidspapir sak {case_id}</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:800px;margin:24px auto;padding:0 16px;color:#111}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:4px 6px;text-align:left;font-size:13px}}
pre{{white-space:pre-wrap;background:#f5f5f5;padding:8px}}small{{color:#666}}</style>
<h1>Arbeidspapir: {e(d['title'] or '')}</h1>
<p><b>Oppdragsgiver:</b> {e(d['buyer'] or '')} &nbsp; <b>Kunngjøring:</b> {e(d['id'])} ({e(d['pub_date'] or '')})<br>
<b>Status:</b> {e(c['status'])} &nbsp; <b>Ansvarlig:</b> {e(c['assignee'] or '–')} &nbsp; <b>Utskrevet:</b> {db.now()}</p>
<p><b>Prioritet:</b> {fmt(d['priority'])} ({e(d['tier'])}) – ML {fmt(d['ml_score'])}, CRI {fmt(d['cri'])}, avvik {fmt(d['anomaly'])}.
Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.</p>
<h2>Nøkkeldata</h2><table>
<tr><th>Prosedyre</th><td>{e(d['method'] or '')}</td></tr><tr><th>Antall tilbud</th><td>{fmt(d['num_bidders'])}</td></tr>
<tr><th>Estimert verdi</th><td>{fmt(d['est_value'])}</td></tr><tr><th>Tildelt verdi</th><td>{fmt(d['award_value'])}</td></tr>
<tr><th>Leverandør(er)</th><td>{e(', '.join(d['winner_names'] or []))}</td></tr><tr><th>Kilde</th><td>{e(d['url'] or d['source'])}</td></tr></table>
<h2>Røde flagg</h2><ul>{flags}</ul><h2>Leverandør (Brønnøysundregistrene)</h2><ul>{sup or '<li>–</li>'}</ul>
<h2>Notat</h2><pre>{e(c['note'] or '')}</pre>{ai}
<h2>Logg</h2><table><tr><th>Tid</th><th>Hvem</th><th>Felt</th><th>Fra</th><th>Til</th></tr>{events}</table></html>"""
    return HTMLResponse(body)


# ---------------------------------------------------------------- klient

_web = Path(__file__).resolve().parent.parent / "web"
if _web.exists():  # i Docker-imaget kopieres den bygde klienten hit
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
