import json
import sys

from radar import ai_vurderinger as av


def row(i, tier="Høy", title="Renhold", **kw):
    return {"id": i, "title": title, "buyer": "Kommune", "pub_date": "2026-01-01", "method": "open", "tier": tier,
            "priority": 90, "notice_type": "tildeling", "flags": [{"label": "Ett registrert tilbud", "source": "cri"}], **kw}


def test_bare_nye_og_endrede_saker(tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    script = tmp_path / "fake.py"
    script.write_text(f"import sys\nsys.stdin.read()\nopen({str(calls)!r}, 'a').write('x')\nprint('**Vurdering:** sjekk')\n")
    monkeypatch.setenv("RADAR_AI_CMD", f"{sys.executable} {script}")
    store = {}
    s = av.update(store, [row("a"), row("b"), row("c", tier="Lav"), row("d", notice_type="intensjon")], log=lambda *_: None)
    assert s == {"wanted": 2, "made": 2, "failed": 0, "removed": 0}
    assert set(store) == {"a", "b"} and store["a"]["kind"] == "nattlig" and store["a"]["text"].startswith("**Vurdering")
    # Uendret: ingen nye kall. Endret tittel: ny vurdering. Ikke lenger høy: fjernes.
    s = av.update(store, [row("a"), row("b", title="Vakthold")], log=lambda *_: None)
    assert s["made"] == 1 and s["removed"] == 0
    s = av.update(store, [row("a", tier="Forhøyet"), row("b", title="Vakthold")], log=lambda *_: None)
    assert s == {"wanted": 1, "made": 0, "failed": 0, "removed": 1} and set(store) == {"b"}
    assert len(calls.read_text()) == 3


def test_feil_lagres_ikke(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_AI_CMD", f"{sys.executable} -c \"import sys; sys.exit(1)\"")
    store = {}
    s = av.update(store, [row("a")], log=lambda *_: None)
    assert s["failed"] == 1 and store == {}


def test_main_skriver_ai_json(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_AI_CMD", f"{sys.executable} -c \"print('ok')\"")
    (tmp_path / "contracts-1.json").write_text(json.dumps([row("a")]))
    assert av.main([str(tmp_path), str(tmp_path / "store.json")]) == 0
    assert json.loads((tmp_path / "ai.json").read_text())["a"]["text"] == "ok"
