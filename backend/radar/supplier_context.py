"""Alternative forklaringer for leverandørflaggene.

Fire flagg er undersøkelsespunkter, ikke funn: kontraktsverdi over halve omsetningen, ingen ansatte,
samme adresse og felles roller/eiere med en annen leverandør. Før de vises sjekkes de mot spørsmålene
under. Svarene legges på flagget som `checks`, så revisoren ser hva som er sjekket og hva som mangler.

  1. Er kontraktsverdien flerårig, mens omsetningen gjelder ett år?   -> årlig verdi = verdi / (måneder / 12)
  2. Gjelder verdien hele rammeavtalen eller én leverandørs andel?    -> delt likt på leverandørene (antakelse)
  3. Er leverandøren et konsernselskap?                               -> konsernregnskap, eiere ≥ 50 %
  4. Var rollen/adressen felles på tildelingstidspunktet, eller bare i dag?  -> Brregs endringslogg
  5. Er to leverandører egentlig samme juridiske enhet etter en endring?    -> underenhet, fusjon/fisjon, navn

Status per sjekk:
  ok        forklaringen gjelder ikke (flagget står)
  justert   tallet er korrigert (f.eks. årlig verdi); flagget står bare hvis det fortsatt slår til
  forklarer forklaringen gjelder; flagget eller koblingen faller bort
  svekker   forklaringen kan gjelde; vekten halveres
  ukjent    data mangler; står i flagget, vekten endres ikke
"""
from __future__ import annotations

import json
from datetime import date

from .db import norm_name
from .enrich import FLAGS as EXTRA_FLAGS
from .ingest.brreg import BrregIndex, _d

CONTEXT_KEYS = ("rs_big_share", "brreg_no_staff", "brreg_shared_address", "brreg_shared_suppliers", "own_shared_suppliers")
NO_STAFF_MIN_VALUE = 2_000_000


def _nok(v: float) -> str:
    return f"{v / 1e6:,.1f} MNOK".replace(",", " ").replace(".", ",") if v >= 1e6 else f"{v:,.0f} kr".replace(",", " ")


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _value(c: dict) -> float | None:
    """Tildelt verdi. Estimert verdi brukes ikke: den er ofte en maksramme, og det ville vært å gjette. NaN = mangler."""
    v = c.get("award_value")
    return float(v) if v is not None and v == v and v > 0 else None


def _check(q: str, status: str, a: str) -> dict:
    return {"q": q, "status": status, "a": a}


