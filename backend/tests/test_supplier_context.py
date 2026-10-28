"""Testplan: alternative forklaringer for leverandørflaggene.

Flaggene «kontraktsverdi over halve årsomsetningen», «ingen ansatte registrert», «samme adresse» og
«felles rolleinnehaver/eier med annen leverandør» er undersøkelsespunkter. Hvert tilfelle under er en
alternativ forklaring som systemet skal håndtere, med forventet utfall og hvilke data som brukes.
Planen er også beskrevet i docs/testplan-leverandorflagg.md.

Data: fiktive enheter i Enhetsregisteret-format, regnskap per år (regnskap_aar), aksjonærer (owners),
Brregs endringslogg (brreg_changes/brreg_changes_fetched) og fusjon/fisjon-fil (brreg_successors).

Utfall: «flagg» = flagget står med full vekt, «halv» = vekten halveres, «borte» = flagget faller bort,
«merket» = flagget står og sier hvilke data som mangler (data_note).
"""
import json

import pytest

from radar import db
from radar.enrich import ExtraIndex
from radar.ingest import brreg, brreg_changes, regnskap
from radar.ingest.brreg import BrregIndex
from radar.supplier_context import SupplierContext

BUYER, A, B, C, PARENT = "944444444", "911111111", "922222222", "933333333", "955555555"
ADDR = {"adresse": ["Storgata 1"], "postnummer": "0155", "poststed": "OSLO", "kommunenummer": "0301"}


def _ent(orgnr, navn, **kw):
    return {"organisasjonsnummer": orgnr, "navn": navn, "organisasjonsform": {"kode": kw.pop("form", "AS")},
            "stiftelsesdato": "2010-01-01", "registrertIMvaregisteret": True, **kw}


@pytest.fixture()
def con(tmp_path):
    return db.connect(tmp_path / "t.sqlite")


def setup(con, ents, roles=(), years=(), owners=(), changes=None, successors=()):
    """Legger inn testdata og gir SupplierContext + flaggene før kontekst."""
    brreg.load_demo(con, [_ent(BUYER, "Testby kommune", form="KOMM"), *ents], list(roles), "salt")
    for e in ents:  # load_demo setter kind=enhet; slettede/underenheter overstyres her
        if e.get("kind"):
            con.execute("UPDATE brreg_entities SET data=? WHERE orgnr=?", (json.dumps(e), e["organisasjonsnummer"]))
    regnskap.store(con, [], [{"orgnr": o, "aar": y, "type": t, "driftsinntekter": v, "egenkapital": 1} for o, y, t, v in years])
    con.executemany("INSERT INTO owners(orgnr, owner_name, owner_orgnr, owner_birth_year, share_pct, source, year) VALUES (?,?,?,?,?,?,?)",
                    [(o, "Eier", p, None, pct, "test", yr) for o, p, pct, yr in owners])
    for o, (since, rows) in (changes or {}).items():
        brreg_changes.store(con, o, since, [{"orgnr": o, "at": at, "felt": f, "verdi": v} for at, f, v in rows])
    con.executemany("INSERT INTO brreg_successors VALUES (?,?,?,?,?)", [(*s, "test") for s in successors])
    con.commit()
    b = BrregIndex(con)
    x = ExtraIndex(con, b.ent)
    return SupplierContext(con, b, x), b, x


def contract(winners, value=4_000_000, months=None, framework=False, pub="2025-06-01"):
    return {"id": "K1", "buyer_id": BUYER, "winner_ids": winners, "pub_date": pub, "cpv": "79000000",
            "award_value": value, "est_value": None, "contract_months": months, "framework": framework}


def run(ctx, b, x, c, others=None):
    others = set(others or c["winner_ids"])
    return {f["key"]: f for f in ctx.apply(c, b.flags(c, others) + x.flags(c, others), others)}


def statuses(f):
    return {ch["q"]: ch["status"] for ch in f["checks"]}


# ---------------------------------------------------------------- 1. Flerårig verdi mot ett års omsetning

