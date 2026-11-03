"""Flagg fra tilleggskildene: regnskap, eiere, godkjenningsregistre, kandidatlister, sanksjoner/PEP og KOFA.

Hver kilde gir bare flagg når tabellen har data, så en kilde som ikke er satt opp gir ingen falske funn.
Personer matches på normalisert navn + fødselsår. Det er en «mulig kobling» som alltid må verifiseres.
"""
from __future__ import annotations

import re

from .db import norm_name
from .ingest.registers import CPV_PREFIX

# Oversikt over flaggene herfra (nøkkel -> etikett, vekt, kilde). Vises på metodesiden.
FLAGS = {
    "rs_big_share": ("Kontraktsverdi over halve årsomsetningen", 1.0, "Regnskapsregisteret"),
    "rs_neg_equity": ("Negativ egenkapital", 0.8, "Regnskapsregisteret"),
    "rs_missing": ("Ingen årsregnskap registrert", 0.6, "Regnskapsregisteret"),
    "own_shared_suppliers": ("Felles eier med annen leverandør hos samme oppdragsgiver", 1.2,
                             "Aksjonærregisteret: direkte eller via holdingselskap, ≥ 10 %, uten stat og kommuner"),
    "own_buyer_person": ("Ubekreftet navnetreff: eier har rolle hos oppdragsgiver", 2.0, "Aksjonærregisteret + Brreg (navn + fødselsår)"),
    "own_buyer_owns": ("Oppdragsgiver registrert som eier av leverandøren", 0.3, "Aksjonærregisteret (direkte eller indirekte)"),
    "pol_link": ("Ubekreftet navnetreff mot lokalpolitiker", 1.5, "Kandidatlister mot roller og eiere (0,8 hvis ikke valgt)"),
    "sanc_org": ("Treff på sanksjonsliste (OpenSanctions)", 2.0, "OpenSanctions (0,8 uten sanksjon)"),
    "sanc_person": ("Navnetreff: rolleinnehaver/eier på sanksjonsliste", 2.0, "OpenSanctions"),
    "pep_person": ("Navnetreff: rolleinnehaver/eier på PEP-liste", 0.8, "OpenSanctions"),
    "reg_renhold": ("Ikke registrert som godkjent i renholdsregisteret", 1.2, "Arbeidstilsynet"),
    "reg_bemanning": ("Ikke registrert som godkjent i bemanningsregisteret", 1.2, "Arbeidstilsynet"),
    "kofa_brudd": ("KOFA har konstatert brudd i saken", 1.0, "KOFA (1,5 ved ulovlig direkte anskaffelse)"),
}

# Eierskap: minste andel (direkte eller beregnet indirekte) som teller som eier, og hvor mange ledd som følges
MIN_PCT = 10
MAX_DEPTH = 3
# Offentlige eiere tas ikke med i «felles eier»: staten og kommunene eier mange selskaper
PUBLIC_OWNER = re.compile(r"\bSTATEN\b|KOMMUNE\b|DEPARTEMENTET?\b|\bFOLKETRYGDFONDET\b|\bRHF\b", re.I)

# Godkjent-status i Arbeidstilsynets registre
_OK = ("godkjent",)


def _ok_status(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in _OK) and "ikke" not in s


