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


def test_valg_parse():
    rows = valg.parse((FX / "kandidater.csv").read_text())
    assert len(rows) == 2 and rows[0] == {"navn_norm": "kari nordmann", "fodselsaar": 1970, "kommunenr": "0301",
                                          "kommune": "Oslo", "parti": "Demopartiet", "valgt": 1}
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
