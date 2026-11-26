"""Endringskunngjøringer (TED can-modif og Doffin) som fast kontrollpunkt."""
import json

from radar import db
from radar.ingest.ted import parse_modification
from radar.observasjon import link
from radar.scoring import score_all

B, S1 = "944444444", "111111111"


def _checks(con, cid):
    return json.loads(con.execute("SELECT regler FROM scores WHERE contract_id=?", (cid,)).fetchone()["regler"])


def test_parse_modification():
    m = parse_modification({"publication-number": "104283-2025", "publication-date": "2025-02-17+01:00",
                            "procedure-identifier": "P9", "modification-justification": ["mod-cir"],
                            "modification-previous-notice-identifier": ["170031-2024"],
                            "result-value-notice": "53000000", "result-value-cur-notice": "NOK",
                            "modification-description": {"eng": ["Utvidet omfang"]}})
    assert m["previous_notice"] == "170031-2024" and m["new_value"] == 53e6 and m["pub_date"] == "2025-02-17"
    assert m["description"] == "Utvidet omfang" and m["justification"] == "mod-cir"
    assert parse_modification({"publication-number": "1-2025", "result-value-notice": "5", "result-value-cur-notice": "EUR"})["new_value"] is None


def test_changes_become_fixed_check(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    base = {"buyer_id": B, "buyer": "Testby kommune", "cpv": "90910000", "method": "Åpen", "num_bidders": 3,
            "winner_ids": [S1], "winner_names": ["A"]}
    db.upsert_contracts(con, [
        base | {"id": "170031-2024", "source": "ted", "title": "Renhold", "pub_date": "2024-05-01", "award_value": 40e6,
                "raw": {"procedure_id": "P9"}},
        base | {"id": "200-2024", "source": "ted", "title": "Vask", "pub_date": "2024-06-01", "award_value": 1e6,
                "raw": {"procedure_id": "P8"}},
        base | {"id": "doffin-2024-5", "source": "doffin", "title": "Brøyting", "pub_date": "2024-07-01",
                "raw": {"notice_type": "tildeling"}},
        base | {"id": "doffin-2025-9", "source": "doffin", "title": "Brøyting", "pub_date": "2025-01-10",
                "raw": {"notice_type": "endring"}},
    ])
    with db.tx(con):
        con.execute("INSERT INTO contract_changes VALUES ('104283-2025','2025-02-17','P9','170031-2024','mod-cir',53e6,NULL)")
        # Ukjent forrige kunngjøring: kobles på forløpet
        con.execute("INSERT INTO contract_changes VALUES ('5-2025','2025-03-01','P8','999-2023','add-wss',NULL,NULL)")
    link(con, log=lambda *_: None)
    score_all(con, log=lambda *_: None)
    ch = next(x for x in _checks(con, "170031-2024") if x["key"] == "endret")
    assert "uforutsette omstendigheter" in ch["why"] and "53 000 000 kr mot 40 000 000 kr" in ch["why"]
    assert "tilleggsytelser" in next(x for x in _checks(con, "200-2024") if x["key"] == "endret")["why"]
    assert "Doffin 2025-9" in next(x for x in _checks(con, "doffin-2024-5") if x["key"] == "endret")["why"]
    # Teller ikke i prioriteten: ingen flagg med nøkkelen
    flags = json.loads(con.execute("SELECT flags FROM scores WHERE contract_id='170031-2024'").fetchone()["flags"])
    assert all(f["key"] != "endret" for f in flags)