class ExtraIndex:
    def __init__(self, con, ent: dict[str, dict] | None = None):
        self.ent = ent or {}
        self.regnskap = {r["orgnr"]: dict(r) for r in con.execute("SELECT * FROM regnskap")}
        self.has_regnskap = any(r.get("driftsinntekter") is not None for r in self.regnskap.values())

        # Eiere: nøkkel = orgnr for selskapseiere, ellers navn|fødselsår. Flere årganger slås sammen:
        # andelen fra siste år brukes, og `years` sier hvilke årsskifter eierposten er registrert.
        self.owners: dict[str, dict[str, dict]] = {}
        self.owner_years: set[int] = set()
        for r in con.execute("SELECT * FROM owners ORDER BY COALESCE(year, 0)"):
            key = r["owner_orgnr"] or f"{norm_name(r['owner_name'])}|{r['owner_birth_year'] or ''}"
            prev = self.owners.setdefault(r["orgnr"], {}).get(key)
            row = dict(r, years=sorted({*(prev["years"] if prev else []), *([r["year"]] if r["year"] else [])}))
            self.owners[r["orgnr"]][key] = row
            if r["source"] == "aksjonaerregisteret" and r["year"]:
                self.owner_years.add(r["year"])
        self._ult: dict[str, dict[str, dict]] = {}

        # Personer med roller: orgnr -> {(navn_norm, år)}
        self.role_people: dict[str, set[tuple]] = {}
        for r in con.execute("SELECT orgnr, navn, fodselsaar FROM brreg_roles WHERE navn IS NOT NULL"):
            self.role_people.setdefault(r["orgnr"], set()).add((norm_name(r["navn"]), r["fodselsaar"]))

        self.registers: dict[str, dict[str, str]] = {}
        for r in con.execute("SELECT register, orgnr, status FROM registers"):
            self.registers.setdefault(r["register"], {})[r["orgnr"]] = r["status"]

        self.politicians: dict[tuple, list[dict]] = {}
        for r in con.execute("SELECT * FROM politicians"):
            self.politicians.setdefault((r["navn_norm"], r["fodselsaar"]), []).append(dict(r))

        self.sanc_org: dict[str, dict] = {}
        self.sanc_person: dict[tuple, dict] = {}
        for r in con.execute("SELECT * FROM sanctions"):
            if r["orgnr"]:
                self.sanc_org[r["orgnr"]] = dict(r)
            if r["schema"] == "Person" and r["birth_year"]:
                self.sanc_person[(r["name_norm"], r["birth_year"])] = dict(r)

        self.kofa: dict[str, list[dict]] = {}
        for r in con.execute("""SELECT m.contract_id, m.score, k.* FROM kofa_matches m JOIN kofa_cases k USING (saksnr)"""):
            self.kofa.setdefault(r["contract_id"], []).append(dict(r))

    # ------------------------------------------------------------------ hjelpere
    def ultimate(self, orgnr: str, depth: int = MAX_DEPTH) -> dict[str, dict]:
        """Direkte og indirekte eiere: {nøkkel: {owner, pct, via}}. Indirekte andel = produktet av andelene i kjeden
        (60 % av et holdingselskap som eier 50 % gir 30 %). `via` er selskapene i mellom, nærmest leverandøren først.
        Ukjent andel (None) følges ikke videre. Sykluser stoppes."""
        if depth == MAX_DEPTH and orgnr in self._ult:
            return self._ult[orgnr]
        out: dict[str, dict] = {}

        def walk(org, share, via, left, seen):
            for k, o in self.owners.get(org, {}).items():
                pct = None if o["share_pct"] is None or share is None else round(o["share_pct"] * share / 100, 2)
                cur = out.get(k)
                if cur is None or (pct or 0) > (cur["pct"] or 0):
                    out[k] = {"owner": o, "pct": pct, "via": via}
                if o["owner_orgnr"] and left > 1 and pct is not None and o["owner_orgnr"] not in seen:
                    walk(o["owner_orgnr"], pct, [*via, o["owner_orgnr"]], left - 1, seen | {o["owner_orgnr"]})

        walk(orgnr, 100.0, [], depth, {orgnr})
        if depth == MAX_DEPTH:
            self._ult[orgnr] = out
        return out

    def _persons(self, orgnr: str) -> set[tuple]:
        """Rolleinnehavere og personlige eiere (direkte, eller indirekte med minst 10 %) for en virksomhet."""
        p = set(self.role_people.get(orgnr, set()))
        for u in self.ultimate(orgnr).values():
            o = u["owner"]
            if not o["owner_orgnr"] and o["owner_birth_year"] and (not u["via"] or (u["pct"] or 0) >= MIN_PCT):
                p.add((norm_name(o["owner_name"]), o["owner_birth_year"]))
        return p

    def _owner_keys(self, orgnr: str, public: bool = True) -> set[str]:
        """Eiere med minst 10 % direkte eller indirekte (eller ukjent andel), så små aksjonærer i børsselskaper ikke gir støy.
        public=False tar bort staten, kommuner og fylkeskommuner, som eier mange selskaper uten at det sier noe om koblingen."""
        return {k for k, u in self.ultimate(orgnr).items()
                if (u["pct"] is None and not u["via"]) or (u["pct"] or 0) >= MIN_PCT
                if public or not PUBLIC_OWNER.search(u["owner"]["owner_name"] or "")}

    def _via_text(self, sid: str, key: str) -> str:
        u = self.ultimate(sid).get(key)
        if not u or not u["via"]:
            return "direkte"
        return "via " + " → ".join(self._owner_name(sid, v) for v in u["via"])

    def _owner_name(self, child: str, orgnr: str) -> str:
        if (self.ent.get(orgnr) or {}).get("navn"):
            return self.ent[orgnr]["navn"]
        u = self.ultimate(child).get(orgnr)
        return u["owner"]["owner_name"] if u else orgnr

    def owner_status(self, orgnr: str) -> str | None:
        """Datakvalitet: AS/ASA som ikke finnes i de lastede årgangene av aksjonærregisteret. None hvis alt er i orden."""
        if not self.owner_years or (self.ent.get(orgnr) or {}).get("orgform") not in ("AS", "ASA"):
            return None
        if orgnr in self.owners:
            return None
        yrs = " og ".join(str(y) for y in sorted(self.owner_years))
        return (f"Ikke funnet i aksjonærregisteret ({yrs}) med eierposter på minst 5 %. Datakvalitet, ikke et trekk ved leverandøren: "
                "selskapet kan være stiftet etter årsskiftet, ha spredt eierskap eller mangle innrapportering.")

    def _name(self, orgnr: str) -> str:
        return (self.ent.get(orgnr) or {}).get("navn") or orgnr

    # ------------------------------------------------------------------ flagg
    def flags(self, c: dict, other_suppliers: set[str]) -> list[dict]:
        out: list[dict] = []

        def add(key, label, weight, why, source):
            if key not in {f["key"] for f in out}:
                out.append({"key": key, "label": label, "weight": weight, "source": source, "why": why})

        value = c.get("award_value") or c.get("est_value")
        cpv = str(c.get("cpv") or "")
        buyer = c.get("buyer_id")
        buyer_kommune = (self.ent.get(buyer) or {}).get("kommunenr")
        buyer_people = self.role_people.get(buyer, set())

        for sid in c.get("winner_ids") or []:
            s = self.ent.get(sid) or {}
            # Regnskap
            rs = self.regnskap.get(sid)
            if rs and rs.get("driftsinntekter"):
                if value and value > 0.5 * rs["driftsinntekter"]:
                    add("rs_big_share", "Kontraktsverdi over halve årsomsetningen", 1.0,
                        f"{value / rs['driftsinntekter']:.0%} av driftsinntekter {rs['aar']}", "regnskap")
            if rs and rs.get("egenkapital") is not None and rs["egenkapital"] < 0:
                add("rs_neg_equity", "Negativ egenkapital", 0.8, f"Egenkapital {rs['egenkapital']:,.0f} kr ({rs['aar']})".replace(",", " "), "regnskap")
            if self.has_regnskap and s.get("orgform") in ("AS", "ASA") and not (rs and rs.get("driftsinntekter") is not None) \
                    and (s.get("stiftet") or "9999") < "2024-01-01":
                add("rs_missing", "Ingen årsregnskap registrert", 0.6, "AS eldre enn to år uten innsendt regnskap", "regnskap")

            # Eiere (aksjonærregisteret: direkte og indirekte via holdingselskap, inntil tre ledd)
            mine = self._owner_keys(sid, public=False)
            linked = sorted(o for o in other_suppliers - {sid} if mine & self._owner_keys(o, public=False))
            if linked:
                common = sorted(mine & self._owner_keys(linked[0], public=False))
                indirect = any(self.ultimate(sid)[k]["via"] or self.ultimate(linked[0])[k]["via"] for k in common)
                add("own_shared_suppliers", "Felles eier med annen leverandør hos samme oppdragsgiver", 1.2,
                    ", ".join(self._name(o) for o in linked[:3]) + (" (eierskap gjennom holdingselskap)" if indirect else "")
                    + self._years_text(sid), "eiere")
            if buyer and buyer in self._owner_keys(sid):
                add("own_buyer_owns", "Oppdragsgiver registrert som eier av leverandøren", 0.3,
                    f"{self._via_text(sid, buyer).capitalize()}. Kan være lovlig egenregi (sjekk unntak)", "eiere")
            owner_people = {(norm_name(u["owner"]["owner_name"]), u["owner"]["owner_birth_year"]) for u in self.ultimate(sid).values()
                            if not u["owner"]["owner_orgnr"] and u["owner"]["owner_birth_year"]
                            and (not u["via"] or (u["pct"] or 0) >= MIN_PCT)}
            if hit := owner_people & buyer_people:
                add("own_buyer_person", "Ubekreftet navnetreff: eier har rolle hos oppdragsgiver", 2.0,
                    f"{len(hit)} person(er), navn + fødselsår{self._years_text(sid)}", "eiere")

            # Kandidatlister
            pols = [p for person in self._persons(sid) for p in self.politicians.get(person, [])
                    if not buyer_kommune or not p["kommunenr"] or p["kommunenr"] == buyer_kommune]
            if pols:
                valgt = any(p["valgt"] for p in pols)
                add("pol_link", "Ubekreftet navnetreff mot lokalpolitiker", 1.5 if valgt else 0.8,
                    f"{len(pols)} treff på navn + fødselsår: {'valgt' if valgt else 'kandidat'} i {pols[0]['kommune'] or pols[0]['kommunenr'] or 'ukjent kommune'}, {pols[0]['valg']}",
                    "valg")

            # Sanksjoner og PEP
            if hit := self.sanc_org.get(sid):
                add("sanc_org", "Treff på sanksjonsliste (OpenSanctions)" if hit["is_sanction"] else "Treff i OpenSanctions (ikke sanksjon)",
                    2.0 if hit["is_sanction"] else 0.8, hit["datasets"][:80], "opensanctions")
            for person in self._persons(sid):
                if hit := self.sanc_person.get(person):
                    add("sanc_person" if hit["is_sanction"] else "pep_person",
                        "Navnetreff: rolleinnehaver/eier på sanksjonsliste" if hit["is_sanction"] else "Navnetreff: rolleinnehaver/eier på PEP-liste",
                        2.0 if hit["is_sanction"] else 0.8, hit["datasets"][:80], "opensanctions")

            # Godkjenningsregistre
            for reg, prefixes in CPV_PREFIX.items():
                if reg in self.registers and cpv.startswith(prefixes):
                    st = self.registers[reg].get(sid)
                    if not _ok_status(st or ""):
                        add(f"reg_{reg}", f"Ikke registrert som godkjent i {reg}sregisteret", 1.2,
                            f"Status: {st}" if st else "Finnes ikke i registeret", "arbeidstilsynet")

        # KOFA
        for k in self.kofa.get(c.get("id"), []):
            if k["brudd"]:
                add("kofa_brudd", "KOFA har konstatert brudd i saken", 1.5 if k["ulovlig_direkte"] else 1.0,
                    f"Sak {k['saksnr']} ({k['dato']})", "kofa")
        return out

    def _years_text(self, orgnr: str) -> str:
        yrs = sorted({y for o in self.owners.get(orgnr, {}).values() for y in o.get("years") or []})
        return f" (aksjonærregisteret {' og '.join(map(str, yrs))})" if yrs else ""

    def supplier(self, orgnr: str) -> dict:
        """Data til leverandørkortet."""
        owners = sorted(self.owners.get(orgnr, {}).values(), key=lambda o: -(o["share_pct"] or 0))
        regs = {reg: v.get(orgnr) for reg, v in self.registers.items() if orgnr in v}
        indirect = sorted((u for u in self.ultimate(orgnr).values() if u["via"] and (u["pct"] or 0) >= MIN_PCT),
                          key=lambda u: -(u["pct"] or 0))
        return {
            "regnskap": self.regnskap.get(orgnr),
            # Personlige eiere vises uten fødselsår i API-et
            "owners": [{"name": o["owner_name"], "orgnr": o["owner_orgnr"], "share_pct": o["share_pct"],
                        "source": o["source"], "year": o["year"], "years": o.get("years") or [], "person": not o["owner_orgnr"]}
                       for o in owners[:25]],
            "owners_indirect": [{"name": u["owner"]["owner_name"], "orgnr": u["owner"]["owner_orgnr"], "share_pct": u["pct"],
                                 "via": [self._owner_name(orgnr, v) for v in u["via"]], "person": not u["owner"]["owner_orgnr"]}
                                for u in indirect[:15]],
            "owners_note": self.owner_status(orgnr),
            "registers": regs,
            "sanctions": self.sanc_org.get(orgnr),
        }
