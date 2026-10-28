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
    assert d["drivers"] and d["suppliers"][0]["brreg"]
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
    assert len(m["metrics"]) == 3 and all(x["auc"] > 0.7 for x in m["metrics"])
    assert m["train"]["kofa"] > 0 and m["extra_flags"] and m["sources"]["KOFA"]["status"] == "demo"


def test_klagerisiko_outside_priority(client):
    m = client.get("/api/model").json()
    assert m["klagerisiko"]["metrics"]["auc"] > 0.6 and m["weights"]["cri"] == 1.0
    rows = client.get("/api/contracts", params={"limit": 50}).json()["rows"]
    assert all(0 <= r["klagerisiko"] <= 100 for r in rows)
    assert len({round(r["klagerisiko"]) for r in rows}) > 1
