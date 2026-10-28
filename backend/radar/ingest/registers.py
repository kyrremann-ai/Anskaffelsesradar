"""Arbeidstilsynets godkjenningsregistre (åpne data, NLOD 2.0, oppdateres daglig).

- Renhold:  https://registerdata.arbeidstilsynet.no/renhold_register.xml
- Bemanning: https://data.arbeidstilsynet.no/bemanningsforetaksregisteret2/api (JSON, se openapi.arbeidstilsynet.no)

Elementnavnene står i Arbeidstilsynets XSD (RegisterXML6.xsd). Parseren leter etter
elementer som har et organisasjonsnummer og en status, uansett nøyaktig navn.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import httpx

from ..db import tx

RENHOLD_URL = "https://registerdata.arbeidstilsynet.no/renhold_register.xml"
BEMANNING_URL = "https://data.arbeidstilsynet.no/bemanningsforetaksregisteret2/api"
# CPV-koder der godkjenning kreves
CPV_PREFIX = {"renhold": ("9091",), "bemanning": ("7962", "7961")}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _json_records(node, inherited: dict | None = None):
    """Alle objekter i JSON-treet, med enkle felt flatet ut til små bokstaver.
    Underenheter uten egen status arver godkjenningen til foretaket de ligger under."""
    if isinstance(node, list):
        for x in node:
            yield from _json_records(x, inherited)
    elif isinstance(node, dict):
        rec = {k.lower(): str(v).strip() for k, v in node.items() if isinstance(v, (str, int)) and not isinstance(v, bool)}
        status = {k: v for k, v in rec.items() if "status" in k} or (inherited or {})
        yield {**status, **rec}
        for v in node.values():
            if isinstance(v, (list, dict)):
                yield from _json_records(v, status)


def parse(raw: bytes) -> list[dict]:
    """XML (renhold) eller JSON (bemanning). Leter etter objekter med orgnr og status."""
    if raw.lstrip()[:1] in (b"[", b"{"):
        records = _json_records(json.loads(raw))
    else:
        records = ({_local(c.tag): (c.text or "").strip() for c in el} for el in ET.fromstring(raw).iter())
    out, seen = [], set()
    for kids in records:
        org = next((v for k, v in kids.items() if "organisasjonsnummer" in k or k in ("orgnr", "orgnummer")), None)
        org = re.sub(r"\D", "", org or "")
        if len(org) != 9 or org in seen:
            continue
        seen.add(org)
        status = next((v for k, v in kids.items() if "status" in k), "")
        navn = next((v for k, v in kids.items() if k in ("navn", "virksomhetsnavn", "name") or k.startswith("navn")), "")
        out.append({"orgnr": org, "navn": navn, "status": status or "Ukjent"})
    return out


def store(con, register: str, rows: list[dict]) -> None:
    with tx(con):
        con.execute("DELETE FROM registers WHERE register=?", (register,))
        con.executemany("INSERT OR REPLACE INTO registers(register, orgnr, navn, status) VALUES (?,?,?,?)",
                        [(register, r["orgnr"], r["navn"], r["status"]) for r in rows])


def refresh(con, register: str, url: str, log=print) -> int:
    if not url:
        return 0
    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    rows = parse(r.content)
    store(con, register, rows)
    log(f"Register {register}: {len(rows)} virksomheter")
    return len(rows)
