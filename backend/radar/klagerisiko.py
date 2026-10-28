"""Klagerisiko: hvor sannsynlig er det at en konkurranse havner i KOFA?

Dette måler risiko for klage, ikke korrupsjon. Store konkurranser med mange tilbydere
klages oftere inn, mens direkte anskaffelser uten konkurranse sjelden klages inn.

Positive: Doffin-konkurransekunngjøringer koblet til en KOFA-sak (kofa_link).
Sammenligning: et tilfeldig utvalg av konkurransekunngjøringer i Doffin i samme periode.
Bare felt som finnes når konkurransen kunngjøres brukes, så modellen kan score pågående konkurranser.

    python -m radar.klagerisiko hent      # utvalg av konkurranser til data/cache/doffin_konkurranser.json
    python -m radar.klagerisiko test [--json fil]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .db import norm_name
from .ingest import doffin

COMPETITION = "ANNOUNCEMENT_OF_COMPETITION"
# Doffin tillater 30 kall/min, men kvoten deles med kofa_link: gå roligere
SLOW = 4.0
BUYER_TYPES = [("kommune", r"kommune|bydel|kf\b"), ("fylke", r"fylkeskommune"), ("helse", r"\bhf\b|helse|sykehus"),
               ("innkjøpssamarbeid", r"innkjøp|anskaffelser|iks\b"), ("stat", r"direktorat|departement|etaten|tilsyn|statens|"
                                                                      r"forsvar|politi|universitet|høgskole")]


def fetch_sample(cache: Path, start: str, end: str | None = None, pages_per_month: int = 3, seed: int = 42, log=print) -> int:
    """Tilfeldige sider med konkurransekunngjøringer per måned. Kan avbrytes og startes igjen."""
    data = json.loads(cache.read_text()) if cache.exists() else {"months": [], "hits": {}}
    rng = random.Random(seed)
    m, stop = date.fromisoformat(start).replace(day=1), date.fromisoformat(end) if end else date.today()
    with httpx.Client(headers=doffin._headers("")) as client:
        while m <= stop:
            nxt = (m.replace(day=28) + pd.Timedelta(days=4)).replace(day=1)
            if m.isoformat() in data["months"]:
                m = nxt
                continue
            span = {"type": COMPETITION, "numHitsPerPage": 100, "sortBy": "PUBLICATION_DATE_ASC",
                    "issueDateFrom": m.isoformat(), "issueDateTo": (nxt - pd.Timedelta(days=1)).isoformat()}
            try:
                j = doffin._get(client, {**span, "page": 1})
                time.sleep(SLOW)
                n_pages = max(1, min(10, -(-int(j.get("numHitsAccessible") or 0) // 100)))
                for p in sorted(rng.sample(range(1, n_pages + 1), min(pages_per_month, n_pages))):
                    jj = j if p == 1 else doffin._get(client, {**span, "page": p})
                    if p != 1:
                        time.sleep(SLOW)
                    for h in jj.get("hits") or []:
                        data["hits"][h["id"]] = h
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    raise
                log("  429 fra Doffin, venter 5 min")
                time.sleep(300)
                continue
            data["months"].append(m.isoformat())
            cache.write_text(json.dumps(data, ensure_ascii=False))
            log(f"  {m:%Y-%m}: {len(data['hits'])} konkurranser")
            m = nxt
    return len(data["hits"])


def features(h: dict) -> dict:
    ev = h.get("estimatedValue") or {}
    est = ev.get("amount") if isinstance(ev, dict) and (ev.get("currencyCode") or "NOK") == "NOK" else None
    iss, dl = doffin._date(h.get("issueDate")), doffin._date(h.get("deadline"))
    buyer = norm_name(((h.get("buyer") or [{}])[0] or {}).get("name"))
    cpv = (h.get("cpvCodes") or [""])[0] or ""
    text = f"{h.get('heading') or ''} {h.get('description') or ''}".lower()
    f = {
        "log_est_value": np.log10(est) if est and est > 0 else np.nan,
        "tender_days": (dl - iss).days if iss and dl and dl > iss else np.nan,
        "n_lots": len(h.get("lots") or []),
        "n_cpv": len(h.get("cpvCodes") or []),
        "desc_len": np.log1p(len(h.get("description") or "")),
        "framework": float("rammeavtale" in text),
        "works": float(cpv[:2] == "45"), "services": float(cpv[:1] in "5679" and cpv[:2] != "45"),
        "eos": float("eøs" in text or (est or 0) > 1.4e6),
    }
    for name, pat in BUYER_TYPES:
        f[f"buyer_{name}"] = float(bool(re.search(pat, buyer)))
    return f


def dataset(link_cache: Path, sample_cache: Path) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    pos = {v["hit"]["id"]: v["hit"] for v in json.loads(link_cache.read_text()).values()
           if v.get("hit") and v["hit"].get("type") == COMPETITION}
    neg = {k: h for k, h in json.loads(sample_cache.read_text())["hits"].items() if k not in pos}
    lo = min(h["issueDate"][:10] for h in pos.values())
    neg = {k: h for k, h in neg.items() if h["issueDate"][:10] >= lo}
    hits = list(pos.values()) + list(neg.values())
    X = pd.DataFrame([features(h) for h in hits])
    y = np.array([1] * len(pos) + [0] * len(neg))
    groups = np.array([norm_name(((h.get("buyer") or [{}])[0] or {}).get("name")) for h in hits])
    years = np.array([int(h["issueDate"][:4]) for h in hits])
    return X, y, groups, years


def _ci(y, p, n=1000, seed=0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    v = [roc_auc_score(y[i], p[i]) for i in (rng.integers(0, len(y), len(y)) for _ in range(n)) if 0 < y[i].sum() < len(i)]
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


def evaluate(link_cache: Path, sample_cache: Path, seed: int = 42) -> dict:
    X, y, groups, years = dataset(link_cache, sample_cache)
    models = {
        "Logistisk regresjon": Pipeline([("imp", SimpleImputer(strategy="median", add_indicator=True)), ("sc", StandardScaler()),
                                         ("clf", LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000))]),
        "Gradient Boosting": HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                                            class_weight="balanced", random_state=seed),
    }
    out = {"n": int(len(y)), "positive": int(y.sum()), "models": []}
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)  # samme oppdragsgiver aldri i både trening og test
    test_year = int(years.max())
    tr, te = years < test_year, years == test_year
    for name, m in models.items():
        p = np.zeros(len(y))
        for a, b in cv.split(X, y, groups):
            p[b] = clone(m).fit(X.iloc[a], y[a]).predict_proba(X.iloc[b])[:, 1]
        lo, hi = _ci(y, p)
        res = {"name": name, "auc": float(roc_auc_score(y, p)), "lo": lo, "hi": hi}
        if y[te].sum() >= 10:
            pt = clone(m).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            res["time_auc"] = float(roc_auc_score(y[te], pt))
            res["time_split"] = f"trent til og med {test_year - 1}, testet på {test_year}"
        out["models"].append(res)
    gb = clone(models["Gradient Boosting"]).fit(X, y)
    from sklearn.inspection import permutation_importance
    imp = permutation_importance(gb, X, y, scoring="roc_auc", n_repeats=5, random_state=seed)
    out["importance"] = sorted(((c, float(v)) for c, v in zip(X.columns, imp.importances_mean)), key=lambda t: -t[1])[:6]
    return out


def table(r: dict) -> str:
    lines = [f"Konkurranser: {r['n']}, herav {r['positive']} som havnet i KOFA", "",
             "| Modell | AUC (gruppert CV) | 95 % KI | AUC siste år |", "|---|---|---|---|"]
    for m in r["models"]:
        t = f"{m['time_auc']:.3f}" if "time_auc" in m else "–"
        lines.append(f"| {m['name']} | {m['auc']:.3f} | {m['lo']:.2f}–{m['hi']:.2f} | {t} |")
    lines += ["", "Viktigst: " + ", ".join(f"{c} ({v:.3f})" for c, v in r["importance"])]
    return "\n".join(lines)


if __name__ == "__main__":
    from .config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["hent", "test"])
    ap.add_argument("--fra", default="2021-01-01")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    cache = settings.data_dir / "cache"
    sample = cache / "doffin_konkurranser.json"
    if a.cmd == "hent":
        print(f"{fetch_sample(sample, a.fra)} konkurranser lagret")
    else:
        r = evaluate(cache / "kofa_doffin.json", sample, settings.seed)
        print(table(r))
        if a.json:
            a.json.write_text(json.dumps(r, indent=2, ensure_ascii=False))
