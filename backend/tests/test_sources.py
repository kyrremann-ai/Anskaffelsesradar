"""Tilleggskildene testet mot små fixtures (ekte formater, fiktivt innhold)."""
import json
from pathlib import Path

import pytest

from radar import db
from radar.enrich import ExtraIndex
from radar.ingest import brreg, kofa, owners, registers, regnskap, sanctions, valg

FX = Path(__file__).parent / "fixtures"
BUYER, S1, S2 = "944444444", "111111111", "222222222"


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.sqlite")
    base = {"source": "ted", "buyer_id": BUYER, "buyer": "Testby kommune", "cpv": "90910000", "method": "Åpen", "raw": None}
    db.upsert_contracts(c, [
        base | {"id": "C1", "title": "Renhold kommunale bygg", "pub_date": "2024-09-01", "award_value": 800000.0,
                "winner_ids": [S1], "winner_names": ["Rent og Pent AS"]},
        base | {"id": "C2", "title": "Vinduspuss", "pub_date": "2024-10-01", "award_value": 100000.0,
                "winner_ids": [S2], "winner_names": ["Skinnende AS"]},
    ])
    ents = [{"organisasjonsnummer": BUYER, "navn": "Testby kommune", "organisasjonsform": {"kode": "KOMM"},
             "forretningsadresse": {"kommunenummer": "0301"}},
            {"organisasjonsnummer": S1, "navn": "Rent og Pent AS", "organisasjonsform": {"kode": "AS"}, "stiftelsesdato": "2010-01-01"},
            {"organisasjonsnummer": S2, "navn": "Skinnende AS", "organisasjonsform": {"kode": "AS"}, "stiftelsesdato": "2010-01-01"}]
    roles = [{"orgnr": BUYER, "rolle": "LEDE", "person": "Kari Nordmann", "year": 1970},
             {"orgnr": S2, "rolle": "DAGL", "person": "Per Hansen", "year": 1965}]
    brreg.load_demo(c, ents, roles, "salt")
    return c


def test_regnskap_parse_picks_latest_year():
    r = regnskap.parse(S1, json.loads((FX / "regnskap.json").read_text()))
    assert r["aar"] == 2024 and r["driftsinntekter"] == 1200000 and r["egenkapital"] == -150000 and r["gjeld"] == 700000
    assert regnskap.parse(S1, []) is None


def test_aksjonaer_import_latin1_sums_classes_and_filters(con):
    n = owners.import_shareholders(con, FX / "aksjonaer.csv", 2024, wanted={S1, S2}, log=lambda *_: None)
    rows = {(r["orgnr"], r["owner_name"]): dict(r) for r in con.execute("SELECT * FROM owners")}
    assert n == 3 and ("333333333", "OLA") not in rows
    kari = rows[(S1, "KARI NORDMANN")]
    assert kari["share_pct"] == 70.0 and kari["owner_birth_year"] == 1970 and kari["owner_orgnr"] is None
    assert rows[(S2, "FJELLET HOLDING AS")]["owner_orgnr"] == "998877665"


def test_proff_owner_shapes():
    body = {"shareholders": [{"name": "Holding AS", "organisationNumber": "998 877 665", "share": "40,5"}, {"x": 1}]}
    assert owners.proff_owners(body) == [{"owner_name": "Holding AS", "owner_orgnr": "998877665", "owner_birth_year": None, "share_pct": 40.5}]


def test_register_xml_parse():
    rows = registers.parse((FX / "renhold.xml").read_bytes())
    assert [(r["orgnr"], r["status"]) for r in rows] == [(S1, "Godkjent"), (S2, "Ikke godkjent")]


def test_register_json_parse():
    raw = json.dumps([{"organisasjonsnummer": S1, "navn": "A AS", "godkjenningsstatus": "Godkjent",
                       "underenheter": [{"organisasjonsnummer": "987654321", "navn": "A avd"}]},
                      {"organisasjonsnummer": S2, "godkjenningsstatus": "Ikke godkjent"}]).encode()
    rows = registers.parse(raw)
    assert [(r["orgnr"], r["status"]) for r in rows] == [(S1, "Godkjent"), ("987654321", "Godkjent"), (S2, "Ikke godkjent")]


def test_bemanning_api_parse():
    rows = registers.parse((FX / "bemanning.json").read_bytes())
    assert [(r["orgnr"], r["status"]) for r in rows] == [(S1, "Godkjent"), ("333333333", "Godkjent"), (S2, "Ikke godkjent")]


def test_valg_parse():
    rows = valg.parse((FX / "kandidater.csv").read_text())
    assert len(rows) == 2 and rows[0] == {"navn_norm": "kari nordmann", "fodselsaar": 1970, "kommunenr": "0301",
                                          "kommune": "Oslo", "valgt": 1}
    assert rows[1]["kommunenr"] == "0301" and rows[1]["valgt"] == 0


