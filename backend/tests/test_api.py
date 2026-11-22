import importlib
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["RADAR_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    os.environ["RADAR_MODE"] = "demo"
    from radar import api, config, pipeline
    importlib.reload(config)
    importlib.reload(pipeline)
    importlib.reload(api)
    pipeline.run(api.con, demo=True)
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_summary_and_scope(client):
    s = client.get("/api/summary").json()
    assert s["n"] == 1800 and s["n_high"] > 0 and s["flag_counts"]
    b = client.get("/api/buyers").json()[0]["id"]
    scoped = client.get("/api/summary", params={"buyers": b}).json()
    assert 0 < scoped["n"] < s["n"] and scoped["national"]["n"] == s["n"]


def test_contracts_detail_brreg_and_assess(client):
    rows = client.get("/api/contracts", params={"limit": 5}).json()["rows"]
    assert rows[0]["priority"] >= rows[-1]["priority"]
    d = client.get(f"/api/contracts/{rows[0]['id']}").json()
    assert "drivers" not in d and "ml_score" not in d and d["suppliers"][0]["brreg"]
    a = client.post(f"/api/contracts/{rows[0]['id']}/assess").json()
    assert "Vurdering" in a["text"] and a["provider"] == "mal"
    keys = {f["key"] for r in client.get("/api/contracts", params={"limit": 500}).json()["rows"] for f in r["flags"]}
    assert any(k.startswith("brreg_") for k in keys)


def test_cases_audit_trail(client):
    cid = client.get("/api/contracts", params={"limit": 1}).json()["rows"][0]["id"]
    c = client.post("/api/cases", json={"contractId": cid}, headers={"X-Remote-User": "kari"}).json()
    c = client.patch(f"/api/cases/{c['id']}", json={"status": "Under vurdering", "note": "Be om protokoll"}).json()
    assert [e["field"] for e in c["events"]][:2] == ["note", "status"] or {e["field"] for e in c["events"]} >= {"status", "note", "opprettet"}
    assert client.patch(f"/api/cases/{c['id']}", json={"status": "Tull"}).status_code == 422
    html = client.get(f"/api/cases/{c['id']}/arbeidspapir").text
    assert "Arbeidspapir" in html and "Be om protokoll" in html
    assert client.get("/api/contracts", params={"case_status": "med"}).json()["total"] == 1
    c = client.patch(f"/api/cases/{c['id']}", json={"vurdering": "Ikke relevant", "vurdering_grunn": "Rammeavtale, normalt"},
                     headers={"X-Remote-User": "per"}).json()
    assert c["vurdering"] == "Ikke relevant" and any(e["field"] == "vurdering" and e["actor"] == "per" for e in c["events"])
    assert client.patch(f"/api/cases/{c['id']}", json={"vurdering": "Kanskje"}).status_code == 422
    lines = client.get("/api/labels.csv").text.splitlines()
    assert lines[0].startswith("\ufeffkontrakt_id;") and len(lines) == 2
    assert f"{cid};" in lines[1] and ";Ikke relevant;0;Rammeavtale, normalt;per;" in lines[1]


def test_suppliers_and_export(client):
    s = client.get("/api/suppliers").json()
    assert s["total"] == 140 and any(r["brreg_flags"] for r in s["rows"])
    d = client.get(f"/api/suppliers/{s['rows'][0]['orgnr']}").json()
    assert d["contracts"] and "links" in d and {"regnskap", "owners", "registers", "sanctions"} <= d.keys()
    csv = client.get("/api/contracts/export.csv").text
    assert csv.startswith("﻿id;") and len(csv.splitlines()) == 1801


def test_model(client):
    m = client.get("/api/model").json()
    assert "metrics" not in m and m["weights"] == {"brreg_per_weight": 8.0, "brreg_cap": 20.0}
    assert m["extra_flags"] and m["sources"]["KOFA"]["status"] == "demo"


def test_klagerisiko_outside_priority(client):
    m = client.get("/api/model").json()
    assert m["klagerisiko"]["metrics"]["auc"] > 0.6
    rows = client.get("/api/contracts", params={"limit": 50}).json()["rows"]
    assert all(0 <= r["klagerisiko"] <= 100 for r in rows)
    assert len({round(r["klagerisiko"]) for r in rows}) > 1


def test_dismiss_flag_lowers_priority_and_is_logged(client):
    rows = client.get("/api/contracts", params={"limit": 50}).json()["rows"]
    r = next(x for x in rows if any((f.get("bidrag") or 0) > 0 for f in x["flags"]))
    f = max(r["flags"], key=lambda f: f.get("bidrag") or 0)
    client.post("/api/cases", json={"contractId": r["id"]}, headers={"X-Remote-User": "kari"})
    assert client.post(f"/api/contracts/{r['id']}/flags/{f['key']}/dismiss", json={"reason": "tull"}).status_code == 422
    assert client.post(f"/api/contracts/{r['id']}/flags/{f['key']}/dismiss", json={"reason": "annet"}).status_code == 422
    res = client.post(f"/api/contracts/{r['id']}/flags/{f['key']}/dismiss", json={"reason": "konsern", "note": "Samme konsern"},
                      headers={"X-Remote-User": "kari@kommune.no"}).json()
    d = res["contract"]
    g = next(x for x in d["flags"] if x["key"] == f["key"])
    assert g["dismissed"]["actor"] == "kari@kommune.no" and g["bidrag"] == 0
    assert abs(d["priority"] - (r["priority"] - f["bidrag"])) < 0.01
    assert any(e["field"] == "flagg avskrevet" and e["actor"] == "kari@kommune.no" for e in d["case"]["events"])
    assert client.post(f"/api/contracts/{r['id']}/flags/{f['key']}/dismiss", json={"reason": "konsern"}).status_code == 409
    assert "Avskrevet" in client.get(f"/api/cases/{d['case']['id']}/arbeidspapir").text
    csv = client.get("/api/dismissals.csv").text.splitlines()
    assert len(csv) == 2 and ";konsern;Samme konsern;kari@kommune.no;" in csv[1]
    assert client.delete(f"/api/dismissals/{res['id']}", headers={"X-Remote-User": "per"}).json() == {"revoked": res["id"]}
    d = client.get(f"/api/contracts/{r['id']}").json()
    assert "dismissed" not in next(x for x in d["flags"] if x["key"] == f["key"])
    assert client.get("/api/dismissals.csv").text.splitlines()[1].endswith(";per")


def test_supplier_scope_dismissal_survives_rescoring(client):
    from radar import api, pipeline
    rows = client.get("/api/contracts", params={"limit": 500}).json()["rows"]
    r = next(x for x in rows if len(x["winner_ids"]) == 1 and any(f["source"] == "brreg" and not f.get("dismissed") for f in x["flags"]))
    f = next(f for f in r["flags"] if f["source"] == "brreg")
    client.post(f"/api/contracts/{r['id']}/flags/{f['key']}/dismiss", json={"reason": "konsern", "scope": "leverandor"})
    pipeline.run(api.con, demo=True)
    for x in client.get("/api/contracts", params={"supplier": r["winner_ids"][0], "limit": 500}).json()["rows"]:
        for g in x["flags"]:
            if g["key"] == f["key"]:
                assert g["dismissed"]["scope"] == "leverandor" and g["bidrag"] == 0


def test_require_user(client, monkeypatch):
    import dataclasses
    from radar import api
    monkeypatch.setattr(api, "settings", dataclasses.replace(api.settings, require_user=True))
    cid = client.get("/api/contracts", params={"limit": 3}).json()["rows"][2]["id"]
    assert client.post("/api/cases", json={"contractId": cid}).status_code == 401
    assert client.post("/api/cases", json={"contractId": cid}, headers={"X-Remote-User": "ola@kommune.no"}).status_code == 200
