"""Røde flagg (CRI), registerflagg, prioritetsscore og klagerisiko.

Prioriteten er regelbasert. ModelBundle (tre modeller trent på den syntetiske generatoren
fra eksamensrapporten) brukes ikke lenger i appen fordi den ikke traff bedre enn tilfeldig
på ekte KOFA-saker. Den beholdes som baseline i kofa_eval og kofa_models.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import recall_score, roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import klagerisiko
from .db import set_meta, tx
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
    "repeat_pair_count": "Tidligere tildelinger, samme par", "prev_contract": "Tidligere tildeling til samme leverandør",
    "winner_market_share_pct": "Vinners markedsandel", "award_over_estimate_pct": "Tildelt mot estimat",
    "m_direct": "Direkte tildeling", "m_negotiated": "Forhandlet prosedyre", "m_restricted": "Begrenset prosedyre",
}
# Røde flagg med vekter fra Fazekas et al. (2013), som i rapporten.
FLAG_LABELS = {
    "single_bid": ("Ett registrert tilbud", 1.8), "direct": ("Direkte tildeling", 1.3),
    "repeat": ("Over tre tildelinger til samme leverandør", 1.5), "overrun": ("Tildelt >20 % over estimat", 1.2),
    "low_comp": ("To registrerte tilbud", 0.8), "prev": ("Tidligere tildeling til samme leverandør", 0.5),
    "accel": ("Akselerert prosedyre oppgitt", 0.7), "share": ("Leverandøren har over 30 % av kontraktsverdien i CPV-gruppen", 0.4),
    "short_deadline": ("Frist under lovens minstefrist", 1.0),
}
CRI_MAX = sum(w for _, w in FLAG_LABELS.values())
# Faste kontrollpunkter: slår ut uansett fordeling, og vises ved siden av rangeringen
NATIONAL_THRESHOLD = 1_300_000  # nasjonal terskelverdi, FOA § 5-1 (kr ekskl. mva)
MIN_DEADLINE = {"open": 30, "restricted": 30, "neg-w-call": 30, "comp-dial": 30, "innovation": 30}  # dager, FOA del III
MIN_DEADLINE_ACCELERATED = 15
# Register-tillegg i prioritetsscoren. Dokumentert på metodesiden.
BRREG_PER_WEIGHT, BRREG_CAP = 8.0, 20.0


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
    # Duplikater (samme tildeling i TED og Doffin, rettelser), endringer og avlysninger er ikke egne kontrakter.
    # Intensjonskunngjøringer tas med som egen kunngjøringstype, men teller ikke i historikk og rangering.
    df = pd.read_sql_query("""
        SELECT c.*, COALESCE(o.notice_type, 'tildeling') AS notice_type, COALESCE(o.procedure_id, c.id) AS procedure_id,
               o.value_note, o.cn_ref, o.cn_accelerated, t.procedure AS cn_procedure,
               (SELECT json_group_array(json_object('id', d.id, 'source', d.source, 'url', d.url, 'rule', od.dup_rule))
                  FROM observasjon od JOIN contracts d ON d.id = od.contract_id WHERE od.duplicate_of = c.id) AS also
        FROM contracts c LEFT JOIN observasjon o ON o.contract_id = c.id
        LEFT JOIN ted_competition t ON t.publication_number = o.cn_ref
        WHERE o.duplicate_of IS NULL AND COALESCE(o.notice_type, 'tildeling') IN ('tildeling', 'intensjon')""", con)
    for c in ("winner_ids", "winner_names", "also"):
        df[c] = df[c].map(lambda s: json.loads(s or "[]"))
    if extra:
        df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
        df["notice_type"] = df.notice_type.fillna("tildeling")
    if df.empty:
        return df
    df["winner"] = df.winner_ids.map(lambda w: w[0] if w else None)
    df = df.sort_values(["pub_date", "id"]).reset_index(drop=True)
    award = df.notice_type.eq("tildeling") & df.winner.notna()
    # Tidligere tildelinger for samme oppdragsgiver + leverandør (før denne kontrakten)
    df["repeat_pair_count"] = df[award].groupby(["buyer_id", "winner"]).cumcount().reindex(df.index)
    df["prev_contract"] = (df.repeat_pair_count > 0).astype(float).where(award)
    # Vinners andel av verdien (eller antall) i CPV-divisjonen
    df["cpv2"] = df.cpv.fillna("").str[:2]
    val = df.award_value.fillna(df.est_value).fillna(1.0).where(award)
    grp = val.groupby([df.cpv2, df.winner]).transform("sum")
    tot = val.groupby(df.cpv2).transform("sum")
    df["winner_market_share_pct"] = (grp / tot * 100).where(award)
    # Verdier som feilet verdikontrollen brukes ikke til overskridelse
    ok = df.est_value.gt(0) & df.award_value.notna() & df.get("value_note", pd.Series(None, index=df.index)).isna()
    df["award_over_estimate_pct"] = ((df.award_value / df.est_value - 1) * 100).where(ok)
    return df


def rule_flags(r) -> list[dict]:
    f = []
    nb = r.num_bidders
    direct = r.method == "Direkte tildeling"
    # Observasjon, ikke tolkning: bare når kunngjøringen oppgir ett tilbud. Direkte tildeling har eget flagg.
    if nb == 1:
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
    if short_deadline(r):
        f.append("short_deadline")
    out = []
    for k in f:
        label, w = FLAG_LABELS[k]
        if k == "overrun" and r.framework:
            label, w = label + " (rammeavtale, ofte maksverdi)", w * 0.5
        flag = {"key": k, "label": label, "weight": w, "source": "kunngjøring"}
        if k == "short_deadline":
            flag["why"] = f"{r.tender_duration_days:.0f} dager fra utsendelse av {r.cn_ref} til frist"
        out.append(flag)
    if getattr(r, "value_note", None) and isinstance(r.value_note, str):
        out.append({"key": "value_check", "label": "Usikker verdi", "weight": 0.0, "source": "datakvalitet", "why": r.value_note})
    return out


def short_deadline(r) -> bool:
    """Frist fra utsendelse av konkurransekunngjøringen er kortere enn minstefristen for prosedyren."""
    days, proc = getattr(r, "tender_duration_days", None), getattr(r, "cn_procedure", None)
    if days is None or pd.isna(days) or proc not in MIN_DEADLINE:
        return False
    return days < (MIN_DEADLINE_ACCELERATED if getattr(r, "cn_accelerated", 0) == 1 else MIN_DEADLINE[proc])


def fixed_checks(r) -> list[dict]:
    """Faste kontrollpunkter: observasjoner som slår ut uansett hvordan resten av datasettet ser ut."""
    out = []
    value = r.award_value if pd.notna(r.award_value) else r.est_value
    if r.notice_type == "tildeling" and r.num_bidders == 1:
        out.append({"key": "ett_tilbud", "label": "Ett registrert tilbud", "why": "Kunngjøringen oppgir ett mottatt tilbud"})
    if r.notice_type == "intensjon":
        out.append({"key": "intensjon", "label": "Intensjonskunngjøring om direkte tildeling",
                    "why": "Planlagt kontrakt uten konkurranse" + (f", oppgitt verdi {value:,.0f} kr".replace(",", " ") if pd.notna(value) else ", verdi ikke oppgitt")})
    elif r.method == "Direkte tildeling" and pd.notna(value) and value >= NATIONAL_THRESHOLD:
        out.append({"key": "direkte_over_terskel", "label": "Direkte tildeling over nasjonal terskelverdi",
                    "why": f"{value:,.0f} kr, terskel {NATIONAL_THRESHOLD:,.0f} kr".replace(",", " ")})
    if short_deadline(r):
        out.append({"key": "kort_frist", "label": "Frist under lovens minstefrist",
                    "why": f"{r.tender_duration_days:.0f} dager fra utsendelse av {r.cn_ref} til frist, minstefrist "
                           f"{MIN_DEADLINE_ACCELERATED if r.cn_accelerated == 1 else MIN_DEADLINE[r.cn_procedure]} dager"})
    return out


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average") * 100


def score_all(con, log=print) -> int:
    df = contracts_frame(con)
    if df.empty:
        return 0
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
    raw = df.cri + df.brreg_boost
    df["priority"] = (raw / (100 + BRREG_CAP) * 100).clip(0, 100)
    # Reglene gir mange like poengsummer; ved likhet rangeres større kontrakter først.
    # Rangeringen gjelder bare kontraktene. Intensjonskunngjøringer rangeres ikke; de vises som eget kontrollpunkt.
    award = df.notice_type.eq("tildeling")
    df["percentile"] = pct_rank((raw + pct_rank(df.award_value.fillna(df.est_value).fillna(0)) / 1e4)[award]).reindex(df.index)
    df["tier"] = np.select([~award, df.percentile >= 95, df.percentile >= 80], ["Intensjon", "Høy", "Forhøyet"], "Lav")
    df["data_coverage"] = df[["num_bidders", "est_value", "award_value", "contract_months"]].notna().sum(axis=1)
    # Klagerisiko (eksperimentell): persentil for sannsynlighet for KOFA-klage. Vises, men teller ikke i prioriteten.
    km = klagerisiko.load_model()
    df["klagerisiko"] = pct_rank(pd.Series(klagerisiko.predict(df, km), index=df.index)) if km else np.nan

    cols = ["priority", "tier", "percentile", "cri", "klagerisiko",
            "repeat_pair_count", "prev_contract", "winner_market_share_pct", "award_over_estimate_pct", "data_coverage"]
    rows = []
    for i, r in df.iterrows():
        vals = [None if (isinstance(r[c], float) and np.isnan(r[c])) else (r[c].item() if hasattr(r[c], "item") else r[c]) for c in cols]
        rows.append([r.id, *vals, json.dumps(flags[i], ensure_ascii=False), json.dumps(fixed_checks(r), ensure_ascii=False)])
    with tx(con):
        con.execute("DELETE FROM scores")
        con.executemany(f"INSERT INTO scores(contract_id, {','.join(cols)}, flags, regler) VALUES ({','.join('?' * (len(cols) + 3))})", rows)
        set_meta(con, "weights", {"brreg_per_weight": BRREG_PER_WEIGHT, "brreg_cap": BRREG_CAP})
    log(f"Scoret {len(rows)} kontrakter")
    return len(rows)