def test_opensanctions_filters_norway_and_aliases():
    with open(FX / "opensanctions.csv", encoding="utf-8") as f:
        rows = sanctions.parse_lines(f)
    ids = {r["id"] for r in rows}
    assert "X-3" not in ids and {"Q1", "NK-2"} <= ids and any(i.startswith("NK-2#") for i in ids)
    org = next(r for r in rows if r["id"] == "NK-2")
    assert org["orgnr"] == S2 and org["is_sanction"] == 1


def test_kofa_parse_and_match(con):
    rows = kofa.parse((FX / "kofa.csv").read_text())
    assert len(rows) == 2 and rows[0]["brudd"] == 1 and rows[0]["ulovlig_direkte"] == 1 and rows[1]["innklaget_orgnr"] is None
    kofa.store(con, rows)
    assert kofa.match(con) == 1
    assert kofa.labels(con) == {"C1": 1}


def test_flags_from_all_sources(con):
    regnskap.store(con, [regnskap.parse(S1, json.loads((FX / "regnskap.json").read_text()))])
    owners.import_shareholders(con, FX / "aksjonaer.csv", 2024, wanted={S1, S2}, log=lambda *_: None)
    registers.store(con, "renhold", registers.parse((FX / "renhold.xml").read_bytes()))
    valg.store(con, valg.parse((FX / "kandidater.csv").read_text()), "test")
    with open(FX / "opensanctions.csv", encoding="utf-8") as f:
        sanctions.store(con, sanctions.parse_lines(f))
    kofa.store(con, kofa.parse((FX / "kofa.csv").read_text()))
    kofa.match(con)

    x = ExtraIndex(con, brreg.BrregIndex(con).ent)
    c1 = {"id": "C1", "buyer_id": BUYER, "winner_ids": [S1], "cpv": "90910000", "award_value": 800000.0}
    c2 = {"id": "C2", "buyer_id": BUYER, "winner_ids": [S2], "cpv": "90910000", "award_value": 100000.0}
    k1 = {f["key"] for f in x.flags(c1, {S1, S2})}
    k2 = {f["key"] for f in x.flags(c2, {S1, S2})}
    # S1: 800k av 1,2 MNOK omsetning, negativ EK, eier Kari sitter i styret hos kommunen og er valgt politiker
    assert {"rs_big_share", "rs_neg_equity", "own_shared_suppliers", "own_buyer_person", "pol_link", "kofa_brudd"} <= k1
    assert "reg_renhold" not in k1
    # S2: ikke godkjent i renholdsregisteret, sanksjonert selskap, daglig leder er PEP, ingen regnskap
    assert {"reg_renhold", "sanc_org", "pep_person", "rs_missing", "own_shared_suppliers"} <= k2
    card = x.supplier(S1)
    assert card["regnskap"]["aar"] == 2024 and card["owners"][0]["share_pct"] == 70.0 and "owner_birth_year" not in card["owners"][0]


def test_no_flags_without_data(con):
    x = ExtraIndex(con, brreg.BrregIndex(con).ent)
    assert x.flags({"id": "C1", "buyer_id": BUYER, "winner_ids": [S1], "cpv": "90910000", "award_value": 8e5}, {S1, S2}) == []


def test_einnsyn_parse_and_names():
    from radar.ingest import einnsyn
    assert einnsyn.core_name("Brøyt og Strø AS") == "Brøyt og Strø" and einnsyn.core_name("Statkraft ASA") == "Statkraft"
    items = [{"entity": "Journalpost", "offentligTittel": "Habilitetsvurdering - Rammeavtale", "journaldato": "2026-10-12",
              "journalposttype": "inngaaende_dokument", "externalId": "http://data.einnsyn.no/noark4/Journalpost--1",
              "saksmappe": {"externalId": "http://data.einnsyn.no/noark4/Saksmappe--971278374--3859--2026", "offentligTittel": "Rammeavtale"}},
             {"entity": "Moetemappe", "offentligTittel": "Ikke med"}]
    out = einnsyn.parse_items(items)
    assert len(out) == 1 and out[0]["case"] == "Rammeavtale" and out[0]["date"] == "2026-10-12"
    assert out[0]["url"].startswith("https://einnsyn.no/saksmappe?id=http%3A%2F%2Fdata.einnsyn.no") and "&jid=" in out[0]["url"]


# ------------------------------------------------------------ aksjonærregisteret: uttrekk, årganger og indirekte eierskap
HDR = "Orgnr;Selskap;Aksjeklasse;Navn aksjonær;Fødselsår/orgnr;Postnr/sted;Landkode;Antall aksjer;Antall aksjer selskap\n"
H1, H2 = "955555551", "955555552"


def _register(tmp_path, name, rows):
    f = tmp_path / name
    f.write_text("﻿" + HDR + "".join(";".join(map(str, r)) + "\n" for r in rows), encoding="utf-8")
    return f


