"""Innhenting + scoring. Kjøres fra «Hent nye data», fra planlagt jobb eller fra kommandolinjen:

    python -m radar.pipeline            # henter etter RADAR_MODE
    python -m radar.pipeline --demo     # fyller med demodata
"""
from __future__ import annotations

import argparse
import threading
import traceback

from . import db
from .config import settings
from .ingest import brreg, ted
from .scoring import ModelBundle, score_all
from .synthetic import demo_brreg, demo_contracts

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


def load_demo(con) -> None:
    rows = demo_contracts()
    with db.tx(con):
        con.execute("DELETE FROM scores")
        con.execute("DELETE FROM contracts WHERE source='demo'")
    db.upsert_contracts(con, rows)
    ents, roles = demo_brreg(rows)
    brreg.load_demo(con, ents, roles, settings.person_salt)
    with db.tx(con):
        db.set_meta(con, "mode", "demo")


def run(con, demo: bool | None = None) -> None:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("En oppdatering kjører allerede")
    status.update(running=True, error=None, log=[])
    try:
        demo = settings.mode == "demo" if demo is None else demo
        if demo:
            _log("Laster demodata")
            load_demo(con)
        else:
            rows = ted.refresh(con, settings.data_dir, settings.ted_from, log=_log)
            db.upsert_contracts(con, rows)
            _log(f"Lagret {len(rows)} nye kontrakter fra TED")
            brreg.refresh(con, settings.brreg_user_agent, settings.person_salt, log=_log)
            with db.tx(con):
                db.set_meta(con, "mode", "live")
        _log("Kjører modeller")
        score_all(con, bundle(), log=_log)
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