def test_T1_multiyear_contract_annualised_below_half_drops_flag(con):
    """4 MNOK over 48 mnd mot 3 MNOK omsetning: 133 % rått, 33 % per år -> borte."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)], years=[(A, 2025, "SELSKAP", 3e6)])
    assert "rs_big_share" not in run(ctx, b, x, contract([A], months=48))


def test_T2_multiyear_still_above_half_keeps_flag_with_annual_value(con):
    """8 MNOK over 24 mnd mot 3 MNOK: 4 MNOK per år = 133 % -> flagg, «Årlig verdi» i forklaringen."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)], years=[(A, 2025, "SELSKAP", 3e6)])
    f = run(ctx, b, x, contract([A], value=8e6, months=24))["rs_big_share"]
    assert f["why"].startswith("Årlig verdi 4,0 MNOK er 133 %")
    assert statuses(f)["Er kontraktsverdien flerårig, mens omsetningen gjelder ett år?"] == "justert"


def test_T3_unknown_duration_is_stated_not_guessed(con):
    """Varighet mangler -> flagg står med full vekt, data_note sier at varighet mangler."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)], years=[(A, 2025, "SELSKAP", 3e6)], owners=[(A, None, 100, 2025)])
    f = run(ctx, b, x, contract([A]))["rs_big_share"]
    assert f["weight"] == 1.0 and "Varighet er ikke oppgitt" in f["data_note"]


# ---------------------------------------------------------------- 2. Rammeavtale: hele rammen eller én andel

def test_T4_framework_value_split_between_suppliers(con):
    """Rammeavtale 9 MNOK med tre leverandører: 3 MNOK hver. Mot 5 MNOK = 60 % -> flagg; mot 7 MNOK = 43 % -> borte."""
    ents = [_ent(o, n, antallAnsatte=5) for o, n in ((A, "Alfa AS"), (B, "Beta AS"), (C, "Gamma AS"))]
    ctx, b, x = setup(con, ents, years=[(A, 2025, "SELSKAP", 5e6), (B, 2025, "SELSKAP", 7e6), (C, 2025, "SELSKAP", 7e6)])
    f = run(ctx, b, x, contract([A, B, C], value=9e6, months=12, framework=True))["rs_big_share"]
    assert "60 %" in f["why"] and "3 leverandører" in f["checks"][0]["a"]
    con.execute("UPDATE regnskap_aar SET driftsinntekter=7e6 WHERE orgnr=?", (A,))
    ctx = SupplierContext(con, b, x)
    assert "rs_big_share" not in run(ctx, b, x, contract([A, B, C], value=9e6, months=12, framework=True))


def test_T5_single_supplier_framework_is_a_ceiling_half_weight(con):
    """Rammeavtale med én leverandør: verdien er ofte en øvre ramme -> halv vekt."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)], years=[(A, 2025, "SELSKAP", 3e6)])
    f = run(ctx, b, x, contract([A], value=4e6, months=12, framework=True))["rs_big_share"]
    assert f["weight"] == 0.5


# ---------------------------------------------------------------- regnskapsår og underenhet

