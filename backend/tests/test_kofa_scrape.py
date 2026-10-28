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
    from radar.kofa_models import compare, table as mtable
    cmp = compare(con, None)
    assert cmp["n"] == 40 and cmp["from_doffin"] == 0
    names = {m["name"] for m in cmp["models"]}
    assert {"CRI (røde flagg)", "Regularisert LR (L2, C=0,1)"} <= names
    assert all(m["lo"] <= m["hi"] for m in cmp["models"] if "auc" in m)
    assert "95 % KI" in mtable(cmp)


def test_link_helpers():
    from radar.ingest import kofa_link as kl
    s = "Innklagede gjennomførte en åpen anbudskonkurranse for anskaffelse av renholdstjenester. Klager anførte noe."
    assert kl.subject(s) == "anskaffelse av renholdstjenester"
    case = {"innklaget": "Testby kommune", "tittel": s}
    hits = [{"buyer": [{"name": "Annen kommune"}], "heading": "Renholdstjenester", "type": "ANNOUNCEMENT_OF_COMPETITION"},
            {"buyer": [{"name": "Testby kommune"}], "heading": "Renholdstjenester 2025", "type": "ANNOUNCEMENT_OF_COMPETITION",
             "issueDate": "2025-01-01T00:00:00Z", "deadline": "2025-02-01T00:00:00Z", "id": "2025-1", "lots": []}]
    h, score = kl.best_hit(hits, case)
    assert h is hits[1] and score >= 0.5
    row = kl.hit_row(h)
    assert row["winner_ids"] == [] and row["tender_duration_days"] == 31 and row["id"] == "doffin-2025-1"


def test_klagerisiko(tmp_path):
    import json as _json
    from radar import klagerisiko as kr
    rng = np.random.default_rng(0)

    def hit(i, big):
        return {"id": f"2025-{i}", "type": kr.COMPETITION, "issueDate": f"202{4 + i % 2}-03-01T00:00:00Z",
                "deadline": f"202{4 + i % 2}-04-{10 if big else 1:02d}T00:00:00Z", "heading": "Rammeavtale renhold",
                "description": "x" * int(rng.integers(10, 500)), "cpvCodes": ["90910000"], "lots": [{}] * (3 if big else 1),
                "estimatedValue": {"amount": float(5e7 if big else 5e5), "currencyCode": "NOK"},
                "buyer": [{"name": f"Kommune {i % 30}"}]}
    link = {f"2025/{i}": {"score": 1, "hit": hit(i, True)} for i in range(40)}
    sample = {h["id"]: h for h in (hit(i, rng.random() < 0.2) for i in range(100, 400))}
    (tmp_path / "l.json").write_text(_json.dumps(link))
    (tmp_path / "s.json").write_text(_json.dumps({"months": [], "hits": sample}))
    f = kr.features(hit(1, True))
    assert f["tender_days"] == 40 and f["n_lots"] == 3 and f["buyer_kommune"] == 1.0 and f["framework"] == 1.0
    r = kr.evaluate(tmp_path / "l.json", tmp_path / "s.json")
    assert r["positive"] == 40 and r["models"][0]["auc"] > 0.7
    assert "AUC siste år" in kr.table(r)
