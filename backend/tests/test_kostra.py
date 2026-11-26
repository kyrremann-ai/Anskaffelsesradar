"""SSB KOSTRA-grupper: innlesing og sammenligningsgruppe for kommuner."""
import json

import pandas as pd

from radar import db, kostra
from radar.scoring import peer_rank


def test_bundled_file():
    m, src = kostra.load(None)
    assert len(m) == 357 and src == "kostra_grupper_2024.csv"
    assert m["0301"] == 13 and m["4601"] == 12 and set(m.values()) == set(range(1, 18))


def test_parse_xlsx_headers_and_import_override(tmp_path):
    assert kostra.parse([["komnr", "komnavn", "kostragr"], [301, "Oslo", 13], [1160, "Vindafjord", 1]]) == {"0301": 13, "1160": 1}
    (tmp_path / "kostra_2025.csv").write_text("kommunenr;kommune;kostragruppe\n" + "".join(f"{i:04d};K{i};{i % 3 + 1}\n" for i in range(1, 400)))
    m, src = kostra.load(tmp_path)
    assert src == "kostra_2025.csv" and m["0003"] == 1


def test_peer_rank_prefers_kostra_group():
    n = 70
    df = pd.DataFrame({"buyer_type": ["Kommune"] * n + ["Stat"] * 40, "sector": ["Helse"] * (n + 40),
                       "kostra_gruppe": [7.0] * 35 + [3.0] * 35 + [None] * 40})
    award = pd.Series(True, index=df.index)
    _, g = peer_rank(df, pd.Series(range(len(df)), dtype=float), award)
    assert g[0] == "Kommune i KOSTRA-gruppe 7 × Helse" and g[40] == "Kommune i KOSTRA-gruppe 3 × Helse"
    assert g[n] == "Stat × Helse"
    # For få kontrakter i gruppen: faller tilbake på oppdragsgivertype × sektor
    _, g = peer_rank(df.assign(kostra_gruppe=[7.0] * 10 + [None] * (n + 30)), pd.Series(range(len(df)), dtype=float), award)
    assert g[0] == "Kommune × Helse"


def test_score_all_sets_kostra_gruppe(tmp_path):
    from radar.scoring import score_all
    from radar.observasjon import link

    con = db.connect(tmp_path / "t.sqlite")
    with db.tx(con):
        con.execute("INSERT INTO brreg_entities VALUES (?,?,?)", ("944444444", "2026-01-01", json.dumps(
            {"organisasjonsnummer": "944444444", "navn": "TESTBY KOMMUNE", "organisasjonsform": {"kode": "KOMM"},
             "forretningsadresse": {"kommunenummer": "4601"}})))
    db.upsert_contracts(con, [{"id": "1-2025", "source": "ted", "title": "Vask", "pub_date": "2025-03-01", "buyer_id": "944444444",
                               "buyer": "Testby kommune", "cpv": "90910000", "method": "Åpen", "num_bidders": 3,
                               "winner_ids": ["111111111"], "winner_names": ["A"], "raw": {}}])
    link(con, log=lambda *_: None)
    score_all(con, log=lambda *_: None)
    assert con.execute("SELECT kostra_gruppe FROM scores").fetchone()[0] == 12
