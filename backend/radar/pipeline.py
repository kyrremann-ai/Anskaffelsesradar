"""Innhenting + scoring. Kjøres fra «Hent nye data», fra planlagt jobb eller fra kommandolinjen:

    python -m radar.pipeline            # henter etter RADAR_MODE
    python -m radar.pipeline --demo     # fyller med demodata
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import traceback

from . import db
from .config import settings
from .ingest import brreg, doffin, einnsyn, kofa, owners, registers, regnskap, sanctions, ted, valg
from .scoring import ModelBundle, kofa_training, score_all
from .synthetic import demo_brreg, demo_contracts, demo_extra

_lock = threading.Lock()
_bundle: ModelBundle | None = None
status = {"running": False, "step": None, "log": [], "error": None}


def bundle() -> ModelBundle:
    global _bundle
    if _bundle is None:
        _bundle = ModelBundle(settings.seed)
    return _bundle


def _log(msg: str) -> None:
    status["step"] = msg
    status["log"] = (status["log"] + [f"{db.now()} {msg}"])[-200:]
    print(msg, flush=True)


def load_demo(con) -> dict:
    rows = demo_contracts()
    with db.tx(con):
        con.execute("DELETE FROM scores")
        con.execute("DELETE FROM contracts WHERE source='demo'")
        for t in ("regnskap", "owners", "registers", "politicians", "sanctions", "kofa_cases", "kofa_matches"):
            con.execute(f"DELETE FROM {t}")
    db.upsert_contracts(con, rows)
    ents, roles = demo_brreg(rows)
    brreg.load_demo(con, ents, roles, settings.person_salt)
    x = demo_extra(rows, ents, roles)
    regnskap.store(con, x["regnskap"])
    with db.tx(con):
        con.executemany("INSERT INTO owners(orgnr, owner_name, owner_orgnr, owner_birth_year, share_pct, source, year, fetched_at) "
                        "VALUES (:orgnr,:owner_name,:owner_orgnr,:owner_birth_year,:share_pct,:source,:year,'')", x["owners"])
    for reg in ("renhold", "bemanning"):
        registers.store(con, reg, [r for r in x["registers"] if r["register"] == reg])
    valg.store(con, [r | {"navn_norm": db.norm_name(r["navn"])} for r in x["politicians"]], "Demo-valg")
    sanctions.store(con, [r | {"name_norm": db.norm_name(r["name"])} for r in x["sanctions"]])
    kofa.store(con, x["kofa"])
    n = kofa.match(con)
    with db.tx(con):
        db.set_meta(con, "mode", "demo")
    return {"Regnskapsregisteret": len(x["regnskap"]), "Aksjonærregisteret": len(x["owners"]),
            "Renholdsregisteret": sum(r["register"] == "renhold" for r in x["registers"]),
            "Bemanningsregisteret": sum(r["register"] == "bemanning" for r in x["registers"]),
            "Kandidatlister": len(x["politicians"]), "OpenSanctions": len(x["sanctions"]), "KOFA": n}


def _imports(con) -> dict[str, int]:
    """Importerer filer fra data/import/ (aksjonærregister, kandidatlister, KOFA)."""
    d, out = settings.import_dir, {}
    if not d.exists():
        return out
    wanted = set()
    for r in con.execute("SELECT winner_ids FROM contracts"):
        wanted.update(json.loads(r["winner_ids"] or "[]"))
    for f in sorted(d.glob("aksjonaer*.csv")):
        year = next((int(t) for t in re.findall(r"20\d\d", f.name)), None)
        out["Aksjonærregisteret"] = owners.import_shareholders(con, f, year, wanted, log=_log)
    for f in sorted(d.glob("kandidat*.csv")):
        raw = f.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        rows = valg.parse(text)
        valg.store(con, rows, f.stem)
        out["Kandidatlister"] = out.get("Kandidatlister", 0) + len(rows)
    kf = sorted([*d.glob("kofa*.csv"), *d.glob("kofa*.json")])
    for f in kf:
        kofa.store(con, kofa.parse(f.read_text(encoding="utf-8-sig")))
    if kf:
        out["KOFA"] = kofa.match(con)
    return out


def _source(name: str, fn, sources: dict) -> None:
    """Kjører én kilde. En feil stopper ikke resten av oppdateringen."""
    try:
        n = fn()
        sources[name] = {"status": "ok" if n is not None else "av", "rows": n, "at": db.now()}
    except Exception as e:  # noqa: BLE001
        sources[name] = {"status": "feil", "error": str(e)[:300], "at": db.now()}
        _log(f"{name} feilet: {e}")


def live_sources(con) -> dict:
    s, src = settings, {}
    _source("Brønnøysundregistrene", lambda: brreg.refresh(con, s.brreg_user_agent, s.person_salt, log=_log), src)
    _source("Regnskapsregisteret", lambda: regnskap.refresh(con, s.brreg_user_agent, log=_log) if s.regnskap else None, src)
    _source("Proff API", lambda: owners.refresh_proff(con, s.proff_api_key, s.proff_base_url, s.proff_auth_scheme, log=_log)
            if s.proff_api_key else None, src)
    _source("Renholdsregisteret", lambda: registers.refresh(con, "renhold", s.renhold_register_url, log=_log)
            if s.renhold_register_url else None, src)
    _source("Bemanningsregisteret", lambda: registers.refresh(con, "bemanning", s.bemanning_register_url, log=_log)
            if s.bemanning_register_url else None, src)
    _source("OpenSanctions", lambda: sanctions.refresh(con, s.opensanctions_url, s.opensanctions_api_key, log=_log)
            if s.opensanctions_url else None, src)
    try:
        for name, n in _imports(con).items():
            src[name] = {"status": "ok", "rows": n, "at": db.now(), "note": "fra fil"}
    except Exception as e:  # noqa: BLE001
        src["Filimport"] = {"status": "feil", "error": str(e)[:300], "at": db.now()}
        _log(f"Filimport feilet: {e}")
    return src


def run(con, demo: bool | None = None) -> None:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("En oppdatering kjører allerede")
    status.update(running=True, error=None, log=[])
    try:
        demo = settings.mode == "demo" if demo is None else demo
        if demo:
            _log("Laster demodata")
            sources = {k: {"status": "demo", "rows": v, "at": db.now()} for k, v in load_demo(con).items()}
        else:
            rows = ted.refresh(con, settings.data_dir, settings.ted_from, log=_log)
            db.upsert_contracts(con, rows)
            _log(f"Lagret {len(rows)} nye kontrakter fra TED")
            doffin_src: dict = {}
            if settings.doffin:
                _source("Doffin", lambda: db.upsert_contracts(
                    con, doffin.refresh(con, settings.doffin_api_key, settings.ted_from, log=_log)), doffin_src)
            sources = doffin_src | live_sources(con)
            with db.tx(con):
                db.set_meta(con, "mode", "live")
        _log("Kjører modeller")
        global _bundle
        real = kofa_training(con, kofa.labels(con))
        if real is not None and bundle().info["train"].get("kofa") != len(real[1]):
            _log(f"Trener på nytt med {len(real[1])} KOFA-merkede kontrakter")
            _bundle = ModelBundle(settings.seed, real)
        score_all(con, bundle(), log=_log)
        if not demo and settings.einnsyn:
            # Etter scoring, siden bare prioriterte kontrakter slås opp
            _source("eInnsyn", lambda: einnsyn.refresh(con, log=_log), sources)
        with db.tx(con):
            db.set_meta(con, "sources", sources)
        with db.tx(con):
            db.set_meta(con, "updated_at", db.now())
        _log("Ferdig")
    except Exception as e:  # noqa: BLE001
        status["error"] = str(e)
        _log(f"Feil: {e}")
        traceback.print_exc()
    finally:
        status["running"] = False
        _lock.release()


def run_in_background(con, demo: bool | None = None) -> bool:
    if status["running"]:
        return False
    threading.Thread(target=run, args=(con, demo), daemon=True).start()
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    run(db.connect(settings.db_path), demo=True if a.demo else None)
    if status["error"]:
        raise SystemExit(f"Oppdateringen feilet: {status['error']}")
