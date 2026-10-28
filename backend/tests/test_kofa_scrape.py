"""KOFA-skraperen testet mot konstruerte sider (fiktivt innhold, samme oppbygning som nettstedet)."""
from pathlib import Path

import numpy as np

from radar import db
from radar.ingest import kofa, kofa_scrape as ks
from radar.kofa_eval import evaluate, table

FX = Path(__file__).parent / "fixtures"


def test_label():
    assert ks.label("Brudd på regelverket") == 1
    assert ks.label("Vedtak om overtredelsesgebyr") == 1
    assert ks.label("Ikke brudd på regelverket") == 0
    assert ks.label("Avvist som åpenbart uten utsikt til å føre frem") is None
    assert ks.label("Trukket") is None


def test_parse_list():
    html = (FX / "kofa_list.html").read_text()
    rows = ks.parse_list(html)
    assert [r["saksnr"] for r in rows] == ["2026/0297", "2026/1974", "2026/0001"]
    assert rows[0]["dato"] == "2026-10-21" and rows[0]["innklaget"] == "Testdepartementet"
    assert rows[1]["url"] == "https://www.klagenemndssekretariatet.no/sak/2026-1974"
    assert rows[1]["avgjorelse"] == "Ikke brudd på regelverket"
    assert ks.last_page(html) == 493


def test_parse_detail_and_row():
    det = ks.parse_detail((FX / "kofa_sak.html").read_text())
    assert det["klager"] == "Fiktiv Rådgivning AS"
    assert det["avgjorelse"] == "Vedtak om overtredelsesgebyr"
    assert det["gebyr"] == "112245"
    assert det["sammendrag"].startswith("Innklagede gjennomførte")
    lst = ks.parse_list((FX / "kofa_list.html").read_text())[0]
    row = ks.to_row(lst, det)
    assert row["brudd"] == "ja" and row["ulovlig_direkte"] == "ja" and row["leverandor"] == "Fiktiv Rådgivning AS"


def test_csv_roundtrip(tmp_path):
    lst = ks.parse_list((FX / "kofa_list.html").read_text())
    rows = [ks.to_row(r, {}) for r in lst]
    ks.write_csv([r for r in rows if r["brudd"]], tmp_path / "kofa.csv")
    parsed = kofa.parse((tmp_path / "kofa.csv").read_text(encoding="utf-8"))
    assert [(p["saksnr"], p["brudd"], p["ulovlig_direkte"]) for p in parsed] == [("2026/0297", 1, 1), ("2026/1974", 0, 0)]


def test_evaluate(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    rng = np.random.default_rng(1)
    contracts, cases = [], []
    for i in range(40):
        bad = i % 2
        contracts.append({"source": "ted", "id": f"C{i}", "buyer_id": f"9{i:08d}", "buyer": f"Kommune {i}", "cpv": "90910000",
                          "method": "Direkte tildeling" if bad else "Åpen", "title": f"Renhold bygg nummer{i}",
                          "pub_date": "2025-03-01", "est_value": 1e6, "award_value": 1e6 * (1.5 if bad else 1.0),
                          "num_bidders": 1 if bad else int(rng.integers(3, 8)), "winner_ids": [f"8{i:08d}"],
                          "winner_names": [f"Firma {i} AS"], "raw": None})
        cases.append({"saksnr": f"2026/{i}", "dato": "2026-01-01", "innklaget": f"Kommune {i}", "innklaget_orgnr": f"9{i:08d}",
                      "leverandor": f"Firma {i} AS", "tittel": f"Renhold bygg nummer{i}", "brudd": bad, "ulovlig_direkte": bad, "url": None})
    db.upsert_contracts(con, contracts)
    kofa.store(con, cases)
    assert kofa.match(con) == 40
    res = evaluate(con, min_per_class=10)
    assert res["kofa"] == {"n": 40, "brudd": 20, "ikke_brudd": 20}
    assert {m["name"] for m in res["models"]} == {"Logistisk regresjon", "Random Forest", "Gradient Boosting"}
    assert all(0 <= m["kofa_cv"] <= 1 for m in res["models"])
    assert "Syntetisk → KOFA" in table(res)
