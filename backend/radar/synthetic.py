"""Syntetiske data.

1. `training_set`: samme generator som i eksamensrapporten (Foros 2026, data_generation.py):
   1 500 anbud, CRI-vekter fra Fazekas et al. (2013), støy σ=1,5, 5 % label-flipping, seed 42.
2. `demo_contracts` + `demo_brreg`: fiktive kontrakter, oppdragsgivere og leverandører for
   kjøring uten nettilgang. Alle navn er oppdiktet. Orgnr starter med 99 og er ikke ekte.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

METHODS_EN = ["Open", "Restricted", "Negotiated", "Direct Award"]
METHOD_NB = {"Open": "Åpen", "Restricted": "Begrenset", "Negotiated": "Forhandling", "Direct Award": "Direkte tildeling"}
SECTORS = ["Bygg og anlegg", "IT-tjenester", "Helse", "Transport", "Energi", "Utdanning", "Forsvar", "Miljø"]
REGIONS = ["Oslo", "Bergen", "Trondheim", "Stavanger", "Tromsø", "Kristiansand", "Drammen", "Bodø"]


def training_set(n: int = 1500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    method = rng.choice(METHODS_EN, size=n, p=[0.55, 0.2, 0.13, 0.12])
    num_bidders = rng.poisson(4, n) + 1
    is_single = (num_bidders == 1).astype(int)
    df = pd.DataFrame({
        "sector": rng.choice(SECTORS, n),
        "region": rng.choice(REGIONS, n),
        "method": method,
        "est_value_mnok": rng.lognormal(2.5, 1.2, n),
        "num_bidders": num_bidders,
        "is_single_bid": is_single,
        "tender_duration_days": np.clip(rng.normal(35, 12, n), 5, None),
        "contract_months": rng.integers(3, 61, n),
        "repeat_pair_count": rng.poisson(1.5, n),
        "prev_contract": rng.binomial(1, 0.3, n),
        "amendment_count": rng.poisson(0.8, n),
        "subcontractor_pct": rng.beta(2, 5, n) * 100,
        "winner_market_share_pct": rng.beta(2, 6, n) * 100,
        "award_over_estimate_pct": rng.normal(4, 9, n),
    })
    score = (
        df.is_single_bid * 1.8
        + (df.repeat_pair_count > 3) * 1.5
        + (df.award_over_estimate_pct > 20) * 1.2
        + (df.method == "Direct Award") * 1.3
        + (df.num_bidders <= 2) * 0.8
        + (df.amendment_count >= 3) * 0.9
        + df.prev_contract * 0.5
        + (df.tender_duration_days < 15) * 0.7
        + (df.winner_market_share_pct > 30) * 0.4
        + rng.normal(0, 1.5, n)
    )
    y = (score >= np.percentile(score, 80)).astype(int).to_numpy().copy()
    flip = rng.choice(n, size=int(0.05 * n), replace=False)
    y[flip] = 1 - y[flip]
    # Mistenkelige kontrakter får høyere overskridelse (som i rapporten).
    df.loc[y == 1, "award_over_estimate_pct"] += rng.normal(10, 4, int(y.sum()))
    # 15 manglende verdier, som i rapporten.
    df.loc[rng.choice(n, 15, replace=False), "tender_duration_days"] = np.nan
    df["method"] = df.method.map(METHOD_NB)
    df["is_suspicious"] = y
    return df


# ---------------------------------------------------------------- demodata

_BUYER_STEMS = ["Fjordvik", "Nordhavn", "Lyngdal Øst", "Solberg", "Elvetun", "Bjørkely", "Havbakken", "Sandnes Vest",
                "Furuheim", "Steinvik", "Øyfjell", "Breidablikk", "Kvitstein", "Granlia", "Måkeskjær", "Lindeberg"]
_SUPPLIER_STEMS = ["Fjellbygg", "Nordlys Data", "Kystrenhold", "Vestveg", "Bølge IT", "Granitt Entreprenør",
                   "Elvekraft", "Tindtrans", "Lysglimt Konsult", "Havørn Helse", "Brattli Maskin", "Isbre Rådgivning",
                   "Furu Facility", "Sølvberg Drift", "Polar Systemer", "Midnattsol Bemanning", "Fossekall Elektro",
                   "Steinur Anlegg", "Kobber & Co", "Snøfnugg Software"]
_CPV = {
    "Bygg og anlegg": ("45000000", "Bygg og anlegg"), "IT-tjenester": ("72000000", "Tjenester"),
    "Helse": ("85000000", "Tjenester"), "Transport": ("60000000", "Tjenester"), "Energi": ("09000000", "Varer"),
    "Utdanning": ("80000000", "Tjenester"), "Forsvar": ("35000000", "Varer"), "Miljø": ("90000000", "Tjenester"),
}
_TITLES = {
    "Bygg og anlegg": ["Rehabilitering av {x} skole", "Ny barnehage {x}", "Vann- og avløpsanlegg {x}", "Fortau og gang- og sykkelvei {x}"],
    "IT-tjenester": ["Driftsavtale IT {x}", "Sak- og arkivsystem", "Rammeavtale konsulenttjenester IT", "Skylagring og backup"],
    "Helse": ["Vikartjenester sykepleie", "Medisinsk forbruksmateriell", "Legevakt {x}", "Hjemmetjenester nattevakt"],
    "Transport": ["Skoleskyss {x}", "Pasienttransport", "Drosjetjenester", "Kjøp av elbusser"],
    "Energi": ["Strøm og fjernvarme", "Solcelleanlegg {x} rådhus", "Fyringsolje og drivstoff"],
    "Utdanning": ["Læremidler grunnskole", "Kurs og kompetanseheving", "Elev-PC-er {x}"],
    "Forsvar": ["Beredskapsutstyr", "Sambandsutstyr brann {x}"],
    "Miljø": ["Renovasjon {x}", "Renhold kommunale bygg", "Snøbrøyting {x}", "Slamtømming"],
}


def _orgnr(prefix: str, i: int) -> str:
    return "99" + str(int(hashlib.md5(f"{prefix}{i}".encode()).hexdigest(), 16))[:7]


_SUFFIX = ["", " Norge", " Vest", " Nord", " Gruppen", " Service", " Partner"]
# Sektor -> NACE-koder som passer (brukes for at demo-leverandører stort sett «passer» kjøpet)
_SECTOR_NACE = {"Bygg og anlegg": "41.200", "IT-tjenester": "62.010", "Helse": "86.909", "Transport": "49.390",
                "Energi": "35.140", "Utdanning": "85.599", "Forsvar": "46.690", "Miljø": "38.110"}


def _demo_actors(rng):
    buyers = []
    for i, s in enumerate(_BUYER_STEMS + [f"{a}{b}" for a in ("Øst", "Vest", "Nord", "Sør") for b in ("dal", "vik", "fjord", "øy", "heim", "nes")]):
        buyers.append({"id": _orgnr("b", i), "name": f"{s} kommune" if i % 5 else f"{s} kommunale eiendom KF",
                       "region": REGIONS[i % len(REGIONS)]})
    suppliers = []
    for i in range(140):
        stem, suf = _SUPPLIER_STEMS[i % len(_SUPPLIER_STEMS)], _SUFFIX[i // len(_SUPPLIER_STEMS) % len(_SUFFIX)]
        suppliers.append({"id": _orgnr("s", i), "name": f"{stem}{suf} AS", "sector": SECTORS[i % len(SECTORS)],
                          "size": str(rng.choice(["Liten", "Mellomstor", "Stor"], p=[0.5, 0.35, 0.15]))})
    return buyers, suppliers


def demo_contracts(n: int = 1800, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    buyers, suppliers = _demo_actors(rng)
    by_sector = {s: [i for i, x in enumerate(suppliers) if x["sector"] == s] for s in SECTORS}
    start = date(2025, 1, 3)
    rows = []
    for i in range(n):
        b = buyers[int(rng.integers(len(buyers)))]
        sector = SECTORS[int(rng.integers(len(SECTORS)))]
        method = rng.choice(METHODS_EN, p=[0.6, 0.15, 0.12, 0.13])
        # Noen få oppdragsgivere har en «husleverandør» de bruker mye
        pool = by_sector[sector]
        if int(b["id"]) % 6 == 0 and rng.random() < 0.5:
            si = pool[int(b["id"]) % len(pool)]
        else:
            si = pool[int(rng.integers(len(pool)))] if rng.random() < 0.9 else int(rng.integers(len(suppliers)))
        s = suppliers[si]
        bidders = 1 if method == "Direct Award" else int(rng.poisson(3.5) + 1)
        has_bidders = rng.random() > (0.5 if method == "Direct Award" else 0.1)
        est = float(rng.lognormal(15.2, 1.1))
        overrun = rng.normal(3, 12) + (15 if bidders == 1 and rng.random() < 0.3 else 0)
        has_values = rng.random() < 0.4
        framework = rng.random() < 0.3
        pub = start + timedelta(days=int(rng.integers(0, 662)))
        title = rng.choice(_TITLES[sector]).format(x=b["name"].split()[0])
        cpv, nature = _CPV[sector]
        rows.append({
            "id": f"DEMO-{i:05d}", "source": "demo", "url": None, "title": title, "pub_date": pub.isoformat(),
            "buyer_id": b["id"], "buyer": b["name"], "region": b["region"], "sector": sector,
            "cpv": cpv, "nature": nature, "method": METHOD_NB[method],
            "accelerated": int(rng.random() < 0.05),
            "justification": "Hastesak (FOA § 13-3 e)" if method == "Direct Award" and rng.random() < 0.4 else None,
            "framework": int(framework), "num_bidders": bidders if has_bidders else None,
            "est_value": est if has_values else None,
            "award_value": est * (1 + overrun / 100) if has_values else (est if rng.random() < 0.5 else None),
            "tender_duration_days": None if method == "Direct Award" else float(max(6, rng.normal(32, 10))),
            "contract_months": float(rng.integers(6, 49)), "winner_ids": [s["id"]], "winner_names": [s["name"]],
            "winner_size": s["size"], "raw": None,
        })
    return rows


def demo_brreg(contracts: list[dict], seed: int = 11) -> tuple[list[dict], list[dict]]:
    """Fiktive Brreg-oppføringer og roller for demodataene, med noen bevisste røde flagg."""
    rng = np.random.default_rng(seed)
    _, suppliers = _demo_actors(np.random.default_rng(7))
    sector_of = {s["id"]: s["sector"] for s in suppliers}
    orgs: dict[str, dict] = {}
    for c in contracts:
        orgs.setdefault(c["buyer_id"], {"navn": c["buyer"], "buyer": True})
        for oid, name in zip(c["winner_ids"], c["winner_names"]):
            orgs.setdefault(oid, {"navn": name, "buyer": False})
    entities, roles = [], []
    pid = iter(range(10_000))
    shared = [f"Person {next(pid)}" for _ in range(3)]
    for k, (orgnr, o) in enumerate(sorted(orgs.items())):
        if o["buyer"]:
            orgform = "KF" if "KF" in o["navn"] else "KOMM"
            entities.append({"organisasjonsnummer": orgnr, "navn": o["navn"], "organisasjonsform": {"kode": orgform},
                             "naeringskode1": {"kode": "84.110", "beskrivelse": "Generell offentlig administrasjon"},
                             "registrertIMvaregisteret": True,
                             "forretningsadresse": {"adresse": ["Rådhusplassen 1"], "postnummer": f"{1000 + k}"}})
        else:
            young, odd = k % 23 == 0, k % 17 == 0
            nace = "70.220" if odd else _SECTOR_NACE[sector_of.get(orgnr, "Bygg og anlegg")]
            entities.append({"organisasjonsnummer": orgnr, "navn": o["navn"], "organisasjonsform": {"kode": "AS"},
                             "stiftelsesdato": "2025-06-01" if young else f"{int(rng.integers(1985, 2020))}-03-01",
                             "naeringskode1": {"kode": nace, "beskrivelse": "Bedriftsrådgivning" if odd else ""},
                             "antallAnsatte": 0 if young else int(rng.integers(3, 400)),
                             "registrertIMvaregisteret": not young, "konkurs": k % 41 == 0,
                             "forretningsadresse": {"adresse": ["Industriveien 5" if k % 29 == 0 else f"Storgata {k}"],
                                                    "postnummer": "0150"}})
        board = [f"Person {next(pid)}" for _ in range(3)]
        # Noen få personer sitter i styret både hos en oppdragsgiver og hos leverandører
        if (o["buyer"] and k % 11 == 0) or (not o["buyer"] and k % 19 == 0):
            board[1] = shared[k % len(shared)]
        for j, p in enumerate(board):
            roles.append({"orgnr": orgnr, "rolle": ["LEDE", "MEDL", "DAGL"][j], "person": p})
    return entities, roles
