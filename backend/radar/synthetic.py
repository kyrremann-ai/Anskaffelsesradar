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
        # Renhold og vikartjenester krever godkjenning hos Arbeidstilsynet
        cpv = "90910000" if title.startswith("Renhold") else "79620000" if title.startswith("Vikar") else cpv
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
                             "forretningsadresse": {"adresse": ["Rådhusplassen 1"], "postnummer": f"{1000 + k}",
                                                    "kommunenummer": f"{3000 + k}"}})
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
            roles.append({"orgnr": orgnr, "rolle": ["LEDE", "MEDL", "DAGL"][j], "person": p, "year": _year(p)})
    return entities, roles


def _year(person: str) -> int:
    return 1950 + int(person.split()[-1]) % 50


def demo_extra(contracts: list[dict], entities: list[dict], roles: list[dict], seed: int = 13) -> dict[str, list[dict]]:
    """Fiktive data for tilleggskildene: regnskap, eiere, registre, kandidatlister, sanksjoner/PEP og KOFA."""
    rng = np.random.default_rng(seed)
    buyers = {c["buyer_id"] for c in contracts}
    sup = [e for e in entities if e["organisasjonsnummer"] not in buyers]
    ent = {e["organisasjonsnummer"]: e for e in entities}
    people = {}
    for r in roles:
        people.setdefault(r["orgnr"], []).append(r)
    buyer_people = [r for r in roles if r["orgnr"] in buyers]
    out: dict[str, list[dict]] = {k: [] for k in ("regnskap", "owners", "registers", "politicians", "sanctions", "kofa")}

    for k, e in enumerate(sup):
        o = e["organisasjonsnummer"]
        if e.get("stiftelsesdato", "") >= "2025" or k % 37 == 0:
            continue  # nyetablerte og noen få andre har ikke levert regnskap
        rev = 3e6 if k % 29 == 0 else max(1.5e7, float(rng.lognormal(17.5, 1.0)))
        eq = -float(rng.uniform(2e5, 2e6)) if k % 31 == 0 else rev * float(rng.uniform(0.05, 0.4))
        out["regnskap"].append({"orgnr": o, "aar": 2024, "driftsinntekter": rev, "driftsresultat": rev * 0.06,
                                "aarsresultat": rev * 0.04, "egenkapital": eq, "gjeld": rev * 0.3, "eiendeler": rev * 0.3 + eq})

    holding = "998877665"
    for k, e in enumerate(sup):
        o = e["organisasjonsnummer"]
        dagl = next((r for r in people.get(o, []) if r["rolle"] == "DAGL"), None)
        rows = []
        if dagl:
            rows.append({"owner_name": dagl["person"], "owner_orgnr": None, "owner_birth_year": dagl["year"], "share_pct": 60.0})
        if k % 35 == 0:  # samme holdingselskap eier flere konkurrenter
            rows.append({"owner_name": "Fjellet Holding AS", "owner_orgnr": holding, "owner_birth_year": None, "share_pct": 40.0})
        elif k % 27 == 0 and buyer_people:  # eier sitter i styret hos en oppdragsgiver
            p = buyer_people[k % len(buyer_people)]
            rows.append({"owner_name": p["person"], "owner_orgnr": None, "owner_birth_year": p["year"], "share_pct": 40.0})
        else:
            rows.append({"owner_name": f"Investor {k}", "owner_orgnr": None, "owner_birth_year": 1960 + k % 30, "share_pct": 40.0})
        out["owners"] += [{"orgnr": o, "source": "demo", "year": 2024, **r} for r in rows]

    for reg, prefix in (("renhold", "9091"), ("bemanning", "7962")):
        ids = sorted({w for c in contracts if str(c["cpv"]).startswith(prefix) for w in c["winner_ids"]})
        for k, o in enumerate(ids):
            if k % 7 == 3:
                continue  # mangler i registeret
            out["registers"].append({"register": reg, "orgnr": o, "navn": ent[o]["navn"],
                                     "status": "Ikke godkjent" if k % 7 == 5 else "Godkjent"})
        # Registrene inneholder også mange andre virksomheter
        out["registers"] += [{"register": reg, "orgnr": f"91{1 if reg == 'renhold' else 2}{i:06d}", "navn": f"Firma {i} AS", "status": "Godkjent"}
                             for i in range(50)]

    by_sup_buyers: dict[str, set[str]] = {}
    for c in contracts:
        for w in c["winner_ids"]:
            by_sup_buyers.setdefault(w, set()).add(c["buyer_id"])
    for k, e in enumerate(sup):
        if k % 21 != 0:
            continue
        o = e["organisasjonsnummer"]
        p = people[o][0]
        b = sorted(by_sup_buyers.get(o, buyers))[0]
        kom = ent[b]["forretningsadresse"].get("kommunenummer")
        out["politicians"].append({"navn": p["person"], "fodselsaar": p["year"], "kommunenr": kom,
                                   "kommune": ent[b]["navn"].split()[0], "valgt": int(k % 2 == 0)})
    out["politicians"] += [{"navn": f"Kandidat {i}", "fodselsaar": 1950 + i % 50, "kommunenr": f"{3000 + i % 40}",
                            "kommune": "", "valgt": int(i % 3 == 0)} for i in range(300)]

    if sup:
        out["sanctions"].append({"id": "demo-org-1", "schema": "Company", "name": sup[5]["navn"], "birth_year": None,
                                 "orgnr": sup[5]["organisasjonsnummer"], "is_sanction": 0, "datasets": "Demo: utestengte leverandører"})
        p = people[sup[9]["organisasjonsnummer"]][0]
        out["sanctions"].append({"id": "demo-pep-1", "schema": "Person", "name": p["person"], "birth_year": p["year"],
                                 "orgnr": None, "is_sanction": 0, "datasets": "Demo: politisk eksponerte personer"})

    # KOFA: et utvalg kontrakter, med brudd oftere ved direkte tildeling og ett tilbud
    for i, c in enumerate(contracts):
        if i % 12:
            continue
        risky = c["method"] == "Direkte tildeling" or c["num_bidders"] == 1
        brudd = rng.random() < (0.7 if risky else 0.15)
        d = date.fromisoformat(c["pub_date"]) + timedelta(days=150)
        out["kofa"].append({"saksnr": f"DEMO-{d.year}/{i:04d}", "dato": d.isoformat(), "innklaget": c["buyer"],
                            "innklaget_orgnr": c["buyer_id"], "leverandor": c["winner_names"][0], "tittel": c["title"],
                            "brudd": int(brudd), "ulovlig_direkte": int(brudd and c["method"] == "Direkte tildeling"), "url": None})
    return out
