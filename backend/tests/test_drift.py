"""Varsling, kjørelogg og sporbarhet (radar.check, pipeline._record_run, contracts.fetched_at, build-info)."""
from datetime import datetime, timedelta, timezone

from radar import check, db, pipeline


def _con(tmp_path):
    return db.connect(tmp_path / "radar.sqlite")


def test_check_flags_failed_source_and_stale_data(tmp_path):
    con = _con(tmp_path)
    assert check.problems(con) == ["Ingen fullført oppdatering i databasen"]
    old = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat(timespec="seconds")
    with db.tx(con):
        db.set_meta(con, "updated_at", old)
        db.set_meta(con, "sources", {"Doffin": {"status": "feil", "error": "429"}, "Brreg": {"status": "ok"}})
    p = check.problems(con)
    assert any("50" in m or "36 timer" in m for m in p) and any(m.startswith("Doffin feilet") for m in p)
    with db.tx(con):
        db.set_meta(con, "updated_at", db.now())
        db.set_meta(con, "sources", {"Brreg": {"status": "ok"}})
    assert check.problems(con) == []


def test_check_live_without_new_notices(tmp_path):
    con = _con(tmp_path)
    db.upsert_contracts(con, [{"id": "t1", "source": "ted", "pub_date": "2026-01-02", "winner_ids": [], "winner_names": []}])
    with db.tx(con):
        db.set_meta(con, "updated_at", db.now())
        db.set_meta(con, "mode", "live")
    assert any("Nyeste kunngjøring" in m for m in check.problems(con))


def test_fetched_at_set_on_upsert(tmp_path):
    con = _con(tmp_path)
    db.upsert_contracts(con, [{"id": "t1", "source": "ted", "winner_ids": [], "winner_names": []}])
    assert con.execute("SELECT fetched_at FROM contracts WHERE id='t1'").fetchone()["fetched_at"]


def test_run_is_recorded(tmp_path):
    con = _con(tmp_path)
    pipeline.status["error"] = None
    with db.tx(con):
        db.set_meta(con, "sources", {"Doffin": {"status": "feil"}})
    pipeline._record_run(con)
    runs = db.get_meta(con, "runs")
    assert runs[-1]["ok"] is False and runs[-1]["failed"] == ["Doffin"]
