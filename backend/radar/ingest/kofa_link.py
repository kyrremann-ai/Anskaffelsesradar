"""Kobler KOFA-saker til Doffin-kunngjøringer via Doffin-søket.

De fleste klager gjelder konkurranser som ennå ikke har en tildeling, så kobling bare mot
tildelinger (kofa.match) finner få saker. Her søkes Doffin per sak med oppdragsgiver +
gjenstanden fra sammendraget, i tre år før avgjørelsen. Beste treff hos samme oppdragsgiver
velges, med tildeling foran konkurranse. Resultatet caches i data/cache/kofa_doffin.json og
brukes av kofa_eval (konkurransekunngjøringer gir estimat, frist og prosedyre, men ikke tilbud).

    python -m radar.ingest.kofa_link [--since 2024-01-01]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

from ..db import norm_name
from . import doffin

STOP = set("""anskaffelse anskaffelser anskaffelsen kontrakt kontrakten kontrakter rammeavtale rammeavtaler levering
leveranse tjenester tjeneste konkurranse konkurransen innklagede klager klagenemnda nemnda kommune kommunes knyttet
gjelder etter mellom under samt eller annet andre ifølge intensjonskunngjøring kunngjøring kunngjorde inngåelse kjøp
avtale avtaler gjennomførte åpen anbudskonkurranse tilbudskonkurranse forhandling forutgående begrenset prosjektet
prosjekt også blant annet skulle hvor ulike ulik flere""".split())
RESULT = {"RESULT", "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT"}
INTENT = "ANNOUNCEMENT_OF_INTENT"


def tokens(s: str) -> set[str]:
    return {w for w in norm_name(s).split() if len(w) > 3 and w not in STOP and not w.isdigit()}


def subject(summary: str) -> str:
    """Gjenstanden for anskaffelsen fra første setning i sammendraget."""
    first = re.split(r"(?<=[a-zæøå])\.\s", summary, maxsplit=1)[0]
    m = re.search(r"\b(?:av|for|om|på|til)\s+(.{4,140})$", first)
    return m.group(1) if m else first


def _buyer_ok(hit: dict, innklaget: str) -> bool:
    kn = norm_name(innklaget)
    kt = set(kn.split())
    for b in hit.get("buyer") or []:
        bn = norm_name(b.get("name"))
        if bn and (bn == kn or bn in kn or kn in bn or (len(kt & set(bn.split())) >= 1 and len(kt) <= 2 and kt <= set(bn.split()))):
            return True
    return False


def best_hit(hits: list[dict], case: dict) -> tuple[dict | None, float]:
    kt = tokens(subject(case["tittel"]))
    best, score = None, 0.0
    for h in hits:
        if not _buyer_ok(h, case["innklaget"]):
            continue
        ht = tokens(f"{h.get('heading') or ''}")
        if not ht or not kt:
            continue
        s = len(kt & ht) / min(len(kt), len(ht))
        types = set(h.get("allTypes") or []) | {h.get("type")}
        # Tildeling foran konkurranse, konkurranse foran markedsdialog, ved likt treff
        s += 0.05 if types & RESULT or INTENT in types else -0.1 if "ADVISORY_NOTICE" in types else 0
        if s > score:
            best, score = h, s
    return best, score


def link(cases: list[dict], cache: Path, api_key: str = "", min_score: float = 0.5, log=print) -> dict[str, dict]:
    out: dict[str, dict] = json.loads(cache.read_text()) if cache.exists() else {}
    with httpx.Client(headers=doffin._headers(api_key)) as client:
        for i, c in enumerate(cases, 1):
            if c["saksnr"] in out or not c.get("tittel"):
                continue
            d = date.fromisoformat(c["dato"])
            span = {"numHitsPerPage": 100, "issueDateTo": d.isoformat(), "issueDateFrom": (d - timedelta(days=3 * 365)).isoformat()}
            words = sorted(tokens(subject(c["tittel"])), key=len, reverse=True)
            # Søket krever alle ordene: prøv gjenstanden smalt, så bredere, så alt fra oppdragsgiveren
            h, s = None, 0.0
            for q in dict.fromkeys(filter(None, [" ".join(words[:2]), " ".join(words[:1]), c["innklaget"]])):
                j = doffin._get(client, {"searchString": q, **span})
                time.sleep(doffin.PAUSE)
                h, s = best_hit(j.get("hits") or [], c)
                if s >= min_score:
                    break
            out[c["saksnr"]] = {"score": round(s, 2), "hit": h if s >= min_score else None}
            if i % 25 == 0:
                cache.write_text(json.dumps(out, ensure_ascii=False))
                log(f"  {i}/{len(cases)}: {sum(1 for v in out.values() if v['hit'])} koblet")
    cache.write_text(json.dumps(out, ensure_ascii=False))
    return out


def hit_row(h: dict) -> dict:
    """Kontraktrad (samme felt som contracts) fra et Doffin-treff, også uten vinner."""
    row = doffin.parse_hit(h)
    if row is None:  # konkurranse: ingen vinner ennå
        row = doffin.parse_hit({**h, "lots": [{"winner": [{"name": "_ingen"}]}]})
        row.update(winner_ids=[], winner_names=[])
    iss, dl = doffin._date(h.get("issueDate")), doffin._date(h.get("deadline"))
    if iss and dl and dl > iss:
        row["tender_duration_days"] = (dl - iss).days
    return row


if __name__ == "__main__":
    from ..config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--kofa", type=Path, default=settings.import_dir / "kofa.csv")
    ap.add_argument("--since", default="2024-01-01")
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(a.kofa.open(encoding="utf-8"), delimiter=";") if r["dato"] >= a.since]
    res = link(rows, settings.data_dir / "cache" / "kofa_doffin.json", settings.doffin_api_key)
    n = [v for k, v in res.items() if v["hit"]]
    print(f"Koblet {len(n)} av {len(res)} saker til Doffin")
