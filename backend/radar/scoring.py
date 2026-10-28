"""Røde flagg (CRI), tre ML-modeller, avviksdeteksjon og samlet prioritetsscore.

Modellene trenes på den syntetiske generatoren fra eksamensrapporten, men bare på
feltene som faktisk finnes i TED. amendment_count og subcontractor_pct er derfor ute
av modellen (de inngår fortsatt i fasiten, og blir støy). Når KOFA-fasit finnes,
byttes `training_set()` ut med ekte merkede kontrakter.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    IsolationForest,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import recall_score, roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import klagerisiko
from .db import get_meta, set_meta, tx
from .enrich import ExtraIndex
from .ingest.brreg import BrregIndex
from .synthetic import training_set

FEATURES = [
    "est_value_mnok", "num_bidders", "is_single_bid", "tender_duration_days", "contract_months",
    "repeat_pair_count", "prev_contract", "winner_market_share_pct", "award_over_estimate_pct",
    "m_direct", "m_negotiated", "m_restricted",
]
FEATURE_LABELS = {
    "est_value_mnok": "Kontraktsverdi", "num_bidders": "Antall tilbud", "is_single_bid": "Ett tilbud",
    "tender_duration_days": "Tilbudsfrist", "contract_months": "Varighet",
    "repeat_pair_count": "Tidligere tildelinger, samme par", "prev_contract": "Tidligere kontrakt med vinner",
    "winner_market_share_pct": "Vinners markedsandel", "award_over_estimate_pct": "Tildelt mot estimat",
    "m_direct": "Direkte tildeling", "m_negotiated": "Forhandlet prosedyre", "m_restricted": "Begrenset prosedyre",
}
# Røde flagg med vekter fra Fazekas et al. (2013), som i rapporten.
FLAG_LABELS = {
    "single_bid": ("Ingen reell konkurranse", 1.8), "direct": ("Direkte tildeling", 1.3),
    "repeat": ("Gjentatte tildelinger (>3)", 1.5), "overrun": ("Tildelt >20 % over estimat", 1.2),
    "low_comp": ("Kun to tilbud", 0.8), "prev": ("Tidligere kontrakt med vinner", 0.5),
    "accel": ("Akselerert prosedyre", 0.7), "share": ("Markedsandel >30 %", 0.4),
}
CRI_MAX = sum(w for _, w in FLAG_LABELS.values())
# Vekting i prioritetsscoren. Dokumentert på metodesiden.
# «regler»: bare røde flagg og registerflagg. ML er under utvikling og vises ved siden av, men teller ikke.
# «ml»: den opprinnelige blandingen fra eksamensrapporten.
WEIGHTS = {"regler": (0.0, 1.0, 0.0), "ml": (0.60, 0.25, 0.15)}
BRREG_PER_WEIGHT, BRREG_CAP = 8.0, 20.0
# Minste antall KOFA-merkede kontrakter (per klasse) før de brukes i treningen
KOFA_MIN = 30


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Lager modellfeatures fra kontrakttabellen (eller treningsdata)."""
    X = pd.DataFrame(index=df.index)
    if "est_value_mnok" in df:
        X["est_value_mnok"] = df.est_value_mnok
    else:
        X["est_value_mnok"] = df.est_value.fillna(df.award_value) / 1e6
    X["num_bidders"] = df.num_bidders
    X["is_single_bid"] = (df.num_bidders == 1).astype(float).where(df.num_bidders.notna())
    for c in ("tender_duration_days", "contract_months", "repeat_pair_count", "prev_contract",
              "winner_market_share_pct", "award_over_estimate_pct"):
        X[c] = df[c]
    X["m_direct"] = (df.method == "Direkte tildeling").astype(float)
    X["m_negotiated"] = (df.method == "Forhandling").astype(float)
    X["m_restricted"] = (df.method == "Begrenset").astype(float)
    return X[FEATURES].astype(float)


