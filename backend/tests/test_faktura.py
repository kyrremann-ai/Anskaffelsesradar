"""Kundens fakturaer mot kunngjorte kontrakter. Syntetiske data: fiktive leverandører og beløp."""
from pathlib import Path

import pytest

from radar import db
from radar.fakturakontroll import analyse
from radar.ingest import faktura
from radar.observasjon import link

FX = Path(__file__).parent / "fixtures"
B = "944444444"


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.sqlite")
    # Én kunngjort kontrakt med Rent og Pent AS (1 mill.), ingen med Fiktiv Konsulent AS
    db.upsert_contracts(c, [{"id": "1-2025", "source": "ted", "buyer_id": B, "buyer": "Testby kommune", "title": "Renhold",
                             "pub_date": "2025-01-02", "award_value": 1e6, "method": "Åpen", "framework": 0,
                             "winner_ids": ["111111111"], "winner_names": ["Rent og Pent AS"], "raw": {"procedure_id": "P1"}}])
    link(c, log=lambda *_: None)
    return c


def test_parse_norwegian_formats():
    rows, skipped = faktura.parse(faktura.read_rows((FX / "faktura_944444444.csv").read_bytes(), "x.csv"), B)
    assert len(rows) == 9 and skipped == 1
    assert rows[0]["amount"] == 400000.0 and rows[0]["date"] == "2025-01-15"
    assert {r["buyer_id"] for r in rows} == {B}
    assert faktura._amount("1.234,50") == 1234.5 and faktura._amount("1,234.50") == 1234.5


def test_checks(con):
    assert faktura.import_file(con, FX / "faktura_944444444.csv", log=lambda *_: None) == 9
    res = {r["orgnr"]: r for r in analyse(con)["rows"]}
    keys = {k: [c["key"] for c in r["checks"]] for k, r in res.items()}
    assert keys["333333333"] == ["uten_kontrakt_over_terskel"]  # 1,4 mill. på 12 mnd, ingen kontrakt
    assert keys["111111111"] == ["over_kontraktsverdi"]  # 1,6 mill. mot 1 mill. kunngjort
    assert keys["444444444"] == ["uten_kontrakt_over_100k"]
    assert keys["555555555"] == []
    assert res["111111111"]["contracts"][0]["id"] == "1-2025"
    assert res["111111111"]["max_12m"] == 1_600_000