def test_T6_turnover_from_award_year_and_gap_is_marked(con):
    """Omsetning hentes fra tildelingsåret (2023), ikke siste år. Bare 2021-regnskap -> merket."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)],
                      years=[(A, 2023, "SELSKAP", 3e6), (A, 2025, "SELSKAP", 30e6)])
    f = run(ctx, b, x, contract([A], months=12, pub="2023-05-01"))["rs_big_share"]
    assert "driftsinntektene 2023" in f["why"]
    con.execute("DELETE FROM regnskap_aar")
    con.execute("INSERT INTO regnskap_aar VALUES (?, 2021, 'SELSKAP', 3e6, 1)", (A,))
    f = run(SupplierContext(con, b, x), b, x, contract([A], months=12, pub="2024-05-01"))["rs_big_share"]
    assert "Nærmeste regnskap er fra 2021" in f["data_note"]


def test_T7_sub_unit_uses_main_unit_accounts(con):
    """Underenhet (avdeling) har ikke eget regnskap: hovedenhetens omsetning brukes."""
    sub = _ent(B, "Alfa AS avd. Bergen", antallAnsatte=5, kind="underenhet", overordnetEnhet=A)
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5), sub], years=[(A, 2025, "SELSKAP", 20e6)])
    c = contract([B], value=12e6, months=12)
    f = ctx.big_share(c, {"key": "rs_big_share", "label": "x", "weight": 1.0, "source": "regnskap"}, {B})
    assert f and "60 %" in f["why"] and any("Underenhet" in ch["a"] for ch in f["checks"])


# ---------------------------------------------------------------- 3. Konsern

def test_T8_group_company_halves_weight_and_unknown_without_owner_data(con):
    """Datterselskap (≥ 50 % eid av selskap) -> halv vekt. Uten aksjonærdata -> merket «ukjent»."""
    ents = [_ent(A, "Alfa AS", antallAnsatte=5), _ent(PARENT, "Alfa Holding AS")]
    ctx, b, x = setup(con, ents, years=[(A, 2025, "SELSKAP", 3e6)])
    f = run(ctx, b, x, contract([A], months=12))["rs_big_share"]
    assert f["weight"] == 1.0 and "aksjonærregisteret er ikke lastet" in f["data_note"]
    con.execute("INSERT INTO owners(orgnr, owner_name, owner_orgnr, share_pct, source, year) VALUES (?,?,?,?,?,?)",
                (A, "Alfa Holding AS", PARENT, 100, "test", 2025))
    f = run(SupplierContext(con, b, x), b, x, contract([A], months=12))["rs_big_share"]
    assert f["weight"] == 0.5 and "Datterselskap av Alfa Holding AS" in f["checks"][-1]["a"]


def test_T8b_parent_with_group_accounts_shows_group_share(con):
    """Morselskap med konsernregnskap: konsernets omsetning vises ved siden av selskapets."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)],
                      years=[(A, 2025, "SELSKAP", 3e6), (A, 2025, "KONSERN", 40e6)])
    f = run(ctx, b, x, contract([A], months=12))["rs_big_share"]
    assert f["weight"] == 0.5 and "konsernets driftsinntekter 2025: 40,0 MNOK, 10 %" in f["checks"][-1]["a"]


def test_T9_no_staff_in_group_or_unreported(con):
    """Ingen ansatte: konsernselskap -> halv vekt; ingen opplysning -> merket, ikke tolket som 0;
    registrerte ansatte uten publisert antall (Brreg skjuler tallet) -> borte."""
    ents = [_ent(A, "Alfa AS", antallAnsatte=0), _ent(B, "Beta AS"), _ent(C, "Gamma AS", harRegistrertAntallAnsatte=True)]
    ctx, b, x = setup(con, ents, owners=[(A, PARENT, 100, 2025)])
    f = run(ctx, b, x, contract([A], value=5e6))["brreg_no_staff"]
    assert f["weight"] == 0.3 and f["why"].startswith("0 ansatte")
    f = run(ctx, b, x, contract([B], value=5e6))["brreg_no_staff"]
    assert "verken antall" in f["data_note"] and f["why"].startswith("Ukjent antall")
    assert "brreg_no_staff" in {f["key"] for f in b.flags(contract([C], value=5e6), {C})}  # dagens regel slår til
    assert "brreg_no_staff" not in run(ctx, b, x, contract([C], value=5e6))


# ---------------------------------------------------------------- 4. På tildelingstidspunktet eller bare i dag

