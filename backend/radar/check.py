"""Sjekker siste oppdatering og avslutter med feil hvis noe bør varsles.

    python -m radar.check            # etter pipeline: kildefeil og gamle data
    python -m radar.check --max-hours 36

Nattjobben kjører denne etter publisering. Feiler sjekken, blir jobben rød og GitHub sender e-post,
men dataene som faktisk ble hentet er allerede publisert.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings


def problems(con, max_hours: float = 36, max_pub_days: int = 10) -> list[str]:
    out = []
    upd = db.get_meta(con, "updated_at")
    if not upd:
        return ["Ingen fullført oppdatering i databasen"]
    now = datetime.now(timezone.utc)
    if now - datetime.fromisoformat(upd) > timedelta(hours=max_hours):
        out.append(f"Dataene er sist oppdatert {upd}, mer enn {max_hours:g} timer siden")
    for name, s in (db.get_meta(con, "sources") or {}).items():
        if s.get("status") == "feil":
            out.append(f"{name} feilet: {s.get('error', '')}")
    if db.get_meta(con, "mode") == "live":
        # TED og Doffin publiserer hver virkedag. Ingen nye kunngjøringer på mange dager betyr at innhentingen har stoppet.
        last = con.execute("SELECT MAX(pub_date) AS d FROM contracts WHERE source IN ('ted','doffin')").fetchone()["d"]
        if not last or now.date() - datetime.fromisoformat(last[:10]).date() > timedelta(days=max_pub_days):
            out.append(f"Nyeste kunngjøring er fra {last}, mer enn {max_pub_days} dager siden")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=36)
    ap.add_argument("--max-pub-days", type=int, default=10)
    a = ap.parse_args()
    p = problems(db.connect(settings.db_path), a.max_hours, a.max_pub_days)
    for msg in p:
        print(f"::error::{msg}", flush=True)
    if p:
        raise SystemExit(1)
    print("Alle kilder OK")
