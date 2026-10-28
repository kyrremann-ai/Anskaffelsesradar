"""Observasjonsenhet: hva én rad er, og hvilke rader som er samme tildeling.

- Én rad i `contracts` er én kunngjøring (TED eller Doffin).
- En *kontrakt* i tellingene er en kunngjort tildeling som ikke er et duplikat. Delkontrakter (lots) i
  samme kunngjøring er én kontrakt.
- Et *anskaffelsesforløp* er alle kunngjøringer med samme prosedyre-ID (TED BT-04). Doffin-kunngjøringer
  som er koblet til TED arver TED-forløpet.
- Intensjonskunngjøringer, endringer og avlysninger er egne kunngjøringstyper og telles ikke som kontrakter.

Koblingene bygges på nytt hver kjøring og lagres i tabellen `observasjon`, med regelen som slo dem sammen.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date

from .db import set_meta, tx

DOFFIN_TED_DAYS = 14  # Doffin og TED publiseres normalt samme dag; 14 dager gir rom for forsinkelser
ORGNR = re.compile(r"^\d{9}$")


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _title(t) -> str:
    return re.sub(r"[^a-z0-9æøå]+", " ", str(t or "").lower()).strip()


def value_note(r: dict) -> str | None:
    """Verdikontroll: grunn til at verdien ikke bør brukes i overskridelse og summer, ellers None."""
    est, award = r.get("est_value"), r.get("award_value")
    if (r.get("raw") or {}).get("value_note"):
        return r["raw"]["value_note"]
    if award is not None and 0 < award < 1000:
        return "Tildelt verdi under 1 000 kr (mulig enhetsfeil)"
    if est and award and (award / est > 20 or award / est < 0.05):
        return "Tildelt verdi avviker mer enn 20 ganger fra estimatet"
    return None


def link(con, log=print) -> dict:
    rows = []
    for r in con.execute("SELECT id, source, pub_date, buyer_id, title, winner_ids, est_value, award_value, framework, raw FROM contracts"):
        r = dict(r)
        r["raw"] = json.loads(r["raw"] or "{}") or {}
        r["w"] = set(json.loads(r["winner_ids"] or "[]"))
        r["d"] = _d(r["pub_date"])
        r["type"] = r["raw"].get("notice_type") or "tildeling"
        if r["source"] == "doffin" and "notice_type" not in r["raw"]:  # rader fra før typen ble lagret
            from .ingest.doffin import notice_type
            r["type"] = notice_type(r["raw"].get("doffin_type"))
        r["proc"] = r["raw"].get("procedure_id") or r["id"]
        r["dup"] = r["rule"] = None
        rows.append(r)
    by_id = {r["id"]: r for r in rows}

    # 1) TED: samme forløp, samme leverandører og samme (eller manglende) verdi = rettelse eller ny publisering.
    #    Senest publiserte kunngjøring telles. Ulike leverandører i samme forløp er egne kontrakter (delkontrakter, DPS).
    groups = defaultdict(list)
    for r in rows:
        if r["source"] == "ted" and r["raw"].get("procedure_id"):
            groups[(r["proc"], frozenset(r["w"]))].append(r)
    for g in groups.values():
        g.sort(key=lambda r: (r["d"] or date.min, r["id"]))
        keep = g[-1]
        for r in g[:-1]:
            if r["award_value"] is None or keep["award_value"] is None or r["award_value"] == keep["award_value"]:
                r["dup"], r["rule"] = keep["id"], "Samme TED-forløp og leverandør (rettelse eller ny publisering)"

    # 2) Doffin ↔ TED: samme oppdragsgiver, minst én felles leverandør, publisert innen 14 dager. TED telles,
    #    fordi TED-kunngjøringen har tilbudstall og tildelt verdi.
    ted_by_buyer = defaultdict(list)
    for r in rows:
        if r["source"] == "ted" and not r["dup"]:
            ted_by_buyer[r["buyer_id"]].append(r)
    ambiguous = 0
    for r in rows:
        if r["source"] != "doffin" or r["type"] not in ("tildeling", "endring") or not r["w"] or not r["d"]:
            continue
        cand = [t for t in ted_by_buyer.get(r["buyer_id"], []) if t["w"] & r["w"] and t["d"]
                and abs((t["d"] - r["d"]).days) <= DOFFIN_TED_DAYS]
        if cand:
            best = min(cand, key=lambda t: (abs((t["d"] - r["d"]).days), -len(t["w"] & r["w"])))
            ambiguous += len({t["proc"] for t in cand}) > 1
            r["dup"], r["rule"], r["proc"] = best["id"], "Samme tildeling i Doffin og TED", best["proc"]

    # 3) Doffin-endringer: samme oppdragsgiver, tittel og leverandører som en tidligere Doffin-tildeling
    doffin_key = {(r["buyer_id"], _title(r["title"]), frozenset(r["w"])): r for r in rows
                  if r["source"] == "doffin" and r["type"] == "tildeling" and not r["dup"]}
    for r in rows:
        if r["source"] == "doffin" and r["type"] == "endring" and not r["dup"]:
            if o := doffin_key.get((r["buyer_id"], _title(r["title"]), frozenset(r["w"]))):
                r["dup"], r["rule"], r["proc"] = o["id"], "Endringskunngjøring for samme Doffin-tildeling", o["proc"]

    # Forløp følger kunngjøringen som telles
    for r in rows:
        if r["dup"]:
            r["proc"] = by_id[r["dup"]]["proc"]
        r["note"] = value_note(r)

    # Tilbudsfrist: fra konkurransekunngjøringen i samme forløp til fristen
    cn = {c["procedure_id"]: dict(c) for c in con.execute("SELECT * FROM ted_competition")}
    days = {}
    for r in rows:
        if (c := cn.get(r["proc"])) and (a := _d(c["dispatch_date"])) and (b := _d(c["deadline"])) and b >= a:
            r["cn"], r["cn_acc"] = c["publication_number"], c["accelerated"]
            days[r["id"]] = (b - a).days
        else:
            r["cn"] = r["cn_acc"] = None

    with tx(con):
        con.execute("DELETE FROM observasjon")
        con.executemany("INSERT INTO observasjon VALUES (?,?,?,?,?,?,?,?)",
                        [(r["id"], r["type"], r["proc"], r["dup"], r["rule"], r["note"], r["cn"], r["cn_acc"]) for r in rows])
        con.executemany("UPDATE contracts SET tender_duration_days=? WHERE id=?", [(v, k) for k, v in days.items()])

    counted = [r for r in rows if r["type"] == "tildeling" and not r["dup"]]
    winners = [w for r in counted for w in r["w"]]
    valid = [w for w in winners if ORGNR.match(str(w))]
    known = {r["orgnr"]: r["kind"] for r in con.execute("SELECT orgnr, json_extract(data, '$.kind') AS kind FROM brreg_entities")}
    found = [w for w in valid if known.get(w) not in (None, "ukjent")]
    stats = {
        "kunngjøringer": len(rows),
        "per_kilde": dict(Counter(r["source"] for r in rows)),
        "per_type": dict(Counter(r["type"] for r in rows)),
        "duplikater": dict(Counter(r["rule"] for r in rows if r["dup"])),
        "duplikater_tvetydige": ambiguous,
        "kontrakter": len(counted),
        "kontrakter_per_kilde": dict(Counter(r["source"] for r in counted)),
        "forløp": len({r["proc"] for r in counted}),
        "verdi": {
            "usikker": dict(Counter(r["note"] for r in counted if r["note"])),
            "med_tildelt_verdi": sum(r["award_value"] is not None for r in counted),
            "bare_estimat": sum(r["award_value"] is None and r["est_value"] is not None for r in counted),
            "uten_verdi": sum(r["award_value"] is None and r["est_value"] is None for r in counted),
            "rammeavtaler": sum(bool(r["framework"]) for r in counted),
        },
        "tilbudsfrist_kjent": sum(r["id"] in days for r in counted),
        "registertreff": {
            "leverandører": len(winners), "gyldig_orgnr": len(valid), "funnet_i_brreg": len(found),
            "kontrakter_uten_leverandør": sum(not r["w"] for r in counted),
        },
    }
    with tx(con):
        set_meta(con, "observasjonsenhet", stats)
    log(f"Observasjonsenhet: {stats['kunngjøringer']} kunngjøringer → {stats['kontrakter']} kontrakter i "
        f"{stats['forløp']} forløp; {sum(stats['duplikater'].values())} duplikater slått sammen")
    return stats
