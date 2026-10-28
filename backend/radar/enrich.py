"""Flagg fra tilleggskildene: regnskap, eiere, godkjenningsregistre, kandidatlister, sanksjoner/PEP og KOFA.

Hver kilde gir bare flagg når tabellen har data, så en kilde som ikke er satt opp gir ingen falske funn.
Personer matches på normalisert navn + fødselsår. Det er en «mulig kobling» som alltid må verifiseres.
"""
from __future__ import annotations

from .db import norm_name
from .ingest.registers import CPV_PREFIX

# Oversikt over flaggene herfra (nøkkel -> etikett, vekt, kilde). Vises på metodesiden.
FLAGS = {
    "rs_big_share": ("Kontraktsverdi over halve årsomsetningen", 1.0, "Regnskapsregisteret"),
    "rs_neg_equity": ("Negativ egenkapital", 0.8, "Regnskapsregisteret"),
    "rs_missing": ("Ingen årsregnskap registrert", 0.6, "Regnskapsregisteret"),
    "own_shared_suppliers": ("Felles eier med annen leverandør hos samme oppdragsgiver", 1.2, "Aksjonærregisteret / Proff"),
    "own_buyer_person": ("Navnetreff: eier har rolle hos oppdragsgiver", 2.0, "Aksjonærregisteret + Brreg"),
    "own_buyer_owns": ("Oppdragsgiver registrert som eier av leverandøren", 0.3, "Aksjonærregisteret"),
    "pol_link": ("Ubekreftet navnetreff mot lokalpolitiker", 1.5, "Kandidatlister (0,8 hvis ikke valgt)"),
    "sanc_org": ("Treff på sanksjonsliste (OpenSanctions)", 2.0, "OpenSanctions (0,8 uten sanksjon)"),
    "sanc_person": ("Navnetreff: rolleinnehaver/eier på sanksjonsliste", 2.0, "OpenSanctions"),
    "pep_person": ("Navnetreff: rolleinnehaver/eier på PEP-liste", 0.8, "OpenSanctions"),
    "reg_renhold": ("Ikke registrert som godkjent i renholdsregisteret", 1.2, "Arbeidstilsynet"),
    "reg_bemanning": ("Ikke registrert som godkjent i bemanningsregisteret", 1.2, "Arbeidstilsynet"),
    "kofa_brudd": ("KOFA har konstatert brudd i saken", 1.0, "KOFA (1,5 ved ulovlig direkte anskaffelse)"),
}

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

        # Eiere: nøkkel = orgnr for selskapseiere, ellers navn|fødselsår
        self.owners: dict[str, dict[str, dict]] = {}
        for r in con.execute("SELECT * FROM owners"):
            key = r["owner_orgnr"] or f"{norm_name(r['owner_name'])}|{r['owner_birth_year'] or ''}"
            self.owners.setdefault(r["orgnr"], {})[key] = dict(r)

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
    def _persons(self, orgnr: str) -> set[tuple]:
        """Rolleinnehavere og personlige eiere for en virksomhet."""
        p = set(self.role_people.get(orgnr, set()))
        for o in self.owners.get(orgnr, {}).values():
            if not o["owner_orgnr"] and o["owner_birth_year"]:
                p.add((norm_name(o["owner_name"]), o["owner_birth_year"]))
        return p

    def _owner_keys(self, orgnr: str) -> set[str]:
        # Bare eiere med minst 10 % (eller ukjent andel), så små aksjonærer i børsselskaper ikke gir støy
        return {k for k, o in self.owners.get(orgnr, {}).items() if o["share_pct"] is None or o["share_pct"] >= 10}

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

            # Eiere
            mine = self._owner_keys(sid)
            linked = sorted(o for o in other_suppliers - {sid} if mine & self._owner_keys(o))
            if linked:
                add("own_shared_suppliers", "Felles eier med annen leverandør hos samme oppdragsgiver", 1.2,
                    ", ".join(self._name(o) for o in linked[:3]), "eiere")
            if mine and buyer and buyer in mine:
                add("own_buyer_owns", "Oppdragsgiver registrert som eier av leverandøren", 0.3, "Kan være lovlig egenregi (sjekk unntak)", "eiere")
            owner_people = {(norm_name(o["owner_name"]), o["owner_birth_year"]) for o in self.owners.get(sid, {}).values()
                            if not o["owner_orgnr"] and o["owner_birth_year"]}
            if hit := owner_people & buyer_people:
                add("own_buyer_person", "Navnetreff: eier har rolle hos oppdragsgiver", 2.0, f"{len(hit)} person(er), navn + fødselsår", "eiere")

            # Kandidatlister
            pols = [p for person in self._persons(sid) for p in self.politicians.get(person, [])
                    if not buyer_kommune or not p["kommunenr"] or p["kommunenr"] == buyer_kommune]
            if pols:
                valgt = any(p["valgt"] for p in pols)
                add("pol_link", "Ubekreftet navnetreff mot lokalpolitiker", 1.5 if valgt else 0.8,
                    f"{len(pols)} treff på navn + fødselsår ({'valgt' if valgt else 'kandidat'}), {pols[0]['kommune'] or pols[0]['kommunenr'] or ''}".strip(", "),
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

    def supplier(self, orgnr: str) -> dict:
        """Data til leverandørkortet."""
        owners = sorted(self.owners.get(orgnr, {}).values(), key=lambda o: -(o["share_pct"] or 0))
        regs = {reg: v.get(orgnr) for reg, v in self.registers.items() if orgnr in v}
        return {
            "regnskap": self.regnskap.get(orgnr),
            # Personlige eiere vises uten fødselsår i API-et
            "owners": [{"name": o["owner_name"], "orgnr": o["owner_orgnr"], "share_pct": o["share_pct"],
                        "source": o["source"], "year": o["year"], "person": not o["owner_orgnr"]} for o in owners[:25]],
            "registers": regs,
            "sanctions": self.sanc_org.get(orgnr),
        }
