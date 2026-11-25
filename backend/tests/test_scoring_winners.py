"""Alle vinnere telles i gjentakelse og markedsandel, laveste tilbudstall over delkontrakter, og NACE-kode 1–3."""
from radar import db, scoring
from radar.ingest.brreg import entity_summary
from radar.observasjon import link

B = "944444444"


def _c(i, winners, date, value=1e6, raw=None, bidders=3):
    return {"id": f"T{i}", "source": "ted", "url": "x", "title": f"K{i}", "pub_date": date, "buyer_id": B, "buyer": "Testby kommune",
            "cpv": "79000000", "method": "Åpen", "framework": 1, "num_bidders": bidders, "award_value": value,
            "winner_ids": winners, "winner_names": winners, "raw": raw or {"procedure_id": f"P{i}"}}


def test_all_winners_count(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    # A er nummer to i den parallelle rammeavtalen; tidligere ble bare første vinner (B) talt
    db.upsert_contracts(con, [_c(1, ["111111111", "222222222"], "2025-01-01"), _c(2, ["222222222"], "2025-02-01"),
                              _c(3, ["111111111"], "2025-03-01", raw={"procedure_id": "P3", "min_bidders": 1}, bidders=4)])
    link(con, log=lambda *_: None)
    df = scoring.contracts_frame(con).set_index("id")
    assert df.loc["T2", "repeat_pair_count"] == 1  # 222 vant også T1 (som nummer to)
    assert df.loc["T3", "repeat_pair_count"] == 1
    # T1 deles på to: 111 har 0,5 + 1 = 1,5 av 3 mill. i CPV 79
    assert round(df.loc["T3", "winner_market_share_pct"]) == 50
    r = df.loc["T3"]
    keys = [c["key"] for c in scoring.fixed_checks(r)]
    assert "ett_tilbud_delkontrakt" in keys and "ett_tilbud" not in keys


def test_nace_codes_2_and_3():
    s = entity_summary({"organisasjonsnummer": "1", "naeringskode1": {"kode": "70.220"}, "naeringskode2": {"kode": "62.010"}})
    assert s["nace_seksjon"] == "M" and s["nace_seksjoner"] == ["J", "M"]
