"""Måler modellene mot ekte KOFA-fasit og sammenligner med den syntetiske baselinen.

    python -m radar.kofa_eval [--json resultat.json]

For hver modell (LR, RF, GB) rapporteres AUC på de KOFA-koblede kontraktene:
- syntetisk: trent bare på syntetiske data (dagens modell), testet på KOFA
- kofa:      trent bare på KOFA, stratifisert k-fold
- begge:     syntetisk + KOFA-treningsfolder, testet på KOFA-testfolden
Syntetisk holdout-AUC (fra ModelBundle) vises som referanse. Merk at alle KOFA-saker er
påklagde kontrakter, så AUC måler brudd blant påklagde, ikke risiko blant alle kontrakter.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

from .ingest import kofa
from .scoring import ModelBundle, _models, contracts_frame, feature_frame
from .synthetic import training_set


def evaluate(con, seed: int = 42, min_per_class: int = 10) -> dict:
    labels = kofa.labels(con)
    df = contracts_frame(con)
    df = df[df.id.isin(labels)].reset_index(drop=True) if not df.empty else df
    y = df.id.map(labels).to_numpy().astype(int) if len(df) else np.array([], dtype=int)
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    out = {"kofa": {"n": len(y), "brudd": n1, "ikke_brudd": n0}, "models": []}
    base = ModelBundle(seed)
    syn_auc = {m["name"]: m["auc"] for m in base.info["metrics"]}
    if min(n1, n0) < min_per_class:
        out["note"] = f"For få koblede saker (trenger minst {min_per_class} per klasse)"
        out["models"] = [{"name": n, "synthetic_holdout": a} for n, a in syn_auc.items()]
        return out
    X = feature_frame(df)
    train = training_set(seed=seed)
    Xs, ys = feature_frame(train), train.is_suspicious.to_numpy()
    k = min(5, n1, n0)
    cv = StratifiedKFold(k, shuffle=True, random_state=seed)
    for name, proto in _models(seed).items():
        p_syn = base.models[name].predict_proba(X)[:, 1]
        p_kofa, p_both = np.zeros(len(y)), np.zeros(len(y))
        for tr, te in cv.split(X, y):
            p_kofa[te] = clone(proto).fit(X.iloc[tr], y[tr]).predict_proba(X.iloc[te])[:, 1]
            m = clone(proto).fit(pd.concat([Xs, X.iloc[tr]], ignore_index=True), np.concatenate([ys, y[tr]]))
            p_both[te] = m.predict_proba(X.iloc[te])[:, 1]
        out["models"].append({"name": name, "synthetic_holdout": syn_auc[name],
                              "synthetic_on_kofa": float(roc_auc_score(y, p_syn)),
                              "kofa_cv": float(roc_auc_score(y, p_kofa)),
                              "both_cv": float(roc_auc_score(y, p_both))})
    return out


def table(res: dict) -> str:
    k = res["kofa"]
    lines = [f"KOFA-koblede kontrakter: {k['n']} ({k['brudd']} brudd, {k['ikke_brudd']} ikke brudd)", "",
             "| Modell | Syntetisk holdout | Syntetisk → KOFA | Kun KOFA (CV) | Syntetisk + KOFA (CV) |",
             "|---|---|---|---|---|"]
    f = lambda v: "–" if v is None else f"{v:.3f}"  # noqa: E731
    for m in res["models"]:
        lines.append(f"| {m['name']} | {f(m.get('synthetic_holdout'))} | {f(m.get('synthetic_on_kofa'))} | "
                     f"{f(m.get('kofa_cv'))} | {f(m.get('both_cv'))} |")
    if res.get("note"):
        lines += ["", res["note"]]
    return "\n".join(lines)


if __name__ == "__main__":
    from .config import settings
    from .db import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    ap.add_argument("--min-per-class", type=int, default=10)
    a = ap.parse_args()
    res = evaluate(connect(settings.db_path), settings.seed, a.min_per_class)
    print(table(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=2, ensure_ascii=False))
