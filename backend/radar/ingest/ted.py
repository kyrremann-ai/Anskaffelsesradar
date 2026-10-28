"""TED (Tenders Electronic Daily): norske tildelingskunngjøringer.

Alle felt hentes fra TED Search API v3 (åpent, uten nøkkel), 250 kunngjøringer per kall:
   POST https://api.ted.europa.eu/v3/notices/search

eForms-XML per kunngjøring (ted.europa.eu/en/notice/{nr}/xml) står bak en bot-sperre og
lastes ikke ned automatisk. Ligger XML-en allerede i data/ted_xml/, brukes den.
eForms er standard for alle TED-kunngjøringer fra 2024, så 2025+ er dekket.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx

from ..db import tx

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_URL = "https://ted.europa.eu/en/notice/-/detail/{pub}"
CAN_TYPES = "can-standard can-social can-desg can-tran"

NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
    "efext": "http://data.europa.eu/p27/eforms-ubl-extensions/1",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}

PROCEDURE = {
    "open": "Åpen", "restricted": "Begrenset", "neg-w-call": "Forhandling", "comp-dial": "Forhandling",
    "innovation": "Forhandling", "neg-wo-call": "Direkte tildeling", "oth-single": "Annet", "oth-mult": "Annet",
}
NATURE = {"works": "Bygg og anlegg", "supplies": "Varer", "services": "Tjenester"}
SIZE = {"micro": "Mikro", "small": "Liten", "medium": "Mellomstor", "large": "Stor", "sme": "SMB"}

# NUTS 3 (2021 og 2024) -> region
NUTS = {
    "NO081": "Oslo", "NO082": "Viken", "NO083": "Akershus", "NO084": "Buskerud", "NO085": "Østfold",
    "NO020": "Innlandet", "NO091": "Vestfold og Telemark", "NO093": "Vestfold", "NO094": "Telemark",
    "NO092": "Agder", "NO0A1": "Rogaland", "NO0A2": "Vestland", "NO0A3": "Møre og Romsdal", "NO060": "Trøndelag",
    "NO071": "Nordland", "NO074": "Troms og Finnmark", "NO072": "Troms", "NO073": "Finnmark",
}
# CPV-divisjon -> sektor (grov)
SECTOR = {
    "45": "Bygg og anlegg", "44": "Bygg og anlegg", "71": "Rådgivning og prosjektering", "72": "IT-tjenester",
    "48": "IT-tjenester", "30": "IT-tjenester", "32": "IT-tjenester", "33": "Helse", "85": "Helse",
    "60": "Transport", "34": "Transport", "63": "Transport", "09": "Energi", "65": "Energi", "31": "Energi",
    "80": "Utdanning", "22": "Utdanning", "35": "Forsvar og beredskap", "90": "Miljø og renovasjon",
    "77": "Miljø og renovasjon", "79": "Forretningstjenester", "66": "Finans og forsikring", "15": "Mat og drikke",
    "55": "Mat og drikke", "50": "Drift og vedlikehold", "39": "Inventar og utstyr", "18": "Inventar og utstyr",
}


def _wait(r: httpx.Response | None, attempt: int) -> None:
    """TED begrenser antall kall (429). Vent det API-et ber om, ellers eksponentielt."""
    after = r.headers.get("Retry-After") if r is not None else None
    time.sleep(float(after) if after and after.isdigit() else min(60, 2 ** attempt * 2))


def _post(client: httpx.Client, body: dict, tries: int = 8) -> httpx.Response:
    for i in range(tries):
        r = None
        try:
            r = client.post(SEARCH_URL, json=body, timeout=60)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r
        except httpx.TransportError:
            pass
        _wait(r, i)
    r.raise_for_status() if r is not None else None
    raise RuntimeError("TED svarte ikke etter flere forsøk")


def _t(el, path: str) -> str | None:
    x = el.find(path, NS) if el is not None else None
    return x.text.strip() if x is not None and x.text else None


def _f(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_notice(xml: bytes, pub: str, pub_date: str) -> dict | None:
    root = ET.fromstring(xml)
    orgs: dict[str, dict] = {}
    for o in root.iterfind(".//efac:Organizations/efac:Organization", NS):
        comp = o.find("efac:Company", NS)
        oid = _t(comp, "cac:PartyIdentification/cbc:ID")
        if not oid:
            continue
        orgs[oid] = {
            "name": _t(comp, "cac:PartyName/cbc:Name"),
            "orgnr": _t(comp, "cac:PartyLegalEntity/cbc:CompanyID"),
            "nuts": _t(comp, "cac:PostalAddress/cbc:CountrySubentityCode"),
            "size": _t(comp, "efbc:CompanySizeCode"),
        }
    buyer_ref = _t(root, "cac:ContractingParty/cac:Party/cac:PartyIdentification/cbc:ID")
    buyer = orgs.get(buyer_ref or "", {})
    proj = root.find("cac:ProcurementProject", NS)
    proc_code = _t(root, "cac:TenderingProcess/cbc:ProcedureCode")
    reasons = {x.get("listName"): (x.text or "").strip()
               for x in root.iterfind(".//cac:TenderingProcess/cac:ProcessJustification/cbc:ProcessReasonCode", NS)}
    cpv = _t(proj, "cac:MainCommodityClassification/cbc:ItemClassificationCode")

    # Tilbudstall: sum over delkontrakter av statistikk «tenders»
    bidders = [_f(_t(s, "efbc:StatisticsNumeric")) for s in root.iterfind(".//efac:ReceivedSubmissionsStatistics", NS)
               if _t(s, "efbc:StatisticsCode") == "tenders"]
    bidders = [b for b in bidders if b is not None]

    # Vinnere: LotTender -> TenderingParty -> Tenderer -> Organization
    parties = {}
    for tp in root.iterfind(".//efac:NoticeResult/efac:TenderingParty", NS):
        parties[_t(tp, "cbc:ID")] = [_t(t, "cbc:ID") for t in tp.iterfind("efac:Tenderer", NS)]
    winners = []
    for lt in root.iterfind(".//efac:NoticeResult/efac:LotTender", NS):
        for oid in parties.get(_t(lt, "efac:TenderingParty/cbc:ID"), []):
            if oid in orgs and oid not in winners:
                winners.append(oid)
    award = _f(_t(root, ".//efac:NoticeResult/cbc:TotalAmount"))
    if award is None:
        amounts = [_f(_t(lt, "cac:LegalMonetaryTotal/cbc:PayableAmount")) for lt in root.iterfind(".//efac:NoticeResult/efac:LotTender", NS)]
        award = sum(a for a in amounts if a) or None
    est = _f(_t(proj, "cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount"))
    if est is None:
        ests = [_f(_t(lot, "cac:ProcurementProject/cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount"))
                for lot in root.iterfind("cac:ProcurementProjectLot", NS)]
        est = sum(e for e in ests if e) or None
    dur = root.find(".//cac:ProcurementProjectLot/cac:ProcurementProject/cac:PlannedPeriod/cbc:DurationMeasure", NS)
    months = None
    if dur is not None and dur.text:
        unit = (dur.get("unitCode") or "MONTH").upper()
        months = _f(dur.text) * {"MONTH": 1, "DAY": 1 / 30.4, "YEAR": 12}.get(unit, 1)
    fw = {x.text for x in root.iterfind(".//cac:ContractingSystem/cbc:ContractingSystemTypeCode", NS) if x.text}
    amount_el = root.find(".//efac:NoticeResult/cbc:TotalAmount", NS)
    currency = amount_el.get("currencyID", "NOK") if amount_el is not None else "NOK"
    if currency != "NOK":
        award = est = None  # unngå blanding av valuta; TED-verdier i EUR er sjeldne for norske kjøpere

    buyer_id = buyer.get("orgnr") or (buyer.get("name") or "ukjent").lower().replace(" ", "-")
    return {
        "id": pub, "source": "ted", "url": NOTICE_URL.format(pub=pub),
        "title": _t(proj, "cbc:Name"), "pub_date": pub_date or date.today().isoformat(),
        "buyer_id": "".join(ch for ch in buyer_id if ch.isalnum() or ch == "-"),
        "buyer": buyer.get("name"), "region": NUTS.get(buyer.get("nuts") or "", "Ukjent"),
        "sector": SECTOR.get((cpv or "")[:2], "Annet"), "cpv": cpv,
        "nature": NATURE.get(_t(proj, "cbc:ProcurementTypeCode") or "", "Annet"),
        "method": PROCEDURE.get(proc_code or "", "Annet"),
        "accelerated": int(reasons.get("accelerated-procedure") == "true"),
        "justification": reasons.get("direct-award-justification"),
        "framework": int(any(c.startswith("fa") for c in fw)),
        "num_bidders": max(bidders) if bidders else None,
        "est_value": est, "award_value": award, "tender_duration_days": None, "contract_months": months,
        "winner_ids": [orgs[w]["orgnr"] or w for w in winners], "winner_names": [orgs[w]["name"] for w in winners],
        "winner_size": SIZE.get(orgs[winners[0]]["size"] or "", None) if winners else None,
        "raw": {"procedure": proc_code, "reasons": reasons, "buyer_nuts": buyer.get("nuts"),
                "procedure_id": _t(root, "cbc:ContractFolderID")},
    }


# Feltene vi trenger, hentet direkte fra søke-API-et. XML-nedlastingen fra ted.europa.eu
# står bak en bot-sperre (AWS WAF) som stopper automatiske kall etter noen få nedlastinger.
FIELDS = ["publication-number", "publication-date", "notice-title", "buyer-name", "buyer-identifier", "buyer-country-sub",
          "winner-identifier", "winner-name", "winner-size", "procedure-type", "procedure-accelerated",
          "direct-award-justification-proc", "classification-cpv", "contract-nature", "result-value-notice",
          "result-value-cur-notice", "result-value-lot", "estimated-value-proc", "estimated-value-lot",
          "received-submissions-type-code", "received-submissions-type-val", "framework-agreement-lot",
          "contract-framework-agreement", "duration-period-value-lot", "duration-period-unit-lot", "procedure-identifier"]


def search_notices(client: httpx.Client, since: str, log=print) -> list[dict]:
    """Norske tildelinger siden `since` (YYYY-MM-DD), med alle felt vi bruker."""
    query = (f"buyer-country=NOR AND notice-type IN ({CAN_TYPES}) "
             f"AND publication-date>={since.replace('-', '')}")
    body = {"query": query, "fields": FIELDS, "limit": 250, "paginationMode": "ITERATION", "scope": "ALL"}
    out: list[dict] = []
    while True:
        j = _post(client, body).json()
        out += j.get("notices", [])
        log(f"  TED søk: {len(out)} treff")
        if not j.get("iterationNextToken") or not j.get("notices"):
            return out
        body["iterationNextToken"] = j["iterationNextToken"]


def _missing(x) -> bool:
    # Norskspråklige kunngjøringer mangler ofte oversettelse i API-et og får en plassholder
    return x is None or str(x).startswith("__NEEDS_TRANSLATION")


def _list(v) -> list:
    if v is None:
        return []
    if isinstance(v, dict):  # flerspråklig: {"nor": [...], "eng": [...]}
        for lang in ("nor", "nob", "nno", "dan", "swe", "eng", *v):
            if lang in v and (xs := _list(v[lang])):
                return xs
        return []
    return [x for x in (v if isinstance(v, list) else [v]) if not _missing(x)]


def _first(v):
    return next(iter(_list(v)), None)


def _sum(v) -> float | None:
    xs = [x for x in map(_f, _list(v)) if x]
    return sum(xs) or None


def parse_fields(n: dict) -> dict | None:
    """Samme rad som parse_notice, men fra søke-API-ets felt."""
    pub = n["publication-number"]
    title = None
    for t in _list(n.get("notice-title")):
        parts = t.split(" – ", 2)  # «Norge – CPV-tekst – Tittel»
        title = parts[-1] if len(parts) == 3 else t
        if "__NEEDS_TRANSLATION" in title:
            title = parts[1] if len(parts) == 3 else None
        break
    proc = _first(n.get("procedure-type"))
    cpv = _first(n.get("classification-cpv"))
    codes, vals = _list(n.get("received-submissions-type-code")), _list(n.get("received-submissions-type-val"))
    bidders = [_f(v) for c, v in zip(codes, vals) if c == "tenders"]
    bidders = [b for b in bidders if b is not None]
    ids, names = _list(n.get("winner-identifier")), _list(n.get("winner-name"))
    winners: dict[str, str] = {}
    for i, w in enumerate(ids):
        winners.setdefault(w, names[i] if i < len(names) else w)
    award = _f(_first(n.get("result-value-notice"))) or _sum(n.get("result-value-lot"))
    est = _f(_first(n.get("estimated-value-proc"))) or _sum(n.get("estimated-value-lot"))
    value_note = None
    if (cur := _first(n.get("result-value-cur-notice")) or "NOK") != "NOK":
        award = est = None
        value_note = f"Verdi oppgitt i {cur}, ikke tatt med"
    months = None
    if (d := _f(_first(n.get("duration-period-value-lot")))) is not None:
        unit = (_first(n.get("duration-period-unit-lot")) or "MONTH").upper()
        months = d * {"MONTH": 1, "DAY": 1 / 30.4, "YEAR": 12}.get(unit, 1)
    fw = _list(n.get("framework-agreement-lot")) + _list(n.get("contract-framework-agreement"))
    buyer_name = _first(n.get("buyer-name"))
    buyer_id = _first(n.get("buyer-identifier")) or (buyer_name or "ukjent").lower().replace(" ", "-")
    nuts = _first(n.get("buyer-country-sub"))
    justification = _first(n.get("direct-award-justification-proc"))
    accelerated = str(_first(n.get("procedure-accelerated"))).lower() == "true"
    return {
        "id": pub, "source": "ted", "url": NOTICE_URL.format(pub=pub),
        "title": title, "pub_date": str(n.get("publication-date") or date.today().isoformat())[:10],
        "buyer_id": "".join(ch for ch in buyer_id if ch.isalnum() or ch == "-"),
        "buyer": buyer_name, "region": NUTS.get(nuts or "", "Ukjent"),
        "sector": SECTOR.get((cpv or "")[:2], "Annet"), "cpv": cpv,
        "nature": NATURE.get(_first(n.get("contract-nature")) or "", "Annet"),
        "method": PROCEDURE.get(proc or "", "Annet"),
        "accelerated": int(accelerated), "justification": justification,
        "framework": int(any(str(c).startswith("fa") for c in fw)),
        "num_bidders": max(bidders) if bidders else None,
        "est_value": est, "award_value": award, "tender_duration_days": None, "contract_months": months,
        "winner_ids": list(winners), "winner_names": list(winners.values()),
        "winner_size": SIZE.get(_first(n.get("winner-size")) or "", None),
        "raw": {"procedure": proc, "reasons": {"accelerated-procedure": str(accelerated).lower(),
                                               **({"direct-award-justification": justification} if justification else {})},
                "buyer_nuts": nuts, "procedure_id": _first(n.get("procedure-identifier")),
                **({"value_note": value_note} if value_note else {})},
    }



def backfill_procedures(con, log=print) -> int:
    """Henter prosedyre-ID (BT-04) for TED-kunngjøringer lagret før vi tok vare på den. Ett søk, bare de to feltene."""
    missing = {r["id"]: r["raw"] for r in con.execute("SELECT id, raw FROM contracts WHERE source='ted'")
               if not (json.loads(r["raw"] or "{}") or {}).get("procedure_id")}
    if not missing:
        return 0
    since = min(r["pub_date"] for r in con.execute("SELECT pub_date FROM contracts WHERE source='ted'"))
    body = {"query": f"buyer-country=NOR AND notice-type IN ({CAN_TYPES}) AND publication-date>={since.replace('-', '')}",
            "fields": ["publication-number", "procedure-identifier"], "limit": 250, "paginationMode": "ITERATION", "scope": "ALL"}
    found: dict[str, str] = {}
    with httpx.Client(headers={"User-Agent": "Anskaffelsesradar/2.0"}) as client:
        while True:
            j = _post(client, body).json()
            for n in j.get("notices", []):
                if n.get("publication-number") in missing and (pid := _first(n.get("procedure-identifier"))):
                    found[n["publication-number"]] = pid
            if not j.get("iterationNextToken") or not j.get("notices"):
                break
            body["iterationNextToken"] = j["iterationNextToken"]
    with tx(con):
        for i, pid in found.items():
            raw = json.loads(missing[i] or "{}") or {}
            con.execute("UPDATE contracts SET raw=? WHERE id=?", (json.dumps(raw | {"procedure_id": pid}, ensure_ascii=False), i))
    log(f"TED: prosedyre-ID for {len(found)} av {len(missing)} eldre kunngjøringer")
    return len(found)


# Bare ordinære konkurranser: sosiale og særlige tjenester og plan- og designkonkurranser har ikke samme minstefrister
CN_TYPES = "cn-standard"


def competitions(con, since: str, log=print) -> int:
    """Konkurransekunngjøringer (frist og prosedyre) for norske forløp, slik at fristen kan kontrolleres mot tildelingen."""
    last = con.execute("SELECT MAX(dispatch_date) AS d FROM ted_competition").fetchone()["d"]
    start = last or since
    body = {"query": f"buyer-country=NOR AND notice-type IN ({CN_TYPES}) AND publication-date>={start[:10].replace('-', '')}",
            "fields": ["publication-number", "dispatch-date", "procedure-identifier", "deadline-receipt-tender-date-lot",
                       "deadline-receipt-request-date-lot", "procedure-type", "procedure-accelerated"],
            "limit": 250, "paginationMode": "ITERATION", "scope": "ALL"}
    rows = []
    with httpx.Client(headers={"User-Agent": "Anskaffelsesradar/2.0"}) as client:
        while True:
            j = _post(client, body).json()
            for n in j.get("notices", []):
                deadlines = _list(n.get("deadline-receipt-request-date-lot")) or _list(n.get("deadline-receipt-tender-date-lot"))
                if (pid := _first(n.get("procedure-identifier"))) and deadlines:
                    rows.append((pid, n["publication-number"], str(_first(n.get("dispatch-date")))[:10], min(str(d)[:10] for d in deadlines),
                                 _first(n.get("procedure-type")), int(str(_first(n.get("procedure-accelerated"))).lower() == "true")))
            if not j.get("iterationNextToken") or not j.get("notices"):
                break
            body["iterationNextToken"] = j["iterationNextToken"]
    with tx(con):
        # Første konkurransekunngjøring i forløpet gjelder; senere rettelser kan forlenge fristen
        con.executemany("INSERT OR IGNORE INTO ted_competition VALUES (?,?,?,?,?,?)", sorted(rows, key=lambda r: r[2]))
    log(f"TED: {len(rows)} konkurransekunngjøringer med frist")
    return len(rows)


def refresh(con, data_dir: Path, since: str, log=print) -> list[dict]:
    """Henter nye norske tildelinger fra TED. Returnerer parsede kontrakter."""
    cache = data_dir / "ted_xml"
    cache.mkdir(parents=True, exist_ok=True)
    known = {r["id"] for r in con.execute("SELECT id FROM contracts WHERE source='ted'")}
    with httpx.Client(headers={"User-Agent": "Anskaffelsesradar/2.0"}) as client:
        notices = search_notices(client, since, log)
    todo = [n for n in notices if n.get("publication-number") not in known]
    log(f"TED: {len(todo)} nye kunngjøringer")
    rows = []
    for n in todo:
        # Bruk XML fra tidligere kjøringer hvis den finnes (mer detaljert), ellers feltene fra søket
        f = cache / f"{n['publication-number']}.xml"
        try:
            row = parse_notice(f.read_bytes(), n["publication-number"], str(n.get("publication-date"))[:10]) if f.exists() else None
        except ET.ParseError:
            row = None
        if row := row or parse_fields(n):
            rows.append(row)
    return rows