def test_T10_staff_at_award_from_change_log(con):
    """0 ansatte i dag, men 12 ved tildeling (endringslogg) -> borte. Endret etter tildeling uten tidligere tall -> merket."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=0)],
                      changes={A: ("2024-01-01", [("2024-03-01", "ansatte", "12"), ("2026-02-01", "ansatte", "0")])})
    assert "brreg_no_staff" not in run(ctx, b, x, contract([A], value=5e6))
    con.execute("DELETE FROM brreg_changes WHERE at='2024-03-01'")
    f = run(SupplierContext(con, b, x), b, x, contract([A], value=5e6))["brreg_no_staff"]
    assert "tallet ved tildeling er ukjent" in f["data_note"]


def test_T11_without_change_log_flag_says_current_data(con):
    """Endringsloggen dekker ikke tildelingsdatoen -> «nåværende data» i flagget."""
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=0)], changes={A: ("2026-01-01", [])})
    f = run(ctx, b, x, contract([A], value=5e6))["brreg_no_staff"]
    assert "nåværende data" in f["data_note"]


def _two_at_same_address(con, changes=None, **kw):
    ents = [_ent(A, "Alfa AS", antallAnsatte=5, forretningsadresse=ADDR), _ent(B, "Beta AS", antallAnsatte=5, forretningsadresse=ADDR)]
    return setup(con, ents, changes=changes, **kw)


def test_T12_address_changed_after_award_halves_weight(con):
    """Beta flyttet til adressen etter tildelingen -> halv vekt, med dato."""
    ctx, b, x = _two_at_same_address(con, changes={A: ("2024-01-01", []),
                                                   B: ("2024-01-01", [("2025-09-01", "adresse", json.dumps(ADDR))])})
    f = run(ctx, b, x, contract([A]), {A, B})["brreg_shared_address"]
    assert f["weight"] == 0.25 and any("adressen endret 2025-09-01" in ch["a"] for ch in f["checks"])


def test_T13_address_unchanged_since_award_keeps_full_weight(con):
    """Ingen adresseendring siden tildelingen for noen av dem -> flagg, full vekt, ingen data_note."""
    ctx, b, x = _two_at_same_address(con, changes={A: ("2024-01-01", [("2025-08-01", "annet", None)]), B: ("2024-01-01", [])},
                                     owners=[(A, None, 100, 2025)])
    f = run(ctx, b, x, contract([A]), {A, B})["brreg_shared_address"]
    assert f["weight"] == 0.5 and "data_note" not in f and f["why"] == "Beta AS"


def test_T14_address_without_change_log_is_current_data(con):
    """Ingen endringslogg -> flagg står, merket «nåværende data»."""
    ctx, b, x = _two_at_same_address(con)
    f = run(ctx, b, x, contract([A]), {A, B})["brreg_shared_address"]
    assert f["weight"] == 0.5 and "nåværende data" in f["data_note"]


def test_T15_role_change_after_award_is_unconfirmed(con):
    """Rollefeeden viser endring etter tildeling, men ikke hvilken -> merket, ikke halvert."""
    roles = [{"orgnr": A, "rolle": "LEDE", "person": "Kari Nordmann", "year": 1970},
             {"orgnr": B, "rolle": "MEDL", "person": "Kari Nordmann", "year": 1970}]
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5), _ent(B, "Beta AS", antallAnsatte=5)], roles=roles,
                      changes={A: ("2024-01-01", []), B: ("2024-01-01", [("2025-10-01", "roller", None)])})
    f = run(ctx, b, x, contract([A]), {A, B})["brreg_shared_suppliers"]
    assert f["weight"] == 1.2 and "roller endret 2025-10-01" in f["data_note"]


def test_T16_shared_roles_inside_group_are_explained(con):
    """Søsterselskaper (samme morselskap) med samme styreleder -> borte."""
    roles = [{"orgnr": A, "rolle": "LEDE", "person": "Kari Nordmann", "year": 1970},
             {"orgnr": B, "rolle": "LEDE", "person": "Kari Nordmann", "year": 1970}]
    ctx, b, x = setup(con, [_ent(A, "Alfa AS"), _ent(B, "Beta AS"), _ent(PARENT, "Holding AS")], roles=roles,
                      owners=[(A, PARENT, 100, 2025), (B, PARENT, 60, 2025)])
    assert "brreg_shared_suppliers" in {f["key"] for f in b.flags(contract([A]), {A, B})}
    assert "brreg_shared_suppliers" not in run(ctx, b, x, contract([A]), {A, B})


# ---------------------------------------------------------------- 5. Samme juridiske enhet etter en endring

def test_T17_sub_unit_and_main_unit_are_same_entity(con):
    """Leverandøren og «konkurrenten» er hovedenhet og underenhet -> borte."""
    sub = _ent(B, "Alfa AS avd. Bergen", kind="underenhet", overordnetEnhet=A, forretningsadresse=ADDR)
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5, forretningsadresse=ADDR), sub])
    assert "brreg_shared_address" not in run(ctx, b, x, contract([A]), {A, B})


def test_T18_merger_chain_from_file_is_same_entity(con):
    """Fusjon A -> C og fisjon C -> B (fil) -> samme virksomhet, også i kjede -> borte."""
    ctx, b, x = _two_at_same_address(con, successors=[(A, C, "fusjon", "2024-01-01"), (C, B, "fisjon", "2024-06-01")])
    assert "brreg_shared_address" not in run(ctx, b, x, contract([A]), {A, B})
    assert ctx.same_entity(A, B).startswith("omorganisering") and ctx.same_entity(B, C).startswith("fisjon")


def test_T19_deleted_entity_with_same_name_is_probably_same(con):
    """Slettet enhet med samme navn som aktiv leverandør -> borte, men forklaringen sier «ikke bekreftet»."""
    old = _ent(B, "Alfa AS", kind="slettet", slettedato="2024-02-01", forretningsadresse=ADDR)
    ctx, b, x = setup(con, [_ent(A, "ALFA AS", antallAnsatte=5, forretningsadresse=ADDR), old])
    assert "ikke bekreftet" in ctx.same_entity(A, B)
    assert "brreg_shared_address" not in run(ctx, b, x, contract([A]), {A, B})


def test_T20_only_unexplained_links_remain(con):
    """To koblinger: én er underenhet (forklart), én er ekte -> flagg med bare den ekte."""
    sub = _ent(B, "Alfa AS avd. Bergen", kind="underenhet", overordnetEnhet=A, forretningsadresse=ADDR)
    ents = [_ent(A, "Alfa AS", antallAnsatte=5, forretningsadresse=ADDR), sub, _ent(C, "Gamma AS", antallAnsatte=5, forretningsadresse=ADDR)]
    ctx, b, x = setup(con, ents)
    f = run(ctx, b, x, contract([A]), {A, B, C})["brreg_shared_address"]
    assert f["why"] == "Gamma AS" and "forklarer" in statuses(f).values()


# ---------------------------------------------------------------- datakildene

def test_T21_change_log_classification():
    assert brreg_changes.classify({"endringstype": "Endring", "endringer": [
        {"op": "replace", "path": "/forretningsadresse/adresse", "value": ["Ny vei 2"]},
        {"op": "replace", "path": "/antallAnsatte", "value": 3}]}) == [("adresse", '["Ny vei 2"]'), ("ansatte", "3")]
    assert brreg_changes.classify({"endringstype": "Endring"}) == [("ukjent", None)]  # eldre endringer uten detaljer
    assert brreg_changes.classify({"endringstype": "Sletting"}) == [("slettet", None)]
    assert brreg_changes.classify({"endringstype": "Endring", "endringer": [{"op": "replace", "path": "/navn"}]}) == [("annet", None)]


def test_T22_successor_file_import(con, tmp_path):
    f = tmp_path / "endringer.csv"
    f.write_text("fra_orgnr;til_orgnr;type;dato\n911 111 111;922222222;fusjon;2024-01-01\n123;922222222;fusjon;\n", encoding="utf-8")
    assert brreg_changes.import_successors(con, f) == 1
    assert dict(con.execute("SELECT fra_orgnr, til_orgnr FROM brreg_successors").fetchone()) == {"fra_orgnr": A, "til_orgnr": B}


def test_T23_regnskap_prefers_company_accounts_and_keeps_all_years():
    body = [{"regnskapstype": "KONSERN", "regnskapsperiode": {"tilDato": "2025-12-31"},
             "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 90}}}},
            {"regnskapstype": "SELSKAP", "regnskapsperiode": {"tilDato": "2025-12-31"},
             "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 10}}}},
            {"regnskapstype": "SELSKAP", "regnskapsperiode": {"tilDato": "2024-12-31"},
             "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 8}}}}]
    assert regnskap.parse(A, body)["driftsinntekter"] == 10
    assert [(y["aar"], y["type"], y["driftsinntekter"]) for y in regnskap.parse_years(A, body)] == \
        [(2024, "SELSKAP", 8), (2025, "KONSERN", 90), (2025, "SELSKAP", 10)]


def test_T24_score_all_attaches_checks(con):
    """Ende til ende: score_all lagrer flaggene med sjekkene."""
    from radar.scoring import score_all
    ctx, b, x = setup(con, [_ent(A, "Alfa AS", antallAnsatte=5)], years=[(A, 2025, "SELSKAP", 3e6)])
    db.upsert_contracts(con, [contract([A], value=8e6, months=24) | {"source": "ted", "method": "Åpen", "winner_names": ["Alfa AS"]}])
    score_all(con, log=lambda *_: None)
    fl = {f["key"]: f for f in json.loads(con.execute("SELECT flags FROM scores").fetchone()["flags"])}
    assert fl["rs_big_share"]["checks"] and fl["rs_big_share"]["why"].startswith("Årlig verdi")
