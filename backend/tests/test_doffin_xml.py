"""Doffin-tildelinger beriket med eForms-XML (prosedyre, tildelt verdi, varighet). Ingen nettverk: XML fra fixtures."""
import json
import shutil
from pathlib import Path

from radar import db
from radar.ingest import doffin

FX = Path(__file__).parent / "fixtures" / "doffin_eforms_2026-118230.xml"


def _row(nid="2026-118230", t="ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT"):
    return {"id": f"doffin-{nid}", "source": "doffin", "url": "x", "title": "Utredning", "pub_date": "2026-11-24",
            "buyer_id": "972417793", "buyer": "Barne- og familiedepartementet", "region": "Oslo", "sector": "Annet",
            "cpv": "73000000", "nature": "Annet", "method": "Annet", "accelerated": 0, "justification": None, "framework": 0,
            "num_bidders": None, "est_value": 1800000.0, "award_value": None, "tender_duration_days": None, "contract_months": None,
            "winner_ids": ["997058925"], "winner_names": ["OsloMet"], "winner_size": None,
            "raw": {"doffin_type": t, "notice_type": doffin.notice_type(t)}}


def test_enrich_from_cache(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    db.upsert_contracts(con, [_row(), _row("2026-1", "ANNOUNCEMENT_OF_INTENT")])
    cache = tmp_path / "xml"
    cache.mkdir()
    shutil.copy(FX, cache / "2026-118230.xml")
    assert doffin.enrich(con, "", cache, limit=0, log=lambda *_: None) == 1  # intensjonen hoppes over
    r = dict(con.execute("SELECT * FROM contracts WHERE id='doffin-2026-118230'").fetchone())
    raw = json.loads(r["raw"])
    assert r["award_value"] == 1800000.0 and r["method"] == "Åpen" and r["nature"] == "Tjenester"
    assert r["contract_months"] == 13.0 and r["num_bidders"] == 4.0
    assert json.loads(r["winner_ids"]) == ["997058925"] and r["source"] == "doffin"
    assert raw["xml"] is True and raw["procedure_id"] and raw["notice_type"] == "tildeling"
    # Allerede beriket: ingenting å gjøre neste gang
    assert doffin.enrich(con, "", cache, limit=0, log=lambda *_: None) == 0


def test_missing_xml_not_retried(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    db.upsert_contracts(con, [_row()])
    cache = tmp_path / "xml"
    cache.mkdir()
    (cache / "2026-118230.xml").write_bytes(b"")  # 404 tidligere
    assert doffin.enrich(con, "", cache, limit=0, log=lambda *_: None) == 1
    raw = json.loads(con.execute("SELECT raw FROM contracts").fetchone()["raw"])
    assert raw["xml"] is False