def _models(seed: int) -> dict:
    prep = ColumnTransformer([("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), FEATURES)])
    return {
        "Logistisk regresjon": Pipeline([("prep", prep), ("clf", LogisticRegression(class_weight="balanced", max_iter=2000))]),
        "Random Forest": Pipeline([("imp", SimpleImputer(strategy="median")),
                                   ("clf", RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=5,
                                                                  class_weight="balanced", random_state=seed, n_jobs=-1))]),
        "Gradient Boosting": Pipeline([("clf", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                                                              class_weight="balanced", random_state=seed))]),
    }


class ModelBundle:
    def __init__(self, seed: int = 42, real: tuple[pd.DataFrame, np.ndarray] | None = None):
        train = training_set(seed=seed)
        X, y = feature_frame(train), train.is_suspicious.to_numpy()
        n_real = 0
        if real is not None:
            # Ekte KOFA-fasit legges til de syntetiske dataene (ikke erstatter dem ennå: for få saker)
            X = pd.concat([X, real[0]], ignore_index=True)
            y = np.concatenate([y, real[1]])
            n_real = len(real[1])
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=seed)
        cv = StratifiedKFold(5, shuffle=True, random_state=seed)
        self.models, metrics = {}, []
        for name, m in _models(seed).items():
            cv_auc = cross_val_score(m, Xtr, ytr, cv=cv, scoring="roc_auc")
            m.fit(Xtr, ytr)
            p = m.predict_proba(Xte)[:, 1]
            fpr, tpr, _ = roc_curve(yte, p)
            grid = np.linspace(0, 1, 40)
            metrics.append({"name": name, "auc": float(roc_auc_score(yte, p)), "cv_auc": float(cv_auc.mean()),
                            "cv_sd": float(cv_auc.std()), "recall": float(recall_score(yte, p >= 0.5)),
                            "roc": [[float(g), float(np.interp(g, fpr, tpr))] for g in grid]})
            self.models[name] = m.fit(X, y)  # endelig modell på alle data
        rf = self.models["Random Forest"].named_steps["clf"]
        self.importance = sorted(({"feature": FEATURE_LABELS[f], "value": float(v)} for f, v in zip(FEATURES, rf.feature_importances_)),
                                 key=lambda d: -d["value"])
        self.medians = X.median()
        self.info = {"train": {"n": len(y), "positives": int(y.sum()), "seed": seed, "synthetic": True, "kofa": n_real},
                     "metrics": metrics, "importance": self.importance, "features": [FEATURE_LABELS[f] for f in FEATURES],
                     "excluded": ["amendment_count (finnes ikke i TED)", "subcontractor_pct (finnes ikke i TED)"]}

    def predict(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        return {n: m.predict_proba(X)[:, 1] for n, m in self.models.items()}

    def drivers(self, x: pd.Series, top: int = 6) -> list[dict]:
        """Endring i ensemble-sannsynlighet (prosentpoeng) når én feature settes til medianen."""
        feats = [f for f in FEATURES if pd.notna(x[f]) and x[f] != self.medians[f]]
        batch = pd.DataFrame([x] * (len(feats) + 1)).reset_index(drop=True)
        for i, f in enumerate(feats, 1):
            batch.loc[i, f] = self.medians[f]
        p = np.mean(list(self.predict(batch).values()), axis=0)
        out = [{"feature": FEATURE_LABELS[f], "effect": round(float(p[0] - p[i]) * 100, 1)} for i, f in enumerate(feats, 1)]
        return sorted(out, key=lambda d: -abs(d["effect"]))[:top]


def contracts_frame(con, extra: list[dict] | None = None) -> pd.DataFrame:
    """Kontrakter med avledede felt. `extra` er rader som ikke ligger i databasen (f.eks. Doffin-treff for KOFA)."""
    df = pd.read_sql_query("SELECT * FROM contracts", con)
    for c in ("winner_ids", "winner_names"):
        df[c] = df[c].map(lambda s: json.loads(s or "[]"))
    if extra:
        df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    if df.empty:
        return df
    df["winner"] = df.winner_ids.map(lambda w: w[0] if w else None)
    df = df.sort_values(["pub_date", "id"]).reset_index(drop=True)
    # Tidligere tildelinger for samme oppdragsgiver + leverandør (før denne kontrakten)
    df["repeat_pair_count"] = df.groupby(["buyer_id", "winner"], dropna=False).cumcount().where(df.winner.notna())
    df["prev_contract"] = (df.repeat_pair_count > 0).astype(float).where(df.winner.notna())
    # Vinners andel av verdien (eller antall) i CPV-divisjonen
    df["cpv2"] = df.cpv.fillna("").str[:2]
    val = df.award_value.fillna(df.est_value).fillna(1.0)
    grp = val.groupby([df.cpv2, df.winner]).transform("sum")
    tot = val.groupby(df.cpv2).transform("sum")
    df["winner_market_share_pct"] = (grp / tot * 100).where(df.winner.notna())
    ok = df.est_value.gt(0) & df.award_value.notna()
    df["award_over_estimate_pct"] = ((df.award_value / df.est_value - 1) * 100).where(ok)
    return df


def kofa_training(con, labels: dict[str, int]) -> tuple[pd.DataFrame, np.ndarray] | None:
    """Features + fasit for kontrakter koblet til KOFA, når det finnes nok av begge klasser."""
    if sum(labels.values()) < KOFA_MIN or sum(1 for v in labels.values() if v == 0) < KOFA_MIN:
        return None
    df = contracts_frame(con)
    df = df[df.id.isin(labels)].reset_index(drop=True)
    return feature_frame(df), df.id.map(labels).to_numpy().astype(int)


def rule_flags(r) -> list[dict]:
    f = []
    nb = r.num_bidders
    direct = r.method == "Direkte tildeling"
    if nb == 1 or (direct and pd.isna(nb)):
        f.append("single_bid")
    if direct:
        f.append("direct")
    if (r.repeat_pair_count or 0) > 3:
        f.append("repeat")
    if pd.notna(r.award_over_estimate_pct) and r.award_over_estimate_pct > 20:
        f.append("overrun")
    if nb == 2:
        f.append("low_comp")
    if r.prev_contract == 1:
        f.append("prev")
    if r.accelerated:
        f.append("accel")
    if pd.notna(r.winner_market_share_pct) and r.winner_market_share_pct > 30:
        f.append("share")
    out = []
    for k in f:
        label, w = FLAG_LABELS[k]
        if k == "overrun" and r.framework:
            label, w = label + " (rammeavtale, ofte maksverdi)", w * 0.5
        out.append({"key": k, "label": label, "weight": w, "source": "kunngjøring"})
    return out


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average") * 100


def score_all(con, bundle: ModelBundle, log=print, mode: str = "regler") -> int:
    w_ml, w_cri, w_anom = WEIGHTS.get(mode, WEIGHTS["regler"])
    df = contracts_frame(con)
    if df.empty:
        return 0
    X = feature_frame(df)
    probs = bundle.predict(X)
    df["ml_lr"], df["ml_rf"], df["ml_gb"] = (probs[n] * 100 for n in ("Logistisk regresjon", "Random Forest", "Gradient Boosting"))
    df["ml_score"] = df[["ml_lr", "ml_rf", "ml_gb"]].mean(axis=1)

    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=bundle.info["train"]["seed"])
    Xi = X.fillna(bundle.medians)
    df["anomaly"] = pct_rank(pd.Series(-iso.fit(Xi).score_samples(Xi), index=df.index))

    brreg = BrregIndex(con)
    extra = ExtraIndex(con, brreg.ent)
    suppliers_by_buyer = df.explode("winner_ids").groupby("buyer_id").winner_ids.agg(lambda s: set(s.dropna())).to_dict()
    flags, cri, bboost = [], [], []
    for r in df.itertuples():
        fl = rule_flags(r)
        c = {"id": r.id, "buyer_id": r.buyer_id, "winner_ids": r.winner_ids, "pub_date": r.pub_date, "cpv": r.cpv,
             "award_value": r.award_value, "est_value": r.est_value}
        others = suppliers_by_buyer.get(r.buyer_id, set())
        bf = brreg.flags(c, others) + extra.flags(c, others)
        flags.append(fl + bf)
        cri.append(sum(f["weight"] for f in fl) / CRI_MAX * 100)
        bboost.append(min(BRREG_CAP, BRREG_PER_WEIGHT * sum(f["weight"] for f in bf)))
    df["cri"], df["brreg_boost"] = cri, bboost
    raw = w_ml * df.ml_score + w_cri * df.cri + w_anom * df.anomaly + df.brreg_boost
    df["priority"] = (raw / (100 + BRREG_CAP) * 100).clip(0, 100)
    # Reglene gir mange like poengsummer; ved likhet rangeres større kontrakter først
    df["percentile"] = pct_rank(raw + pct_rank(df.award_value.fillna(df.est_value).fillna(0)) / 1e4)
    df["tier"] = np.select([df.percentile >= 95, df.percentile >= 80], ["Høy", "Forhøyet"], "Lav")
    df["data_coverage"] = df[["num_bidders", "est_value", "award_value", "contract_months"]].notna().sum(axis=1)
    # Klagerisiko (eksperimentell): persentil for sannsynlighet for KOFA-klage. Vises, men teller ikke i prioriteten.
    km = klagerisiko.load_model()
    df["klagerisiko"] = pct_rank(pd.Series(klagerisiko.predict(df, km), index=df.index)) if km else np.nan

    cols = ["priority", "tier", "percentile", "ml_score", "ml_lr", "ml_rf", "ml_gb", "cri", "anomaly", "klagerisiko",
            "repeat_pair_count", "prev_contract", "winner_market_share_pct", "award_over_estimate_pct", "data_coverage"]
    rows = []
    for i, r in df.iterrows():
        vals = [None if (isinstance(r[c], float) and np.isnan(r[c])) else (r[c].item() if hasattr(r[c], "item") else r[c]) for c in cols]
        rows.append([r.id, *vals, json.dumps(flags[i], ensure_ascii=False)])
    with tx(con):
        con.execute("DELETE FROM scores")
        con.executemany(f"INSERT INTO scores(contract_id, {','.join(cols)}, flags) VALUES ({','.join('?' * (len(cols) + 2))})", rows)
        set_meta(con, "model", bundle.info)
        set_meta(con, "weights", {"mode": mode if mode in WEIGHTS else "regler", "ml": w_ml, "cri": w_cri, "anomaly": w_anom,
                                  "brreg_per_weight": BRREG_PER_WEIGHT, "brreg_cap": BRREG_CAP})
    log(f"Scoret {len(rows)} kontrakter")
    return len(rows)


def model_info(con) -> dict | None:
    return get_meta(con, "model")
