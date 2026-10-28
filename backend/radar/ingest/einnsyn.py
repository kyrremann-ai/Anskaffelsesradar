"""eInnsyn (Digdir): offentlige postjournaler. Åpent API uten nøkkel for søk.

GET https://api.einnsyn.no/enhet?orgnummer=...      oppdragsgiverens enhet i eInnsyn
GET https://api.einnsyn.no/search?administrativEnhet=...&korrespondansepartNavn=...

For kontrakter med høy eller forhøyet prioritet slår vi opp journalposter hos oppdragsgiveren
der vinneren står som avsender/mottaker. Det gir revisor en snarvei til saksdokumentene.
Bare offentlige titler, datoer og lenker lagres. Mange kommuner publiserer ikke i eInnsyn,
så manglende treff betyr ikke at det ikke finnes dokumenter.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from .. import db

API = "https://api.einnsyn.no"
WEB = "https://einnsyn.no/saksmappe?id={sm}&jid={jp}"
# Selskapsform tas bort, ellers bommer fritekstsøket på «AS» vs «A/S»
_SUFFIX = re.compile(r"\b(asa|as|a/s|ans|da|sa|ba|ks|nuf|hf|kf|iks)\.?$", re.I)
MAX_HITS = 10
# Journalposter eldre enn dette før kunngjøringen gjelder som regel noe annet
WINDOW = timedelta(days=730)


def _get(client: httpx.Client, path: str, params: dict, tries: int = 5) -> dict:
    for i in range(tries):
        r = None
        try:
            r = client.get(API + path, params=params, timeout=30)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r.json()
        except httpx.TransportError:
            pass
        after = r.headers.get("Retry-After") if r is not None else None
        time.sleep(float(after) if after and after.isdigit() else min(30, 2 ** i))
    if r is not None:
        r.raise_for_status()
    raise RuntimeError("eInnsyn svarte ikke etter flere forsøk")


def core_name(name: str) -> str:
    return _SUFFIX.sub("", (name or "").strip()).strip(" ,.")


def link(item: dict) -> str | None:
    sm = item.get("saksmappe")
    sm_ext = sm.get("externalId") if isinstance(sm, dict) else None
    jp_ext = item.get("externalId")
    return WEB.format(sm=quote(sm_ext, safe=""), jp=quote(jp_ext, safe="")) if sm_ext and jp_ext else None


def parse_items(items: list[dict], pub_date: str | None = None) -> list[dict]:
    out = []
    try:
        earliest = (date.fromisoformat(pub_date[:10]) - WINDOW).isoformat() if pub_date else ""
    except ValueError:
        earliest = ""
    for it in items:
        if it.get("entity") != "Journalpost" or (it.get("journaldato") or "9999") < earliest:
            continue
        sm = it.get("saksmappe") if isinstance(it.get("saksmappe"), dict) else {}
        out.append({"title": it.get("offentligTittel"), "date": it.get("journaldato"), "type": it.get("journalposttype"),
                    "case": sm.get("offentligTittel"), "url": link(it)})
    return out


def refresh(con, tiers=("Høy", "Forhøyet"), log=print) -> int:
    """Slår opp journalposter for prioriterte kontrakter. Returnerer antall kontrakter med treff."""
    q = f"""SELECT c.id, c.buyer_id, c.winner_names, c.pub_date FROM contracts c JOIN scores s ON s.contract_id = c.id
            WHERE s.tier IN ({','.join('?' * len(tiers))}) AND c.source != 'demo'"""
    rows = list(con.execute(q, tiers))
    enheter = {r["orgnr"]: r["enhet_id"] for r in con.execute("SELECT orgnr, enhet_id FROM einnsyn_enhet")}
    done = {r["contract_id"] for r in con.execute("SELECT contract_id FROM einnsyn WHERE at > datetime('now', '-30 days')")}
    found = 0
    with httpx.Client(headers={"User-Agent": "Anskaffelsesradar/2.0"}) as client:
        for r in rows:
            orgnr = r["buyer_id"]
            if not re.fullmatch(r"\d{9}", orgnr or ""):
                continue
            if orgnr not in enheter:
                items = _get(client, "/enhet", {"orgnummer": orgnr}).get("items") or []
                enheter[orgnr] = items[0]["id"] if items else ""
                with db.tx(con):
                    con.execute("INSERT OR REPLACE INTO einnsyn_enhet VALUES (?, ?)", (orgnr, enheter[orgnr]))
            if not enheter[orgnr] or r["id"] in done:
                continue
            hits = []
            for name in json.loads(r["winner_names"] or "[]")[:3]:
                if not (n := core_name(name)) or len(n) < 3:
                    continue
                j = _get(client, "/search", {"administrativEnhet": enheter[orgnr], "korrespondansepartNavn": f'"{n}"',
                                             "limit": MAX_HITS, "expand": "saksmappe"})
                hits += parse_items(j.get("items") or [], r["pub_date"])
                time.sleep(0.2)
            # Samme journalpost kan treffe flere vinnere; nyeste først
            hits = sorted({h["url"] or h["title"]: h for h in hits}.values(), key=lambda h: h["date"] or "", reverse=True)
            with db.tx(con):
                con.execute("INSERT OR REPLACE INTO einnsyn VALUES (?, ?, ?)",
                            (r["id"], json.dumps(hits[:MAX_HITS], ensure_ascii=False), db.now()))
            found += bool(hits)
    log(f"eInnsyn: {found} prioriterte kontrakter med journalposter ({sum(1 for v in enheter.values() if v)} oppdragsgivere i eInnsyn)")
    return found


def for_contract(con, cid: str) -> list[dict] | None:
    r = con.execute("SELECT items FROM einnsyn WHERE contract_id=?", (cid,)).fetchone()
    return json.loads(r["items"]) if r else None
