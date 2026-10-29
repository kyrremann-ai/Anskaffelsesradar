"""AI-vurderinger laget på forhånd, for visninger uten egen Claude-tilgang (radar.vikingsea.no).

Nattjobben kjører etter eksporten: `python -m radar.ai_vurderinger <eksportmappe> <lager.json>`.
Den vurderer kontrakter med høy prioritet med samme instruks og felt som serverens AI-vurdering (llm.py),
og bare de som er nye eller har endret seg siden sist (hash av instruks + felt). Vurderinger for kontrakter
som ikke lenger har høy prioritet, fjernes. Resultatet skrives til lageret og som ai.json i eksportmappen.

Modellen kalles via Claude Code (`claude -p`), som bruker kontoen nattjobben kjører på. RADAR_AI_CMD
kan peke på en annen kommando (leser prompten på stdin, skriver svaret på stdout), f.eks. i tester.
Personvern: bare kontraktsfelt og flagg sendes (build_input), aldri navn fra Brreg-roller.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import llm

MODEL = os.environ.get("RADAR_AI_MODEL", "sonnet")
WORKERS = 4


def rows(export_dir: Path) -> list[dict]:
    out = []
    for f in sorted(export_dir.glob("contracts-*.json")):
        out += json.loads(f.read_text(encoding="utf-8"))
    return out


def wanted(rs: list[dict]) -> dict[str, tuple[str, dict]]:
    """Kontrakter med høy prioritet: id → (hash, felt som sendes)."""
    out = {}
    for r in rs:
        if r.get("tier") != "Høy" or r.get("notice_type", "tildeling") != "tildeling":
            continue
        data = llm.build_input(r)
        h = hashlib.sha256((llm.SYSTEM + json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)).encode()).hexdigest()[:16]
        out[r["id"]] = (h, data)
    return out


def _cmd() -> list[str]:
    if c := os.environ.get("RADAR_AI_CMD"):
        return shlex.split(c)
    return ["claude", "-p", "--model", MODEL, "--tools", "", "--system-prompt", llm.SYSTEM]


def complete(data: dict) -> str:
    prompt = "Kontrakt:\n" + json.dumps(data, ensure_ascii=False, default=str)
    p = subprocess.run(_cmd(), input=prompt, capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError((p.stderr or p.stdout or f"kode {p.returncode}").strip()[:300])
    return p.stdout.strip()


def update(store: dict, rs: list[dict], limit: int | None = None, log=print) -> dict:
    want = wanted(rs)
    gone = [k for k in store if k not in want]
    for k in gone:
        del store[k]
    todo = [(k, h, d) for k, (h, d) in want.items() if store.get(k, {}).get("hash") != h][:limit]
    log(f"AI-vurderinger: {len(want)} med høy prioritet, {len(todo)} nye/endrede, {len(gone)} fjernet")
    model = "claude-" + MODEL if "RADAR_AI_CMD" not in os.environ else "test"

    def one(t):
        k, h, d = t
        try:
            return k, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provider": "claude", "model": model,
                       "text": complete(d), "hash": h, "kind": "nattlig"}
        except Exception as e:  # én feil stopper ikke resten; prøves igjen neste natt
            log(f"  {k}: {e}")
            return k, None

    failed = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        for k, a in ex.map(one, todo):
            if a:
                store[k] = a
            else:
                failed += 1
    if failed:
        log(f"AI-vurderinger: {failed} feilet")
    return {"wanted": len(want), "made": len(todo) - failed, "failed": failed, "removed": len(gone)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir", type=Path)
    ap.add_argument("store", type=Path)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    store = json.loads(a.store.read_text(encoding="utf-8")) if a.store.exists() else {}
    stats = update(store, rows(a.export_dir), a.limit)
    a.store.parent.mkdir(parents=True, exist_ok=True)
    a.store.write_text(json.dumps(store, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    (a.export_dir / "ai.json").write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(stats))
    return 1 if stats["failed"] and not stats["made"] else 0


if __name__ == "__main__":
    sys.exit(main())
