"""TED (Tenders Electronic Daily): norske tildelingskunngjøringer.

1. Søk etter publiseringsnumre via TED Search API v3 (åpent, uten nøkkel):
   POST https://api.ted.europa.eu/v3/notices/search
2. Hent eForms-XML per kunngjøring og parse feltene vi trenger:
   GET https://ted.europa.eu/en/notice/{publication-number}/xml

XML-en caches på disk (data/ted_xml/), så nye kjøringer bare henter nye kunngjøringer.
eForms er standard for alle TED-kunngjøringer fra 2024, så 2025+ er dekket.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
XML_URL = "https://ted.europa.eu/en/notice/{pub}/xml"
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


def search_publication_numbers(client: httpx.Client, since: str, log=print) -> list[tuple[str, str]]:
    """Returnerer [(publiseringsnummer, publiseringsdato)] for norske tildelinger siden `since` (YYYY-MM-DD)."""
    query = (f"buyer-country=NOR AND notice-type IN ({CAN_TYPES}) "
             f"AND publication-date>={since.replace('-', '')}")
    body = {"query": query, "fields": ["publication-number", "publication-date"], "limit": 250,
            "paginationMode": "ITERATION", "scope": "ALL"}
    out: list[tuple[str, str]] = []
    token = None
    while True:
        if token:
            body["iterationNextToken"] = token
        r = client.post(SEARCH_URL, json=body, timeout=60)
        r.raise_for_status()
        j = r.json()
        for n in j.get("notices", []):
            pd_ = n.get("publication-date") or ""
            out.append((n["publication-number"], str(pd_)[:10]))
        token = j.get("iterationNextToken")
        log(f"  TED søk: {len(out)} treff")
        if not token or not j.get("notices"):
            return out


def fetch_xml(client: httpx.Client, pub: str, cache: Path) -> bytes | None:
    f = cache / f"{pub}.xml"
    if f.exists():
        return f.read_bytes()
    for i in range(4):
        try:
            r = client.get(XML_URL.format(pub=pub), timeout=60, follow_redirects=True)
            if r.status_code == 200:
                f.write_bytes(r.content)
                return r.content
            if r.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        time.sleep(2 ** i)
    return None


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
        "raw": {"procedure": proc_code, "reasons": reasons, "buyer_nuts": buyer.get("nuts")},
    }


def refresh(con, data_dir: Path, since: str, log=print) -> list[dict]:
    """Henter nye norske tildelinger fra TED. Returnerer parsede kontrakter."""
    cache = data_dir / "ted_xml"
    cache.mkdir(parents=True, exist_ok=True)
    known = {r["id"] for r in con.execute("SELECT id FROM contracts WHERE source='ted'")}
    with httpx.Client(headers={"User-Agent": "Anskaffelsesradar/2.0"}) as client:
        pubs = search_publication_numbers(client, since, log)
        todo = [(p, d) for p, d in pubs if p not in known]
        log(f"TED: {len(todo)} nye kunngjøringer")
        rows = []
        for i, (pub, d) in enumerate(todo, 1):
            xml = fetch_xml(client, pub, cache)
            if xml:
                try:
                    if row := parse_notice(xml, pub, d):
                        rows.append(row)
                except ET.ParseError as e:
                    log(f"  kunne ikke parse {pub}: {e}")
            if i % 200 == 0:
                log(f"  TED {i}/{len(todo)}")
            time.sleep(0.05)
    return rows
