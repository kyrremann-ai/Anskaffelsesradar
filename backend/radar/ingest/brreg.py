"""Brønnøysundregistrene: Enhetsregisteret + roller (data.brreg.no, åpent, uten nøkkel).

- GET /enhetsregisteret/api/enheter/{orgnr}          (410 Gone = slettet)
- GET /enhetsregisteret/api/underenheter/{orgnr}     (fallback for avdelinger)
- GET /enhetsregisteret/api/enheter/{orgnr}/roller

Henter bare orgnr som ikke er hentet før, eller som er eldre enn `max_age_days`.
Personer lagres med person_key = sha256(navn|fødselsdato|salt).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx

from ..db import now, tx

BASE = "https://data.brreg.no/enhetsregisteret/api"

ROLE_CODES = {
    "DAGL": "Daglig leder", "LEDE": "Styreleder", "NEST": "Nestleder", "MEDL": "Styremedlem",
    "VARA": "Varamedlem", "INNH": "Innehaver", "DTPR": "Deltaker (delt ansvar)", "DTSO": "Deltaker (solidarisk)",
    "KOMP": "Komplementar", "REPR": "Norsk representant (NUF)", "REVI": "Revisor", "REGN": "Regnskapsfører",
}
CONTROL_ROLES = {"DAGL", "LEDE", "NEST", "MEDL", "INNH", "DTPR", "DTSO", "KOMP", "REPR"}

# Grov CPV-divisjon -> forventede NACE-seksjoner. Heuristikk, utvides ved behov.
CPV_TO_NACE = {
    "03": "A", "09": "BCD", "14": "B", "15": "C", "18": "CG", "22": "CJ", "30": "CG", "31": "CFG", "32": "CGJ",
    "33": "CGQ", "34": "CGH", "35": "CG", "37": "CG", "38": "CGM", "39": "CG", "42": "CG", "43": "CG", "44": "CFG",
    "45": "F", "48": "JG", "50": "CGS", "51": "CF", "55": "I", "60": "H", "63": "H", "64": "HJ", "65": "D", "66": "K",
    "70": "L", "71": "MF", "72": "J", "73": "M", "75": "O", "76": "B", "77": "AN", "79": "MN", "80": "P", "85": "Q",
    "90": "EN", "92": "R", "98": "S",
}
_NACE_RANGES = [(1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"), (45, 47, "G"),
                (49, 53, "H"), (55, 56, "I"), (58, 63, "J"), (64, 66, "K"), (68, 68, "L"), (69, 75, "M"), (77, 82, "N"),
                (84, 84, "O"), (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"), (94, 96, "S"), (97, 98, "T"), (99, 99, "U")]
PUBLIC_FORMS = {"KOMM", "FYLK", "STAT", "ORGL", "KF", "IKS", "SF"}


def norm_orgnr(x) -> str | None:
    d = re.sub(r"\D", "", str(x or ""))
    return d if len(d) == 9 else None


def nace_section(code: str | None) -> str | None:
    try:
        d = int(str(code).split(".")[0][:2])
    except (TypeError, ValueError):
        return None
    return next((s for lo, hi, s in _NACE_RANGES if lo <= d <= hi), None)


def person_key(navn: str, fodselsdato: str, salt: str) -> str:
    return hashlib.sha256(f"{navn.lower()}|{fodselsdato}|{salt}".encode()).hexdigest()[:16]


def _get(client: httpx.Client, url: str, retries: int = 5):
    for i in range(retries):
        try:
            r = client.get(url, timeout=20)
        except httpx.HTTPError:
            time.sleep(2 ** i)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 2 ** i)))
            continue
        body = r.json() if "json" in r.headers.get("content-type", "") and r.content else None
        return r.status_code, body
    return 599, None


def fetch_one(client: httpx.Client, orgnr: str) -> dict | None:
    st, ent = _get(client, f"{BASE}/enheter/{orgnr}")
    kind = "enhet" if st == 200 else "slettet" if st == 410 else None
    if kind is None:
        st, ent = _get(client, f"{BASE}/underenheter/{orgnr}")
        kind = "underenhet" if st == 200 else "slettet" if st == 410 else None
    if kind is None:
        return None if st == 599 else {"orgnr": orgnr, "kind": "ukjent", "enhet": {}, "roller": None}
    roller = None
    if kind != "slettet":
        role_org = (ent or {}).get("overordnetEnhet") if kind == "underenhet" else orgnr
        st, roller = _get(client, f"{BASE}/enheter/{role_org or orgnr}/roller")
        roller = roller if st == 200 else None
    return {"orgnr": orgnr, "kind": kind, "enhet": ent or {}, "roller": roller}


def roles_from_api(orgnr: str, roller: dict | None, salt: str) -> list[dict]:
    out = []
    for g in (roller or {}).get("rollegrupper", []):
        for r in g.get("roller", []):
            code = (r.get("type") or {}).get("kode")
            if code not in ROLE_CODES or r.get("fratraadt") or r.get("avregistrert"):
                continue
            if p := r.get("person"):
                n = p.get("navn") or {}
                navn = " ".join(filter(None, [n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")]))
                out.append({"orgnr": orgnr, "rolle": code, "rolle_navn": ROLE_CODES[code], "type": "person",
                            "person_key": person_key(navn, p.get("fodselsdato", ""), salt), "navn": navn,
                            "er_doed": int(bool(p.get("erDoed"))),
                            "fodselsaar": int(p["fodselsdato"][:4]) if p.get("fodselsdato") else None})
            elif e := r.get("enhet"):
                navn = e.get("navn")
                navn = " ".join(navn) if isinstance(navn, list) else navn
                out.append({"orgnr": orgnr, "rolle": code, "rolle_navn": ROLE_CODES[code], "type": "enhet",
                            "person_key": f"org:{e.get('organisasjonsnummer')}", "navn": navn, "er_doed": 0,
                            "fodselsaar": None})
    return out


def store(con, recs: list[dict], salt: str) -> None:
    with tx(con):
        for rec in recs:
            con.execute("INSERT OR REPLACE INTO brreg_entities(orgnr, fetched_at, data) VALUES (?,?,?)",
                        (rec["orgnr"], now(), json.dumps({"kind": rec["kind"], **(rec["enhet"] or {})}, ensure_ascii=False)))
            con.execute("DELETE FROM brreg_roles WHERE orgnr=?", (rec["orgnr"],))
            con.executemany(
                "INSERT INTO brreg_roles(orgnr, rolle, rolle_navn, type, person_key, navn, er_doed, fodselsaar) VALUES (?,?,?,?,?,?,?,?)",
                [(r["orgnr"], r["rolle"], r["rolle_navn"], r["type"], r["person_key"], r["navn"], r["er_doed"], r["fodselsaar"])
                 for r in roles_from_api(rec["orgnr"], rec["roller"], salt)])


def refresh(con, user_agent: str, salt: str, max_age_days: int = 30, workers: int = 4, log=print) -> int:
    """Henter Brreg-data for alle orgnr i kontraktene som mangler eller er utdatert."""
    wanted: set[str] = set()
    for r in con.execute("SELECT buyer_id, winner_ids FROM contracts WHERE source != 'demo'"):
        if o := norm_orgnr(r["buyer_id"]):
            wanted.add(o)
        wanted.update(filter(None, map(norm_orgnr, json.loads(r["winner_ids"] or "[]"))))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    fresh = {r["orgnr"] for r in con.execute("SELECT orgnr FROM brreg_entities WHERE fetched_at >= ?", (cutoff,))}
    todo = sorted(wanted - fresh)
    log(f"Brreg: {len(todo)} orgnr å hente ({len(wanted)} totalt)")
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    done = 0
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(todo), 200):
            batch = [r for r in ex.map(lambda o: fetch_one(client, o), todo[i:i + 200]) if r]
            store(con, batch, salt)
            done += len(batch)
            log(f"  Brreg {done}/{len(todo)}")
    return done


def load_demo(con, entities: list[dict], roles: list[dict], salt: str) -> None:
    with tx(con):
        for e in entities:
            con.execute("INSERT OR REPLACE INTO brreg_entities(orgnr, fetched_at, data) VALUES (?,?,?)",
                        (e["organisasjonsnummer"], now(), json.dumps({"kind": "enhet", **e}, ensure_ascii=False)))
        con.execute("DELETE FROM brreg_roles")
        con.executemany(
            "INSERT INTO brreg_roles(orgnr, rolle, rolle_navn, type, person_key, navn, er_doed, fodselsaar) VALUES (?,?,?,?,?,?,?,?)",
            [(r["orgnr"], r["rolle"], ROLE_CODES[r["rolle"]], "person", person_key(r["person"], str(r.get("year", "")), salt),
              r["person"], 0, r.get("year"))
             for r in roles])


# ---------------------------------------------------------------- flagg

def entity_summary(data: dict) -> dict:
    adr = data.get("forretningsadresse") or data.get("beliggenhetsadresse") or {}
    nace = (data.get("naeringskode1") or {}).get("kode")
    return {
        "orgnr": data.get("organisasjonsnummer"), "kind": data.get("kind"), "navn": data.get("navn"),
        "orgform": (data.get("organisasjonsform") or {}).get("kode"),
        "stiftet": data.get("stiftelsesdato") or data.get("registreringsdatoEnhetsregisteret"),
        "nace": nace, "nace_seksjon": nace_section(nace), "nace_tekst": (data.get("naeringskode1") or {}).get("beskrivelse"),
        "ansatte": data.get("antallAnsatte"), "mva": data.get("registrertIMvaregisteret"),
        "konkurs": bool(data.get("konkurs")), "under_avvikling": bool(data.get("underAvvikling")),
        "tvangsavvikling": bool(data.get("underTvangsavviklingEllerTvangsopplosning")),
        "slettedato": data.get("slettedato"), "kommunenr": adr.get("kommunenummer"),
        "adresse": ", ".join(filter(None, [*(adr.get("adresse") or []), adr.get("postnummer"), adr.get("poststed")])),
        "adresse_key": "|".join(filter(None, [*(adr.get("adresse") or []), adr.get("postnummer")])).lower() or None,
        "overordnet": data.get("overordnetEnhet"),
    }


def _d(s) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class BrregIndex:
    """Holder Brreg-data i minnet og beregner flagg per kontrakt."""

    def __init__(self, con):
        self.ent = {r["orgnr"]: entity_summary(json.loads(r["data"])) for r in con.execute("SELECT orgnr, data FROM brreg_entities")}
        self.people: dict[str, set[str]] = {}
        self.dead: set[str] = set()
        self.auditor: set[str] = set()
        for r in con.execute("SELECT orgnr, rolle, person_key, er_doed FROM brreg_roles"):
            if r["rolle"] in CONTROL_ROLES:
                self.people.setdefault(r["orgnr"], set()).add(r["person_key"])
            if r["rolle"] == "REVI":
                self.auditor.add(r["orgnr"])
            if r["er_doed"]:
                self.dead.add(r["orgnr"])
        self.by_addr: dict[str, set[str]] = {}
        for o, e in self.ent.items():
            if e["adresse_key"] and e["orgform"] not in PUBLIC_FORMS:
                self.by_addr.setdefault(e["adresse_key"], set()).add(o)

    def flags(self, c: dict, other_suppliers: set[str]) -> list[dict]:
        out: list[dict] = []

        def add(key, label, weight, why):
            out.append({"key": key, "label": label, "weight": weight, "source": "brreg", "why": why})

        award = _d(c.get("pub_date"))
        value = c.get("award_value") or c.get("est_value")
        bp = set(self.people.get(c.get("buyer_id"), set()))
        if (b := self.ent.get(c.get("buyer_id"))) and b.get("overordnet"):
            bp |= self.people.get(b["overordnet"], set())
        for sid in c.get("winner_ids") or []:
            s = self.ent.get(sid)
            if not s:
                continue
            if s["kind"] in ("slettet", "ukjent") or s["slettedato"]:
                add("brreg_deleted", "Leverandør slettet/ukjent i Brreg", 1.5, s["slettedato"] or "Finnes ikke i Enhetsregisteret")
            if s["konkurs"]:
                add("brreg_bankrupt", "Leverandør under konkurs", 1.5, "Konkursbehandling registrert")
            if s["under_avvikling"] or s["tvangsavvikling"]:
                add("brreg_dissolving", "Leverandør under avvikling", 1.0, "Under (tvangs)avvikling")
            reg = _d(s["stiftet"])
            if reg and award:
                age = (award - reg).days
                if age < 0:
                    add("brreg_after_award", "Leverandør stiftet etter kunngjøring", 1.5, f"Stiftet {reg}")
                elif age < 365:
                    add("brreg_new", "Nyetablert leverandør (<12 mnd)", 1.0, f"{age} dager gammel")
            if s["mva"] is False and s["orgform"] not in PUBLIC_FORMS:
                add("brreg_no_vat", "Leverandør ikke MVA-registrert", 1.0, "Ikke i MVA-registeret")
            if s["ansatte"] in (None, 0) and value and value >= 2_000_000:
                add("brreg_no_staff", "Leverandør uten ansatte", 0.6, f"0/ukjent ansatte, kontrakt {value:,.0f} kr".replace(",", " "))
            if sid in self.dead:
                add("brreg_dead_role", "Død rolleinnehaver registrert", 0.4, "Registeret er ikke oppdatert")
            exp = CPV_TO_NACE.get(str(c.get("cpv") or "")[:2])
            if exp and s["nace_seksjon"] and s["nace_seksjon"] not in exp:
                add("brreg_nace", "Bransje passer ikke med kjøpet", 0.5,
                    f"CPV {str(c.get('cpv'))[:2]} mot NACE {s['nace']} {s['nace_tekst'] or ''}".strip())
            sp = self.people.get(sid, set())
            if shared := sp & bp:
                add("brreg_shared_buyer", "Felles rolleinnehaver med oppdragsgiver", 2.0, f"{len(shared)} person(er)")
            linked = sorted(o for o in other_suppliers - {sid} if sp & self.people.get(o, set()))
            if linked:
                add("brreg_shared_suppliers", "Felles roller med konkurrerende leverandør", 1.2,
                    ", ".join(self.ent.get(o, {}).get("navn") or o for o in linked[:3]))
            if s["adresse_key"]:
                co = (self.by_addr.get(s["adresse_key"], set()) & other_suppliers) - {sid}
                if co:
                    add("brreg_shared_address", "Samme adresse som annen leverandør", 0.5,
                        ", ".join(self.ent.get(o, {}).get("navn") or o for o in sorted(co)[:3]))
        # Fjern duplikater (flere vinnere med samme flagg)
        seen, uniq = set(), []
        for f in out:
            if f["key"] not in seen:
                seen.add(f["key"])
                uniq.append(f)
        return uniq
