"""Doffin Public API v2 (DFØ). Krever gratis nøkkel fra DFØs API-portal (Azure APIM).

GET https://api.doffin.no/public/v2/search  (30 kall/min) med header Ocp-Apim-Subscription-Key.
GET https://api.doffin.no/public/v2/download/{id}  (10 kall/min, eForms-XML) brukes ikke i bulk.

To bruk:
- refresh(): tildelinger (RESULT) som kontrakter. Søket gir kjøper, vinnere og antall tilbud,
  så nasjonale kunngjøringer under EØS-terskel (som TED mangler) kommer med.
- latest(): «Doffin live», nyeste kunngjøringer med tidlige varselsignaler.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import httpx

SEARCH_URL = "https://api.doffin.no/public/v2/search"
NOTICE_URL = "https://doffin.no/notices/{id}"


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return default


def _date(s) -> date | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def signals(hit: dict, buyer_stats: dict[str, dict]) -> list[dict]:
    out = []
    kind = str(hit.get("type") or "").upper()
    text = f"{hit.get('heading') or ''} {hit.get('description') or ''}".lower()
    if "INTENT" in kind or "intensjon" in text or "direkte anskaff" in text or "direkte tildel" in text:
        out.append({"key": "planned_direct", "label": "Planlagt direkte tildeling"})
    pub, deadline = _date(hit.get("publicationDate") or hit.get("issueDate")), _date(hit.get("deadline"))
    if pub and deadline and 0 <= (deadline - pub).days < 15:
        out.append({"key": "short_deadline", "label": f"Kort frist ({(deadline - pub).days} dager)"})
    for b in hit.get("buyer") or []:
        st = buyer_stats.get(str(b.get("organizationId") or ""))
        if st and st.get("high_share", 0) >= 0.1:
            out.append({"key": "buyer_history", "label": f"Oppdragsgiver med {st['high_share']:.0%} høy prioritet historisk"})
            break
    return out


def latest(api_key: str, q: str = "", page: int = 1, per_page: int = 50, buyer_stats: dict | None = None) -> dict:
    params = {"numHitsPerPage": per_page, "page": page, "sortBy": "PUBLICATION_DATE_DESC"}
    if q:
        params["searchString"] = q
    r = httpx.get(SEARCH_URL, params=params, headers=_headers(api_key), timeout=30)
    if r.status_code == 401:
        return {"total": 0, "hits": [], "error": "Doffin avviste kallet. Sett DOFFIN_API_KEY (gratis nøkkel hos DFØ)."}
    r.raise_for_status()
    j = r.json()
    hits = []
    for h in j.get("hits", []):
        buyers = h.get("buyer") or []
        ev = h.get("estimatedValue") or {}
        hits.append({
            "id": h.get("id"), "url": NOTICE_URL.format(id=h.get("id")), "title": _first(h, "heading", "title"),
            "buyer": ", ".join(b.get("name", "") for b in buyers), "buyer_orgnr": [b.get("organizationId") for b in buyers],
            "type": h.get("type"), "status": h.get("status"),
            "pub_date": str(_first(h, "publicationDate", "issueDate", default=""))[:10],
            "deadline": str(h.get("deadline") or "")[:10] or None,
            "est_value": ev.get("amount") if isinstance(ev, dict) else ev,
            "flags": signals(h, buyer_stats or {}),
        })
    return {"total": j.get("numHitsTotal", len(hits)), "hits": hits}


# Kunngjøringstyper som betyr at kontrakten er tildelt
RESULT_TYPES = ["RESULT", "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT"]
NATURE = {"WORKS": "Bygg og anlegg", "SUPPLIES": "Varer", "SERVICES": "Tjenester"}
PER_PAGE = 100
# 30 kall i minuttet
PAUSE = 2.1


def _headers(api_key: str) -> dict:
    # Uten nøkkel her kan en proxy legge på headeren (slik skymiljøet gjør med hemmeligheter)
    h = {"User-Agent": "Anskaffelsesradar/2.0"}
    if api_key:
        h["Ocp-Apim-Subscription-Key"] = api_key
    return h


class RateLimited(RuntimeError):
    """Doffin svarte 429 også etter at vi ventet. Det vi har hentet så langt brukes likevel."""


def _get(client: httpx.Client, params: dict, tries: int = 6) -> dict:
    for i in range(tries):
        r = None
        try:
            r = client.get(SEARCH_URL, params=params, timeout=60)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r.json()
        except httpx.TransportError:
            pass
        after = r.headers.get("Retry-After") if r is not None else None
        time.sleep(float(after) if after and after.isdigit() else min(60, 2 ** i * 2))
    if r is not None and r.status_code == 429:
        raise RateLimited("Doffin: 429 Too Many Requests etter flere forsøk")
    if r is not None:
        r.raise_for_status()
    raise RuntimeError("Doffin svarte ikke etter flere forsøk")


def search_results(api_key: str, since: str, until: date | None = None, log=print) -> list[dict]:
    """Alle tildelinger publisert fra `since`. Søket deles i måneder så vi holder oss under
    grensen for hvor mange treff som kan blas gjennom (numHitsAccessible)."""
    until = until or date.today()
    start = date.fromisoformat(since)
    out: dict[str, dict] = {}
    with httpx.Client(headers=_headers(api_key)) as client:
        while start <= until:
            end = min(until, (start.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
            page = 1
            while True:
                params = {"numHitsPerPage": PER_PAGE, "page": page, "sortBy": "PUBLICATION_DATE_ASC",
                          "type": RESULT_TYPES, "issueDateFrom": start.isoformat(), "issueDateTo": end.isoformat()}
                try:
                    j = _get(client, params)
                except RateLimited as e:
                    log(f"  {e}; bruker de {len(out)} som er hentet, resten tas neste natt")
                    return list(out.values())
                hits = j.get("hits") or []
                for h in hits:
                    out[h["id"]] = h
                time.sleep(PAUSE)
                if len(hits) < PER_PAGE or page * PER_PAGE >= (j.get("numHitsAccessible") or j.get("numHitsTotal") or 0):
                    break
                page += 1
            log(f"  Doffin {start:%Y-%m}: {len(out)} tildelinger så langt")
            start = end + timedelta(days=1)
    return list(out.values())


def parse_hit(h: dict) -> dict | None:
    """Gjør et søketreff om til samme kontraktrad som TED gir."""
    from .ted import NUTS, SECTOR  # samme regioner og sektorer som TED

    buyers = h.get("buyer") or []
    b = buyers[0] if buyers else {}
    winners: dict[str, str] = {}
    for lot in h.get("lots") or []:
        for w in lot.get("winner") or []:
            key = str(w.get("organizationId") or w.get("name") or "").replace(" ", "")
            if key:
                winners.setdefault(key, w.get("name") or key)
    if not winners:
        return None  # tildeling uten oppgitt vinner gir ikke noe å vurdere
    cpv = next(iter(h.get("cpvCodes") or []), None)
    ev = h.get("estimatedValue") or {}
    est = ev.get("amount") if isinstance(ev, dict) and (ev.get("currencyCode") or "NOK") == "NOK" else None
    loc = next(iter(h.get("locationId") or []), "") or ""
    buyer_id = str(b.get("organizationId") or b.get("name") or "ukjent").replace(" ", "")
    types = set(h.get("allTypes") or []) | {h.get("type")}
    return {
        "id": f"doffin-{h['id']}", "source": "doffin", "url": NOTICE_URL.format(id=h["id"]),
        "title": h.get("heading"), "pub_date": str(_first(h, "publicationDate", "issueDate", default=""))[:10] or None,
        "buyer_id": "".join(ch for ch in buyer_id if ch.isalnum() or ch == "-").lower(), "buyer": b.get("name"),
        "region": NUTS.get(loc[:5], "Ukjent"), "sector": SECTOR.get((cpv or "")[:2], "Annet"), "cpv": cpv,
        "nature": "Annet",
        # Søket oppgir ikke prosedyre; varsel om intensjon betyr planlagt direkte tildeling
        "method": "Direkte tildeling" if "ANNOUNCEMENT_OF_INTENT" in types else "Annet",
        "accelerated": 0, "justification": None, "framework": 0,
        # Intensjonskunngjøringer oppgir ikke tilbud. Tilbudstallet tas bare med når kunngjøringen oppgir det.
        "num_bidders": h.get("receivedTenders") or None, "est_value": est, "award_value": None,
        "tender_duration_days": None, "contract_months": None,
        "winner_ids": list(winners), "winner_names": list(winners.values()), "winner_size": None,
        "raw": {"doffin_type": h.get("type"), "notice_type": notice_type(h.get("type")), "location": loc},
    }


def notice_type(t: str | None) -> str:
    """Doffins kunngjøringstype som tildeling, intensjon, endring eller avlyst."""
    t = str(t or "").upper()
    return ("intensjon" if "INTENT" in t else "endring" if t.startswith("CHANGE") else
            "avlyst" if t.startswith("CANCELLED") else "tildeling")


def refresh(con, api_key: str, since: str, log=print) -> list[dict]:
    """Nye Doffin-tildelinger. Alle lagres; kunngjøringer som også finnes i TED kobles i observasjon.link."""
    raws = [json.loads(r["raw"] or "{}") or {} for r in con.execute("SELECT raw FROM contracts WHERE source='doffin'")]
    known = {r["id"] for r in con.execute("SELECT id FROM contracts WHERE source='doffin'")}
    # Bare siste måneder hvis vi har hentet før: færre kall, og under grensen på 30 kall i minuttet.
    # Rader fra før kunngjøringstype ble lagret (og duplikater ble droppet ved import) gir full henting én gang.
    last = con.execute("SELECT MAX(pub_date) AS d FROM contracts WHERE source='doffin'").fetchone()["d"]
    if last and (d := _date(last)) and all("notice_type" in r for r in raws):
        since = max(since, (d - timedelta(days=45)).isoformat())
    else:
        known = set()
    rows = [row for h in search_results(api_key, since, log=log) if f"doffin-{h.get('id')}" not in known and (row := parse_hit(h))]
    log(f"Doffin: {len(rows)} nye kunngjøringer")
    return rows