def test_extract_compact_keeps_5pct_and_reimports(tmp_path, con):
    src = _register(tmp_path, "full.csv", [
        (S1, "RENT OG PENT AS", "A-aksjer", "KARI NORDMANN", 1970, "0150 OSLO", "NO", 30, 1000),
        (S1, "RENT OG PENT AS", "B-aksjer", "KARI NORDMANN", 1970, "0150 OSLO", "NO", 30, 1000),  # 6 % til sammen
        (S1, "RENT OG PENT AS", "Ordinære aksjer", "LITEN EIER", 1990, "0150 OSLO", "NO", 40, 1000),  # 4 %
        (S1, "RENT OG PENT AS", "Ordinære aksjer", 'O"BRIEN HOLDING AS', H1, "0150 OSLO", "NO", 900, 1000),
    ])
    out = tmp_path / "aksjonaer_2025.csv.gz"
    assert owners.extract(src, out, log=lambda *_: None) == 2
    owners.import_shareholders(con, out, 2025, wanted={S1}, log=lambda *_: None)
    rows = {r["owner_name"]: dict(r) for r in con.execute("SELECT * FROM owners")}
    assert set(rows) == {"KARI NORDMANN", 'O"BRIEN HOLDING AS'}
    assert rows["KARI NORDMANN"]["share_pct"] == 6.0 and rows["KARI NORDMANN"]["owner_birth_year"] == 1970
    assert rows['O"BRIEN HOLDING AS']["owner_orgnr"] == H1 and rows['O"BRIEN HOLDING AS']["year"] == 2025


def test_two_years_side_by_side_and_indirect_shared_owner(tmp_path, con):
    """S1 eies av H1, S2 av H2. Begge holdingselskapene eies 100 % av samme person: felles eier gjennom holdingselskap."""
    rows = [(S1, "RENT OG PENT AS", "Ordinære aksjer", "HOLDING EN AS", H1, "", "NO", 60, 100),
            (S2, "SKINNENDE AS", "Ordinære aksjer", "HOLDING TO AS", H2, "", "NO", 40, 100),
            (H1, "HOLDING EN AS", "Ordinære aksjer", "PER PERSEN", 1975, "", "NO", 100, 100),
            (H2, "HOLDING TO AS", "Ordinære aksjer", "PER PERSEN", 1975, "", "NO", 100, 100),
            ("977777777", "ANNET AS", "Ordinære aksjer", "PER PERSEN", 1975, "", "NO", 100, 100)]
    owners.import_shareholders(con, _register(tmp_path, "a2024.csv", rows), 2024, wanted={S1, S2}, log=lambda *_: None)
    owners.import_shareholders(con, _register(tmp_path, "a2025.csv", rows), 2025, wanted={S1, S2}, log=lambda *_: None)
    # Holdingselskapene følges oppover, urelaterte selskaper tas ikke med; to årganger ligger side om side
    assert {r[0] for r in con.execute("SELECT DISTINCT orgnr FROM owners")} == {S1, S2, H1, H2}
    assert {r[0] for r in con.execute("SELECT DISTINCT year FROM owners")} == {2024, 2025}
    # Ny import av samme år erstatter bare det året
    owners.import_shareholders(con, _register(tmp_path, "b2025.csv", rows), 2025, wanted={S1, S2}, log=lambda *_: None)
    assert con.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 8

    x = ExtraIndex(con, brreg.BrregIndex(con).ent)
    u = x.ultimate(S2)
    per = next(v for k, v in u.items() if k.startswith("per persen"))
    assert per["pct"] == 40.0 and per["via"] == [H2]
    c1 = {"id": "C1", "buyer_id": BUYER, "winner_ids": [S1], "cpv": "90910000", "award_value": 800000.0}
    f = next(f for f in x.flags(c1, {S1, S2}) if f["key"] == "own_shared_suppliers")
    assert "Skinnende AS" in f["why"] and "holdingselskap" in f["why"] and "2024 og 2025" in f["why"]
    card = x.supplier(S1)
    assert card["owners_indirect"][0]["share_pct"] == 60.0 and card["owners_indirect"][0]["via"] == ["HOLDING EN AS"]
    assert card["owners"][0]["years"] == [2024, 2025] and card["owners_note"] is None


def test_public_owner_not_shared_and_missing_is_data_quality(tmp_path, con):
    rows = [(S1, "RENT OG PENT AS", "Ordinære aksjer", "STATEN V/NÆRINGS- OG FISKERIDEPARTEMENTET", "912660680", "", "NO", 50, 100),
            (S2, "SKINNENDE AS", "Ordinære aksjer", "STATEN V/NÆRINGS- OG FISKERIDEPARTEMENTET", "912660680", "", "NO", 50, 100)]
    owners.import_shareholders(con, _register(tmp_path, "a2024.csv", rows), 2024, wanted={S1, S2}, log=lambda *_: None)
    x = ExtraIndex(con, brreg.BrregIndex(con).ent)
    c1 = {"id": "C1", "buyer_id": BUYER, "winner_ids": [S1], "cpv": "90910000", "award_value": 8e5}
    assert "own_shared_suppliers" not in {f["key"] for f in x.flags(c1, {S1, S2})}
    con.execute("DELETE FROM owners WHERE orgnr=?", (S2,))
    x = ExtraIndex(con, brreg.BrregIndex(con).ent)
    assert "Datakvalitet" in x.supplier(S2)["owners_note"] and "2024" in x.supplier(S2)["owners_note"]
