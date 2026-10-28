"""Observasjonsenhet: duplikater, forløp, intensjoner og faste kontrollpunkter."""
from types import SimpleNamespace

import pytest

from radar import db
from radar.ingest.doffin import parse_hit
from radar.observasjon import link
from radar.scoring import fixed_checks, score_all

B, S1, S2 = "944444444", "111111111", "222222222"


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.sqlite")
    base = {"buyer_id": B, "buyer": "Testby kommune", "cpv": "90910000", "method": "Åpen", "num_bidders": 3}
    db.upsert_contracts(c, [
        # Samme tildeling i TED og Doffin (Doffin har bare én av vinnerne)
        base | {"id": "1-2025", "source": "ted", "title": "Cleaning", "pub_date": "2025-03-01", "award_value": 5e6,
                "winner_ids": [S1, S2], "winner_names": ["A", "B"], "raw": {"procedure_id": "P1"}},
        base | {"id": "doffin-2025-1", "source": "doffin", "title": "Renhold", "pub_date": "2025-03-02",
                "winner_ids": [S1], "winner_names": ["A"], "raw": {"notice_type": "tildeling"}},
        # Rettelse i samme TED-forløp: samme leverandør og verdi
        base | {"id": "2-2025", "source": "ted", "title": "Vask", "pub_date": "2025-04-01", "award_value": 1e6,
                "winner_ids": [S2], "winner_names": ["B"], "raw": {"procedure_id": "P2"}},
        base | {"id": "3-2025", "source": "ted", "title": "Vask", "pub_date": "2025-04-10", "award_value": 1e6,
                "winner_ids": [S2], "winner_names": ["B"], "raw": {"procedure_id": "P2"}},
        # Ny delkontrakt i samme forløp med annen leverandør: egen kontrakt
        base | {"id": "4-2025", "source": "ted", "title": "Vask lot 2", "pub_date": "2025-04-10", "award_value": 2e6,
                "winner_ids": [S1], "winner_names": ["A"], "raw": {"procedure_id": "P2"}},
        # Intensjon
        base | {"id": "doffin-2025-2", "source": "doffin", "title": "Direkte kjøp", "pub_date": "2025-05-01", "method": "Direkte tildeling",
                "num_bidders": None, "winner_ids": [S2], "winner_names": ["B"], "raw": {"notice_type": "intensjon"}},
    ])
    return c


def test_link_counts(con):
    s = link(con, log=lambda *_: None)
    assert s["kunngjøringer"] == 6 and s["kontrakter"] == 3 and s["forløp"] == 2
    assert s["duplikater"] == {"Samme tildeling i Doffin og TED": 1,
                               "Samme TED-forløp og leverandør (rettelse eller ny publisering)": 1}
    dup = dict(con.execute("SELECT contract_id, duplicate_of FROM observasjon WHERE duplicate_of IS NOT NULL").fetchall())
    assert dup == {"doffin-2025-1": "1-2025", "2-2025": "3-2025"}


def test_intents_not_ranked(con):
    link(con, log=lambda *_: None)
    score_all(con, log=lambda *_: None)
    tiers = dict(con.execute("SELECT contract_id, tier FROM scores").fetchall())
    assert tiers["doffin-2025-2"] == "Intensjon" and "doffin-2025-1" not in tiers and "2-2025" not in tiers


def test_doffin_intent_has_no_invented_bid_count():
    h = {"id": "2025-9", "type": "ANNOUNCEMENT_OF_INTENT", "buyer": [{"organizationId": B, "name": "X"}],
         "lots": [{"winner": [{"organizationId": S1, "name": "A"}]}], "publicationDate": "2025-01-01"}
    r = parse_hit(h)
    assert r["num_bidders"] is None and r["raw"]["notice_type"] == "intensjon"


def _r(**kw):
    base = dict(notice_type="tildeling", num_bidders=3, method="Åpen", award_value=None, est_value=None,
                tender_duration_days=None, cn_procedure=None, cn_accelerated=0, cn_ref=None)
    return SimpleNamespace(**(base | kw))


def test_fixed_checks():
    assert [x["key"] for x in fixed_checks(_r(num_bidders=1))] == ["ett_tilbud"]
    assert [x["key"] for x in fixed_checks(_r(method="Direkte tildeling", award_value=2e6))] == ["direkte_over_terskel"]
    assert fixed_checks(_r(method="Direkte tildeling", award_value=5e5)) == []
    assert [x["key"] for x in fixed_checks(_r(tender_duration_days=20, cn_procedure="open", cn_ref="1-2024"))] == ["kort_frist"]
    assert fixed_checks(_r(tender_duration_days=20, cn_procedure="open", cn_accelerated=1, cn_ref="1-2024")) == []
    assert fixed_checks(_r(tender_duration_days=20, cn_procedure="oth-single", cn_ref="1-2024")) == []
