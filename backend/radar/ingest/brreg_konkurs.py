"""Selskaper under konkursbehandling og rollene deres (data.brreg.no, åpne data under NLOD).

Brukes til flagget «Rolleinnehaver har rolle i selskap under konkursbehandling»:
en person med ledende rolle hos leverandøren (daglig leder, styre, innehaver) har også
rolle i et annet selskap som er registrert med konkurs i Enhetsregisteret.

- GET /enhetsregisteret/api/enheter?konkurs=true   (bare enheter som fortsatt er registrert)
- GET /enhetsregisteret/api/enheter/{orgnr}/roller

Dette er IKKE konkurskarantene. Karantene finnes bare i Konkursregisteret, som ikke er åpne data
(se docs/konkurskarantene.md). Revisoren sjekker karantene selv via lenken i saksmappen.

Dataminimering: rollene i konkursselskapene lagres bare som person_key (hash av navn og
fødselsdato med salt), aldri som navn. Navnet vises bare via leverandørens egne roller.
Konkurser eldre enn KEEP_YEARS slettes (jf. konkursregisterforskriften § 11).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx

from ..db import now, tx
from .brreg import BASE, CONTROL_ROLES, ROLE_CODES, _get, person_key

KEEP_YEARS = 5
PAGE = 1000  # Brreg tillater maks 10 000 treff per søk (page * size)


def list_bankrupt(client: httpx.Client) -> list[dict]:
    out, page = [], 0
    while page * PAGE < 10_000:
        st, body = _get(client, f"{BASE}/enheter?konkurs=true&size={PAGE}&page={page}")
        if st != 200:
            raise RuntimeError(f"Brreg konkurssøk ga HTTP {st}")
        rows = (body.get("_embedded") or {}).get("enheter") or []
        out += [{"orgnr": e["organisasjonsnummer"], "navn": e.get("navn"), "konkursdato": e.get("konkursdato")} for e in rows]
        if page + 1 >= (body.get("page") or {}).get("totalPages", 0):
            break
        page += 1
    return out


def roles_from_api(orgnr: str, roller: dict | None, salt: str) -> list[tuple]:
    """Bare personer i ledende roller, og bare som person_key."""
    out = set()
    for g in (roller or {}).get("rollegrupper", []):
        for r in g.get("roller", []):
            p, kode = r.get("person"), (r.get("type") or {}).get("kode")
            if not p or kode not in CONTROL_ROLES or r.get("fratraadt") or r.get("avregistrert"):
                continue
            n = p.get("navn") or {}
            navn = " ".join(filter(None, [n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")]))
            # Uten fødselsdato blir koblingen bare et navnetreff: tas ikke med
            if navn and p.get("fodselsdato"):
                out.add((orgnr, kode, ROLE_CODES.get(kode, kode), person_key(navn, p["fodselsdato"], salt)))
    return sorted(out)


def store(con, bankrupt: list[dict], roles: dict[str, list[tuple]]) -> None:
    cutoff = f"{date.today().year - KEEP_YEARS}{date.today().isoformat()[4:]}"
    with tx(con):
        for b in bankrupt:
            con.execute("INSERT INTO brreg_konkurs(orgnr, navn, konkursdato, fetched_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(orgnr) DO UPDATE SET navn=excluded.navn, konkursdato=excluded.konkursdato",
                        (b["orgnr"], b["navn"], b["konkursdato"], None))
        for orgnr, rs in roles.items():
            con.execute("DELETE FROM brreg_konkurs_roles WHERE orgnr=?", (orgnr,))
            con.executemany("INSERT INTO brreg_konkurs_roles(orgnr, rolle, rolle_navn, person_key) VALUES (?,?,?,?)", rs)
            con.execute("UPDATE brreg_konkurs SET fetched_at=? WHERE orgnr=?", (now(), orgnr))
        # Selskaper som slettes etter bobehandling beholdes til konkursen er KEEP_YEARS år gammel
        con.execute("DELETE FROM brreg_konkurs WHERE konkursdato < ?", (cutoff,))
        con.execute("DELETE FROM brreg_konkurs_roles WHERE orgnr NOT IN (SELECT orgnr FROM brreg_konkurs)")


def refresh(con, user_agent: str, salt: str, workers: int = 4, log=print) -> int:
    """Henter listen over konkurser og roller for konkurser vi ikke har hentet roller for."""
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    with httpx.Client(headers=headers) as client:
        bankrupt = list_bankrupt(client)
        known = {r[0] for r in con.execute("SELECT orgnr FROM brreg_konkurs WHERE fetched_at IS NOT NULL")}
        todo = [b["orgnr"] for b in bankrupt if b["orgnr"] not in known]
        log(f"Brreg konkurser: {len(bankrupt)} registrert, roller hentes for {len(todo)}")
        store(con, bankrupt, {})

        def one(o):
            st, body = _get(client, f"{BASE}/enheter/{o}/roller")
            return o, (roles_from_api(o, body, salt) if st == 200 else None)

        with ThreadPoolExecutor(workers) as ex:
            for i in range(0, len(todo), 200):
                got = {o: rs for o, rs in ex.map(one, todo[i:i + 200]) if rs is not None}
                store(con, [], got)
                log(f"  Konkursroller {min(i + 200, len(todo))}/{len(todo)}")
    return len(bankrupt)


def load_demo(con, bankrupt: list[dict], roles: list[dict], salt: str) -> None:
    """bankrupt: [{orgnr, navn, konkursdato}], roles: [{orgnr, rolle, person, fodselsdato}]."""
    with tx(con):
        con.execute("DELETE FROM brreg_konkurs")
        con.execute("DELETE FROM brreg_konkurs_roles")
    store(con, bankrupt, {})
    by_org: dict[str, list[tuple]] = {}
    for r in roles:
        by_org.setdefault(r["orgnr"], []).append(
            (r["orgnr"], r["rolle"], ROLE_CODES[r["rolle"]], person_key(r["person"], r["fodselsdato"], salt)))
    store(con, [], by_org)
