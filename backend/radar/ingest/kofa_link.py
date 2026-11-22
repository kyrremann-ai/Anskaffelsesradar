"""Kobler KOFA-saker til Doffin-kunngjøringer via Doffin-søket.

De fleste klager gjelder konkurranser som ennå ikke har en tildeling, så kobling bare mot
tildelinger (kofa.match) finner få saker. Her søkes Doffin per sak med oppdragsgiver +
gjenstanden fra sammendraget, i tre år før avgjørelsen. Beste treff hos samme oppdragsgiver
velges, med tildeling foran konkurranse. Resultatet caches i data/cache/kofa_doffin.json og
brukes av kofa_eval (konkurransekunngjøringer gir estimat, frist og prosedyre, men ikke tilbud).

Hver kobling får en konfidens (0–1) fra flere uavhengige signaler, så klagerisiko kan trenes bare på sikre treff:
- oppdragsgiver: samme orgnr (innklagedes orgnr slås opp i Enhetsregisteret når KOFA ikke oppgir det),
  ellers likt eller delvis likt navn. Navnesøk alene ga kunstig høy kommune-effekt: kommuner har entydige navn,
  statlige etater har ikke.
- gjenstand: ordoverlapp mellom sammendraget og kunngjøringens overskrift
- tid: hvor lenge før avgjørelsen kunngjøringen kom (de fleste klager avgjøres innen to år)
- referanse: Doffin-ID-en står i KOFA-teksten (sikker kobling)

    python -m radar.ingest.kofa_link [--since 2024-01-01] [--no-brreg]
    python -m radar.ingest.kofa_link --rescore      # ny konfidens for lagrede koblinger, uten nye Doffin-kall
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


def _buyer_match(hit: dict, case: dict) -> tuple[float, str]:
    """Hvor sikkert oppdragsgiveren i kunngjøringen er den innklagede: (poeng, grunnlag)."""
    org = re.sub(r"\D", "", str(case.get("innklaget_orgnr") or ""))
    buyers = hit.get("buyer") or []
    if len(org) == 9 and any(re.sub(r"\D", "", str(b.get("organizationId") or "")) == org for b in buyers):
        return 0.45, "orgnr"
    kn = norm_name(case.get("innklaget"))
    if kn and any(norm_name(b.get("name")) == kn for b in buyers):
        return 0.35, "navn"
    return (0.2, "delvis navn") if _buyer_ok(hit, case.get("innklaget") or "") else (0.0, "")


def confidence(hit: dict | None, case: dict, subject_score: float) -> tuple[float, list[str]]:
    """Konfidens for at kunngjøringen er den KOFA-saken gjelder. Terskel for trening: KOFA_MIN_CONFIDENCE (0,7).
    Eksempler: orgnr + noe ordoverlapp + under to år = 0,7. Bare delvis navnelikhet krever full ordoverlapp."""
    if not hit:
        return 0.0, []
    text = f"{case.get('tittel') or ''} {case.get('url') or ''}"
    hid = str(hit.get("id") or "")
    if re.fullmatch(r"20\d\d-\d{4,}", hid) and re.search(rf"(?<![\d-]){re.escape(hid)}(?!\d)", text):
        return 1.0, ["Doffin-ID i KOFA-teksten"]
    b, basis = _buyer_match(hit, case)
    if not b:
        return 0.0, []
    out, why = b, [f"oppdragsgiver ({basis})"]
    subj = max(0.0, min(1.0, subject_score))
    out += 0.35 * subj
    why.append(f"gjenstand {subj:.0%}")
    try:
        days = (date.fromisoformat(case["dato"][:10]) - date.fromisoformat(str(hit.get("issueDate"))[:10])).days
    except (KeyError, TypeError, ValueError):
        days = None
    if days is not None and 0 <= days <= 730:
        out += 0.15
        why.append("under 2 år før avgjørelsen")
    elif days is not None and 730 < days <= 3 * 365:
        out += 0.05
        why.append("2–3 år før avgjørelsen")
    return round(min(out, 1.0), 2), why


def resolve_orgnr(client: httpx.Client, name: str, cache: dict) -> str | None:
    """Innklagedes orgnr fra Enhetsregisteret: bare ved eksakt navnelikhet (normalisert), ellers ingen."""
    key = norm_name(name)
    if not key:
        return None
    if key in cache:
        return cache[key]
    org = None
    try:
        r = client.get("https://data.brreg.no/enhetsregisteret/api/enheter", params={"navn": name, "size": 20}, timeout=20)
        if r.status_code == 200:
            hits = [e for e in (r.json().get("_embedded") or {}).get("enheter", []) if norm_name(e.get("navn")) == key]
            if len(hits) == 1:  # flere med samme navn: ikke gjett
                org = hits[0].get("organisasjonsnummer")
    except (httpx.HTTPError, ValueError):
        return None  # ikke cache nettfeil
    cache[key] = org
    return org


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


def link(cases: list[dict], cache: Path, api_key: str = "", min_score: float = 0.5, log=print,
         brreg_lookup: bool = True) -> dict[str, dict]:
    out: dict[str, dict] = json.loads(cache.read_text()) if cache.exists() else {}
    org_cache_path = cache.with_name("kofa_orgnr.json")
    org_cache: dict = json.loads(org_cache_path.read_text()) if org_cache_path.exists() else {}
    with httpx.Client(headers=doffin._headers(api_key)) as client, \
            httpx.Client(headers={"Accept": "application/json", "User-Agent": "Anskaffelsesradar/2.0"}) as brreg_client:
        for i, c in enumerate(cases, 1):
            if c["saksnr"] in out or not c.get("tittel"):
                continue
            if brreg_lookup and not c.get("innklaget_orgnr"):
                c = {**c, "innklaget_orgnr": resolve_orgnr(brreg_client, c.get("innklaget") or "", org_cache) or ""}
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
            conf, why = confidence(h if s >= min_score else None, c, s)
            out[c["saksnr"]] = {"score": round(s, 2), "hit": h if s >= min_score else None, "confidence": conf, "basis": why,
                                "innklaget_orgnr": c.get("innklaget_orgnr") or None}
            if i % 25 == 0:
                cache.write_text(json.dumps(out, ensure_ascii=False))
                org_cache_path.write_text(json.dumps(org_cache, ensure_ascii=False))
                log(f"  {i}/{len(cases)}: {sum(1 for v in out.values() if v['hit'])} koblet")
    cache.write_text(json.dumps(out, ensure_ascii=False))
    org_cache_path.write_text(json.dumps(org_cache, ensure_ascii=False))
    return out


def rescore(cases: list[dict], cache: Path, brreg_lookup: bool = True) -> dict[str, dict]:
    """Ny konfidens for koblinger som allerede er lagret (f.eks. fra før konfidens fantes). Ingen nye Doffin-kall."""
    out = json.loads(cache.read_text())
    by_nr = {c["saksnr"]: c for c in cases}
    org_cache_path = cache.with_name("kofa_orgnr.json")
    org_cache: dict = json.loads(org_cache_path.read_text()) if org_cache_path.exists() else {}
    with httpx.Client(headers={"Accept": "application/json", "User-Agent": "Anskaffelsesradar/2.0"}) as bc:
        for nr, v in out.items():
            c = by_nr.get(nr)
            if not c or not v.get("hit"):
                continue
            if brreg_lookup and not c.get("innklaget_orgnr"):
                c = {**c, "innklaget_orgnr": resolve_orgnr(bc, c.get("innklaget") or "", org_cache) or ""}
            _, s = best_hit([v["hit"]], c)
            v["confidence"], v["basis"] = confidence(v["hit"], c, s or v.get("score", 0))
            v["innklaget_orgnr"] = c.get("innklaget_orgnr") or None
    cache.write_text(json.dumps(out, ensure_ascii=False))
    org_cache_path.write_text(json.dumps(org_cache, ensure_ascii=False))
    return out


def confident(link_cache: dict, min_confidence: float, log=print) -> dict[str, dict]:
    """Koblinger som er sikre nok til trening. Lagrede koblinger uten konfidens (fra før) brukes bare når
    ingen har konfidens ennå, med advarsel: kjør --rescore."""
    scored = {k: v for k, v in link_cache.items() if v.get("hit") and "confidence" in v}
    if not scored:
        legacy = {k: v for k, v in link_cache.items() if v.get("hit")}
        if legacy:
            log(f"KOFA-koblinger uten konfidens ({len(legacy)}): brukes alle. Kjør python -m radar.ingest.kofa_link --rescore.")
        return legacy
    keep = {k: v for k, v in scored.items() if v["confidence"] >= min_confidence}
    log(f"KOFA-koblinger: {len(keep)} av {len(scored)} har konfidens ≥ {min_confidence:g}")
    return keep


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
    ap.add_argument("--rescore", action="store_true", help="ny konfidens for lagrede koblinger")
    ap.add_argument("--no-brreg", action="store_true", help="ikke slå opp innklagedes orgnr i Enhetsregisteret")
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(a.kofa.open(encoding="utf-8"), delimiter=";") if r["dato"] >= a.since]
    cache = settings.data_dir / "cache" / "kofa_doffin.json"
    res = rescore(rows, cache, not a.no_brreg) if a.rescore else link(rows, cache, settings.doffin_api_key, brreg_lookup=not a.no_brreg)
    n = [v for v in res.values() if v.get("hit")]
    sure = [v for v in n if v.get("confidence", 0) >= settings.kofa_min_confidence]
    print(f"Koblet {len(n)} av {len(res)} saker til Doffin, {len(sure)} med konfidens ≥ {settings.kofa_min_confidence:g}")
