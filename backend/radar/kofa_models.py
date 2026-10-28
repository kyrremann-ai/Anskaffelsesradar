"""Sammenligner modellvalg mot KOFA-fasit, med kryssvalidert AUC og 95 % konfidensintervall.

    python -m radar.kofa_models [--json resultat.json]

Fasit: KOFA-saker koblet til en kontrakt (kofa.match) eller til en Doffin-kunngjøring
(kofa_link). Kandidater:
- Baseline: dagens modeller trent på syntetiske data, testet direkte på KOFA
- CRI: summen av røde flagg (regelbasert, ingen trening)
- Svak veiledning: røde flagg som merkefunksjoner på alle kontrakter, modell trent på dem
- Regularisert LR og CatBoost trent på KOFA (gjentatt 5-fold CV)
- PU-læring: KOFA-brudd mot umerkede kontrakter (positive + umerket), testet på KOFA
AUC er gjennomsnitt over 5 gjentak; intervallet er bootstrap av de kryssvaliderte prediksjonene.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .ingest import kofa
from .ingest.kofa_link import hit_row
from .scoring import CRI_MAX, ModelBundle, contracts_frame, feature_frame, rule_flags


def labelled(con, link_cache: Path | None) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, set[str]]:
    """(alle kontrakter, merkede rader, fasit). Kontraktkobling går foran Doffin-kobling per sak."""
    cases = {r["saksnr"]: dict(r) for r in con.execute("SELECT * FROM kofa_cases")}
    by_case: dict[str, str] = {}
    for r in con.execute("""SELECT saksnr, contract_id FROM kofa_matches m
                            WHERE score = (SELECT MAX(score) FROM kofa_matches m2 WHERE m2.saksnr = m.saksnr)"""):
        by_case.setdefault(r["saksnr"], r["contract_id"])
    extra, linked = [], set()
    if link_cache and link_cache.exists():
        known = {r["id"] for r in con.execute("SELECT id FROM contracts")}
        for saksnr, v in json.loads(link_cache.read_text()).items():
            if not v.get("hit") or saksnr in by_case or saksnr not in cases:
                continue
            row = hit_row(v["hit"])
            by_case[saksnr] = row["id"]
            linked.add(row["id"])
            if row["id"] not in known:
                known.add(row["id"])
                extra.append(row)
    df = contracts_frame(con, extra)
    lab: dict[str, int] = {}
    for saksnr, cid in by_case.items():
        lab[cid] = max(lab.get(cid, 0), int(cases[saksnr]["brudd"]))
    sub = df[df.id.isin(lab)].reset_index(drop=True)
    return df, sub, sub.id.map(lab).to_numpy().astype(int), linked


def _ci(y, p, n=1000, seed=0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            vals.append(roc_auc_score(y[i], p[i]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _res(name: str, y, p, aucs=None, note="") -> dict:
    lo, hi = _ci(y, p)
    auc = float(np.mean(aucs)) if aucs else float(roc_auc_score(y, p))
    return {"name": name, "auc": auc, "lo": lo, "hi": hi, "note": note}


def _with_missing(X: pd.DataFrame) -> pd.DataFrame:
    """Manglende verdier som egne features: hva kunngjøringen ikke oppgir kan i seg selv være et signal."""
    M = X.isna().astype(float).add_suffix("_mangler")
    return pd.concat([X, M.loc[:, M.std() > 0]], axis=1)


def _cv(proto, X, y, seed, extra=None, n_rep=5) -> tuple[np.ndarray, list[float]]:
    """Gjentatt stratifisert 5-fold. `extra` = (X, y) som alltid er med i treningen."""
    k = min(5, int(y.sum()), int(len(y) - y.sum()))
    rcv = RepeatedStratifiedKFold(n_splits=k, n_repeats=n_rep, random_state=seed)
    oof, aucs = np.zeros(len(y)), []
    p = np.zeros(len(y))
    for i, (tr, te) in enumerate(rcv.split(X, y)):
        Xtr, ytr = X.iloc[tr], y[tr]
        if extra is not None:
            Xtr, ytr = pd.concat([Xtr, extra[0]], ignore_index=True), np.concatenate([ytr, extra[1]])
        p[te] = clone(proto).fit(Xtr, ytr).predict_proba(X.iloc[te])[:, 1]
        if (i + 1) % k == 0:
            aucs.append(roc_auc_score(y, p))
            oof += p / n_rep
    return oof, aucs


def compare(con, link_cache: Path | None = None, seed: int = 42) -> dict:
    df, sub, y, linked = labelled(con, link_cache)
    out = {"n": int(len(y)), "brudd": int(y.sum()), "ikke_brudd": int(len(y) - y.sum()),
           "from_doffin": int(sub.id.isin(linked).sum()), "models": []}
    if min(out["brudd"], out["ikke_brudd"]) < 10:
        out["note"] = "For få merkede saker"
        return out
    X = feature_frame(sub)
    Xm = _with_missing(X)
    R = out["models"]

    base = ModelBundle(seed)
    for name in ("Random Forest", "Gradient Boosting", "Logistisk regresjon"):
        R.append(_res(f"Baseline {name} (syntetisk)", y, base.models[name].predict_proba(X)[:, 1], note="ingen KOFA-trening"))

    cri = np.array([sum(f["weight"] for f in rule_flags(r)) / CRI_MAX for r in sub.itertuples()])
    R.append(_res("CRI (røde flagg)", y, cri, note="regelbasert"))

    # Svak veiledning: flaggene merker alle umerkede kontrakter, modellen lærer av dem
    rest = df[~df.id.isin(sub.id)].sample(n=min(8000, len(df) - len(sub)), random_state=seed)
    weak = np.array([sum(f["weight"] for f in rule_flags(r)) / CRI_MAX for r in rest.itertuples()])
    yw = (weak >= np.quantile(weak, 0.8)).astype(int) if len(weak) else weak
    gb = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05, random_state=seed)
    if 0 < yw.sum() < len(yw):
        R.append(_res("Svak veiledning (GB på flagg)", y, gb.fit(feature_frame(rest), yw).predict_proba(X)[:, 1],
                      note="ingen KOFA-trening"))

    lr = Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler()),
                   ("clf", LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000))])
    p, a = _cv(lr, Xm, y, seed)
    R.append(_res("Regularisert LR (L2, C=0,1)", y, p, a, note="KOFA, 5×5 CV"))

    try:
        from catboost import CatBoostClassifier
        cb = CatBoostClassifier(depth=3, iterations=300, learning_rate=0.05, l2_leaf_reg=10, auto_class_weights="Balanced",
                                random_seed=seed, verbose=False, allow_writing_files=False)
        p, a = _cv(cb, Xm, y, seed)
        R.append(_res("CatBoost (dybde 3)", y, p, a, note="KOFA, 5×5 CV"))
    except ImportError:
        R.append({"name": "CatBoost", "note": "catboost ikke installert"})

    # PU: positive i treningsfolden mot umerkede kontrakter; testes på brudd vs ikke brudd
    if len(rest) < 50:
        return out
    unl = feature_frame(rest)
    unl_m = _with_missing(unl).reindex(columns=Xm.columns, fill_value=0.0)
    pu = Pipeline([("imp", SimpleImputer(strategy="median")),
                   ("clf", HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                                          class_weight="balanced", random_state=seed))])
    k = min(5, int(y.sum()), int(len(y) - y.sum()))
    rcv = RepeatedStratifiedKFold(n_splits=k, n_repeats=5, random_state=seed)
    pp, aucs, tmp = np.zeros(len(y)), [], np.zeros(len(y))
    for i, (tr, te) in enumerate(rcv.split(Xm, y)):
        pos = tr[y[tr] == 1]
        Xt = pd.concat([Xm.iloc[pos], unl_m], ignore_index=True)
        yt = np.concatenate([np.ones(len(pos)), np.zeros(len(unl_m))])
        tmp[te] = clone(pu).fit(Xt, yt).predict_proba(Xm.iloc[te])[:, 1]
        if (i + 1) % k == 0:
            aucs.append(roc_auc_score(y, tmp))
            pp += tmp / 5
    R.append(_res("PU-læring (brudd mot umerket)", y, pp, aucs, note="KOFA-brudd + umerket, 5×5 CV"))
    return out


def table(res: dict) -> str:
    lines = [f"Merkede saker: {res['n']} ({res['brudd']} brudd, {res['ikke_brudd']} ikke brudd; "
             f"{res['from_doffin']} via Doffin-kunngjøring)", "", "| Modell | AUC | 95 % KI | Trening |", "|---|---|---|---|"]
    for m in res["models"]:
        if "auc" in m:
            lines.append(f"| {m['name']} | {m['auc']:.3f} | {m['lo']:.2f}–{m['hi']:.2f} | {m['note']} |")
    if res.get("note"):
        lines += ["", res["note"]]
    return "\n".join(lines)


if __name__ == "__main__":
    from .config import settings
    from .db import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    res = compare(connect(settings.db_path), settings.data_dir / "cache" / "kofa_doffin.json", settings.seed)
    print(table(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=2, ensure_ascii=False))