class SupplierContext:
    def __init__(self, con, brreg: BrregIndex, extra=None):
        self.b, self.x = brreg, extra
        self.ent = brreg.ent
        # Regnskap per år: orgnr -> {(år, type): driftsinntekter}
        self.years: dict[str, dict[tuple, float]] = {}
        for r in con.execute("SELECT orgnr, aar, type, driftsinntekter FROM regnskap_aar WHERE driftsinntekter IS NOT NULL"):
            self.years.setdefault(r["orgnr"], {})[(r["aar"], r["type"])] = r["driftsinntekter"]
        # Eldre databaser har bare siste år i `regnskap`
        for r in con.execute("SELECT orgnr, aar, driftsinntekter FROM regnskap WHERE driftsinntekter IS NOT NULL AND aar IS NOT NULL"):
            self.years.setdefault(r["orgnr"], {}).setdefault((r["aar"], "SELSKAP"), r["driftsinntekter"])

        # Eiere med minst 50 % som er selskaper -> morselskap
        self.parent: dict[str, str] = {}
        self.owner_year: dict[str, int] = {}
        self.has_owners = False
        for r in con.execute("SELECT orgnr, owner_orgnr, share_pct, year FROM owners"):
            self.has_owners = True
            if r["year"]:
                self.owner_year[r["orgnr"]] = r["year"]
            if r["owner_orgnr"] and (r["share_pct"] or 0) >= 50:
                self.parent[r["orgnr"]] = r["owner_orgnr"]

        # Endringslogg: orgnr -> [(dato, felt, verdi)], og fra hvilken dato den dekker
        self.changes: dict[str, list[tuple]] = {}
        for r in con.execute("SELECT orgnr, at, felt, verdi FROM brreg_changes ORDER BY at"):
            self.changes.setdefault(r["orgnr"], []).append((r["at"], r["felt"], r["verdi"]))
        self.changes_since = {r["orgnr"]: (r["since"], r["fetched_at"][:10]) for r in con.execute("SELECT * FROM brreg_changes_fetched")}

        self.successor: dict[str, set[str]] = {}
        self.succ_info: dict[frozenset, dict] = {}
        for r in con.execute("SELECT * FROM brreg_successors"):
            self.successor.setdefault(r["fra_orgnr"], set()).add(r["til_orgnr"])
            self.successor.setdefault(r["til_orgnr"], set()).add(r["fra_orgnr"])
            self.succ_info[frozenset((r["fra_orgnr"], r["til_orgnr"]))] = dict(r)

    # ------------------------------------------------------------------ enhet og konsern
    def name(self, o: str) -> str:
        return (self.ent.get(o) or {}).get("navn") or o

    def same_entity(self, a: str, b: str) -> str | None:
        """Grunn til at a og b er samme juridiske virksomhet, ellers None."""
        ea, eb = self.ent.get(a) or {}, self.ent.get(b) or {}
        if ea.get("overordnet") == b or eb.get("overordnet") == a:
            return "underenhet av samme juridiske enhet"
        if ea.get("overordnet") and ea.get("overordnet") == eb.get("overordnet"):
            return "underenheter av samme juridiske enhet"
        # Fusjon/fisjon/omdanning, også i kjede (A -> B -> C)
        seen, todo = {a}, [a]
        while todo:
            for n in self.successor.get(todo.pop(), set()) - seen:
                if n == b:
                    info = self.succ_info.get(frozenset((a, b))) or {}
                    return f"{info.get('type') or 'omorganisering'}{' ' + info['dato'] if info.get('dato') else ''} (registrert endring)"
                seen.add(n)
                todo.append(n)
        deleted = [o for o, e in ((a, ea), (b, eb)) if e.get("kind") == "slettet" or e.get("slettedato")]
        if deleted and ea.get("navn") and norm_name(ea.get("navn")) == norm_name(eb.get("navn")):
            return f"{self.name(deleted[0])} er slettet og har samme navn (sannsynlig omorganisering, ikke bekreftet)"
        return None

    def group(self, o: str) -> dict:
        """Konsernforhold: {'parent': orgnr|None, 'is_parent': bool, 'known': bool}."""
        is_parent = any(t == "KONSERN" for _, t in self.years.get(o, {}))
        return {"parent": self.parent.get(o), "is_parent": is_parent, "known": self.has_owners or is_parent}

    def same_group(self, a: str, b: str) -> str | None:
        pa, pb = self.parent.get(a), self.parent.get(b)
        if pa == b or pb == a:
            return "morselskap og datterselskap"
        if pa and pa == pb:
            return f"søsterselskaper (eid av {self.name(pa)})"
        return None

    def _group_check(self, o: str, why_matters: str) -> dict:
        g = self.group(o)
        q = "Er leverandøren et konsernselskap?"
        if g["parent"]:
            return _check(q, "svekker", f"Datterselskap av {self.name(g['parent'])} (≥ 50 % eierandel). {why_matters}")
        if g["is_parent"]:
            return _check(q, "svekker", f"Morselskap som leverer konsernregnskap. {why_matters}")
        if not g["known"]:
            return _check(q, "ukjent", "Konsernforhold ikke kjent: aksjonærregisteret er ikke lastet")
        return _check(q, "ok", "Ingen morselskap med ≥ 50 % og ikke konsernregnskap")

    # ------------------------------------------------------------------ tidspunkt
    def coverage(self, o: str, award: date | None) -> str | None:
        """None hvis endringsloggen dekker tildelingsdatoen, ellers en tekst om hva dataene gjelder."""
        if award is None:
            return "tildelingsdato mangler, så dette er nåværende data"
        cov = self.changes_since.get(o)
        if not cov or (_d(cov[0]) or date.max) > award:
            return f"nåværende data{f' (hentet {cov[1]})' if cov else ''}; endringsloggen dekker ikke tildelingsdatoen"
        return None

    def changed_after(self, o: str, award: date, fields: tuple[str, ...]) -> list[tuple]:
        return [c for c in self.changes.get(o, []) if (_d(c[0]) or date.min) > award and c[1] in fields]

    def at_award(self, o: str, award: date | None, felt: str) -> tuple[str, str]:
        """Var feltet (adresse | roller) det samme ved tildeling som i dag? -> (status, tekst)."""
        if note := self.coverage(o, award):
            return "ukjent", f"{self.name(o)}: {note}"
        if known := self.changed_after(o, award, (felt,)):
            if felt == "adresse":
                return "svekker", f"{self.name(o)}: adressen endret {known[0][0]}, etter tildelingen"
            # Rollefeeden sier bare at noe endret seg, ikke hvilken rolle
            return "ukjent", f"{self.name(o)}: roller endret {known[0][0]}, etter tildelingen; om den felles rollen gjaldt da er ikke bekreftet"
        if felt == "adresse" and (unk := self.changed_after(o, award, ("ukjent",))):
            return "ukjent", f"{self.name(o)}: endret {unk[0][0]} uten detaljer i Brregs logg; adressen ved tildeling er ikke bekreftet"
        return "ok", f"{self.name(o)}: ingen {felt}endring siden tildelingen"

    def staff_at_award(self, o: str, award: date | None) -> tuple[str, int | None, str]:
        """Antall ansatte ved tildeling. -> (status, antall, tekst)."""
        cur = (self.ent.get(o) or {}).get("ansatte")
        if note := self.coverage(o, award):
            return "ukjent", cur, note
        after = self.changed_after(o, award, ("ansatte",))
        if not after:
            if unk := self.changed_after(o, award, ("ukjent",)):
                return "ukjent", cur, f"endret {unk[0][0]} uten detaljer i Brregs logg; antall ansatte ved tildeling er ikke bekreftet"
            return "ok", cur, "ingen endring i antall ansatte siden tildelingen"
        before = [ch for ch in self.changes.get(o, []) if ch[1] == "ansatte" and (_d(ch[0]) or date.min) <= award]
        if before:
            v = before[-1][2]
            return "justert", int(v) if v and v.isdigit() else 0, f"antall ansatte ved tildeling: {v or 0} (endret {after[0][0]})"
        return "ukjent", cur, f"antall ansatte endret {after[0][0]}, etter tildelingen; tallet ved tildeling er ukjent"

    # ------------------------------------------------------------------ flaggene
    def apply(self, c: dict, flags: list[dict], others: set[str]) -> list[dict]:
        """Erstatter de fire leverandørflaggene med versjoner som har sjekket alternative forklaringer."""
        keep = [f for f in flags if f["key"] not in CONTEXT_KEYS]
        present = {f["key"]: f for f in flags if f["key"] in CONTEXT_KEYS}
        # Omsetningsflagget vurderes alltid på nytt: regnskapet fra tildelingsåret kan gi treff der siste år ikke gjør det
        if self.years:
            label, weight, _ = EXTRA_FLAGS["rs_big_share"]
            present.setdefault("rs_big_share", {"key": "rs_big_share", "label": label, "weight": weight, "source": "regnskap"})
        for key, fn in (("rs_big_share", self.big_share), ("brreg_no_staff", self.no_staff),
                        ("brreg_shared_address", self.shared_address), ("brreg_shared_suppliers", self.shared_roles),
                        ("own_shared_suppliers", self.shared_owners)):
            if key in present and (f := fn(c, present[key], others)):
                keep.append(f)
        return keep

    def _finish(self, flag: dict, checks: list[dict], why: str) -> dict:
        w = flag["weight"] * (0.5 if any(ch["status"] == "svekker" for ch in checks) else 1.0)
        missing = [ch["a"] for ch in checks if ch["status"] == "ukjent"]
        out = flag | {"weight": round(w, 2), "why": why, "checks": checks}
        if missing:
            out["data_note"] = "Mangler: " + "; ".join(missing)
        return out

    def _turnover(self, o: str, year: int | None) -> tuple[float, int, str] | None:
        """(driftsinntekter, år, orgnr) fra selskapsregnskapet nærmest tildelingsåret. Underenheter bruker hovedenheten."""
        org = o if self.years.get(o) else (self.ent.get(o) or {}).get("overordnet") or o
        ys = {y: v for (y, t), v in self.years.get(org, {}).items() if t == "SELSKAP" and v}
        if not ys:
            return None
        y = min(ys, key=lambda k: (abs(k - year), -k)) if year else max(ys)
        return ys[y], y, org

    def big_share(self, c: dict, flag: dict, others: set[str]) -> dict | None:
        value = _value(c)
        if not value:
            return None
        award = _d(c.get("pub_date"))
        winners = c.get("winner_ids") or []
        n = max(len(winners), 1)
        months = c.get("contract_months")
        months = months if months == months else None  # NaN
        best = None
        for sid in winners:
            t = self._turnover(sid, award.year if award else None)
            if not t:
                continue
            turnover, year, org = t
            checks = []
            share = value / n
            q2 = "Gjelder verdien hele rammeavtalen eller én leverandørs andel?"
            if n > 1:
                checks.append(_check(q2, "justert", f"{n} leverandører; verdien er delt likt ({_nok(share)} hver). Faktisk fordeling er ukjent"))
            elif c.get("framework"):
                checks.append(_check(q2, "svekker", "Rammeavtale med én leverandør: verdien er ofte en øvre ramme, ikke faktisk kjøp"))
            else:
                checks.append(_check(q2, "ok", "Én leverandør, ikke rammeavtale"))
            q1 = "Er kontraktsverdien flerårig, mens omsetningen gjelder ett år?"
            annual = share
            if months and months > 12:
                annual = share / (months / 12)
                checks.append(_check(q1, "justert", f"{months:.0f} måneder: årlig verdi {_nok(annual)}"))
            elif months:
                checks.append(_check(q1, "ok", f"{months:.0f} måneder"))
            else:
                checks.append(_check(q1, "ukjent", "Varighet er ikke oppgitt i kunngjøringen; hele verdien er sammenlignet med ett års omsetning"))
            if org != sid:
                checks.append(_check("Hvilket regnskap gjelder?", "ok", f"Underenhet: regnskapet til {self.name(org)}"))
            if award and abs(year - award.year) > 1:
                checks.append(_check("Gjelder omsetningen samme periode som kontrakten?", "ukjent",
                                     f"Nærmeste regnskap er fra {year}, tildelt {award.year}"))
            ratio = annual / turnover
            g = self._group_check(org, "Konsernets samlede omsetning kan være mer relevant")
            group_rev = next((v for (y, t), v in self.years.get(org, {}).items() if t == "KONSERN" and y == year and v), None)
            if group_rev:
                g["a"] += f" (konsernets driftsinntekter {year}: {_nok(group_rev)}, {_pct(annual / group_rev)})"
            checks.append(g)
            if ratio <= 0.5:
                continue
            why = f"Årlig verdi {_nok(annual)} er {_pct(ratio)} av driftsinntektene {year} ({_nok(turnover)})"
            f = self._finish(flag, checks, why)
            if best is None or ratio > best[0]:
                best = (ratio, f)
        return best[1] if best else None

    def no_staff(self, c: dict, flag: dict, others: set[str]) -> dict | None:
        value = _value(c)
        award = _d(c.get("pub_date"))
        for sid in c.get("winner_ids") or []:
            s = self.ent.get(sid)
            if not s or s["ansatte"] not in (None, 0) or not value or value < NO_STAFF_MIN_VALUE:
                continue
            checks = []
            st, n, txt = self.staff_at_award(sid, award)
            if st == "justert" and n:
                continue  # hadde ansatte da kontrakten ble tildelt
            checks.append(_check("Gjelder tallet tildelingstidspunktet, eller bare i dag?", st if st != "justert" else "ok", txt))
            if s["ansatte"] is None:
                q = "Er antall ansatte registrert?"
                if s.get("har_ansatte"):
                    continue  # har registrerte ansatte, Brreg viser bare ikke antallet
                checks.append(_check(q, "ok", "Ingen ansatte registrert i Aa-registeret") if s.get("har_ansatte") is False else
                              _check(q, "ukjent", "Enhetsregisteret oppgir verken antall eller om det finnes registrerte ansatte"))
            checks.append(self._group_check(sid, "Ansatte kan være ansatt i et annet selskap i konsernet"))
            if s.get("kind") == "underenhet":
                checks.append(_check("Er leverandøren en underenhet?", "svekker", "Ansatte kan være registrert på hovedenheten"))
            why = f"{'Ukjent antall' if s['ansatte'] is None and s.get('har_ansatte') is None else '0'} ansatte, kontrakt {_nok(value)}"
            return self._finish(flag, checks, why)
        return None

    def _links(self, c, flag, others, candidates_fn, felt: str, what: str) -> dict | None:
        award = _d(c.get("pub_date"))
        checks, kept = [], []
        for sid in c.get("winner_ids") or []:
            for o in sorted(candidates_fn(sid, others - {sid})):
                if why := self.same_entity(sid, o):
                    checks.append(_check("Er de samme juridiske enhet etter en endring?", "forklarer", f"{self.name(sid)} og {self.name(o)}: {why}"))
                    continue
                if why := self.same_group(sid, o):
                    checks.append(_check("Er leverandørene i samme konsern?", "forklarer", f"{self.name(sid)} og {self.name(o)}: {why}"))
                    continue
                if felt == "eiere":
                    yr = self.owner_year.get(sid)
                    st = "ukjent" if not award or not yr or abs(yr - award.year) > 1 else "ok"
                    tid = [(st, f"eierdata fra {yr or 'ukjent år'}, tildelt {award.year if award else 'ukjent'}")]
                else:
                    tid = [self.at_award(sid, award, felt), self.at_award(o, award, felt)]
                for s_, txt in tid:
                    checks.append(_check(f"Var {what} felles på tildelingstidspunktet, eller bare i dag?", s_, txt))
                kept.append(o)
        if not kept:
            return None
        if not any(ch["q"].startswith("Er de samme") for ch in checks):
            checks.insert(0, _check("Er de samme juridiske enhet etter en endring?", "ok",
                                    "Ingen registrert fusjon/fisjon, ikke underenheter av samme enhet"))
        if felt != "eiere" and not self.has_owners:
            checks.append(_check("Er leverandørene i samme konsern?", "ukjent", "Konsernforhold ikke kjent: aksjonærregisteret er ikke lastet"))
        why = ", ".join(self.name(o) for o in kept[:3])
        return self._finish(flag, checks, why)

    def shared_address(self, c, flag, others):
        def cands(sid, rest):
            key = (self.ent.get(sid) or {}).get("adresse_key")
            return (self.b.by_addr.get(key, set()) & rest) if key else set()
        return self._links(c, flag, others, cands, "adresse", "adressen")

    def shared_roles(self, c, flag, others):
        def cands(sid, rest):
            sp = self.b.people.get(sid, set())
            return {o for o in rest if sp & self.b.people.get(o, set())}
        return self._links(c, flag, others, cands, "roller", "rollen")

    def shared_owners(self, c, flag, others):
        if self.x is None:
            return flag
        def cands(sid, rest):
            mine = self.x._owner_keys(sid)
            return {o for o in rest if mine & self.x._owner_keys(o)}
        return self._links(c, flag, others, cands, "eiere", "eierskapet")


