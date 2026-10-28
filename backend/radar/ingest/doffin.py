"""Doffin Public API v2 (DFØ). Krever gratis nøkkel fra DFØs API-portal (Azure APIM).

GET https://betaapi.doffin.no/public/v2/search  med header Ocp-Apim-Subscription-Key.
Brukes til «Doffin live»: nyeste kunngjøringer med tidlige varselsignaler, også under EØS-terskel.
Svarformatet parses defensivt, siden API-et fortsatt er merket beta.
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

SEARCH_URL = "https://betaapi.doffin.no/public/v2/search"
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
    if not api_key:
        return {"total": 0, "hits": [], "error": "DOFFIN_API_KEY mangler. Hent gratis nøkkel hos DFØ."}
    params = {"numHitsPerPage": per_page, "page": page, "sortBy": "PUBLICATION_DATE_DESC"}
    if q:
        params["searchString"] = q
    r = httpx.get(SEARCH_URL, params=params, headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=30)
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