def change_candidates(con, brreg: BrregIndex, contracts) -> set[str]:
    """Leverandører som trenger endringsloggen: de med ingen-ansatte-, felles adresse- eller felles rolle-flagg, og de de deler med."""
    out: set[str] = set()
    by_buyer: dict[str, set[str]] = {}
    for r in contracts:
        by_buyer.setdefault(r["buyer_id"], set()).update(r["winner_ids"])
    for r in contracts:
        others = by_buyer.get(r["buyer_id"], set())
        keys = {f["key"] for f in brreg.flags(r, others)}
        if keys & {"brreg_no_staff", "brreg_shared_address", "brreg_shared_suppliers"}:
            out.update(r["winner_ids"])
            for sid in r["winner_ids"]:
                e = brreg.ent.get(sid) or {}
                out |= brreg.by_addr.get(e.get("adresse_key"), set()) & others
                sp = brreg.people.get(sid, set())
                out |= {o for o in others if sp & brreg.people.get(o, set())}
    return out


def contracts_for_candidates(con) -> list[dict]:
    return [{"id": r["id"], "buyer_id": r["buyer_id"], "winner_ids": json.loads(r["winner_ids"] or "[]"), "pub_date": r["pub_date"],
             "cpv": r["cpv"], "award_value": r["award_value"], "est_value": r["est_value"]}
            for r in con.execute("SELECT id, buyer_id, winner_ids, pub_date, cpv, award_value, est_value FROM contracts")]
