"""Avskrevne flagg: revisorens tilbakemelding på falske positiver.

En avskrivning gjelder ett flagg (flag_key) enten på én kontrakt eller for en leverandør overalt
(f.eks. «legitimt konsern» gjelder alle kontraktene til leverandøren). Avskrevne flagg teller ikke i
prioriteten: API-et trekker fra poengene med en gang, og neste scoring rangerer uten dem.

Avskrivningene er også et merket datasett for å kalibrere vektene i scoring.py (GET /api/dismissals.csv).
De justerer ikke vektene automatisk: revisorer avskriver også ubeleilige funn, så vektene bør endres
etter en manuell gjennomgang av mønstrene, ikke av en løkke som lærer seg å overse dem.
"""
from __future__ import annotations

# (kode, tekst, om avskrivningen kan gjelde leverandøren overalt)
REASONS: list[tuple[str, str, bool]] = [
    ("konsern", "Legitimt konsern eller felles eierskap", True),
    ("rammeavtale", "Kjent rammeavtale eller avrop", False),
    ("navnelikhet", "Navnelikhet eller feil kobling av person", True),
    ("datafeil", "Feil eller mangel i kildedataene", False),
    ("forklart", "Forklart og dokumentert av oppdragsgiver", False),
    ("annet", "Annet (begrunn i notatet)", False),
]
REASON_CODES = {c for c, _, _ in REASONS}
SCOPES = ("kontrakt", "leverandor")


def active(con) -> tuple[dict[str, dict[str, dict]], dict[str, dict[str, dict]]]:
    """(per kontrakt, per leverandør): {id: {flag_key: avskrivning}} for avskrivninger som ikke er angret."""
    by_c: dict[str, dict[str, dict]] = {}
    by_s: dict[str, dict[str, dict]] = {}
    for r in con.execute("SELECT * FROM flag_dismissals WHERE revoked_at IS NULL ORDER BY id"):
        d = dict(r)
        if d["scope"] == "leverandor" and d["orgnr"]:
            by_s.setdefault(d["orgnr"], {})[d["flag_key"]] = d
        elif d["contract_id"]:
            by_c.setdefault(d["contract_id"], {})[d["flag_key"]] = d
    return by_c, by_s


def lookup(act, contract_id: str, winner_ids, key: str) -> dict | None:
    by_c, by_s = act
    if d := by_c.get(contract_id, {}).get(key):
        return d
    return next((by_s[o][key] for o in winner_ids or [] if key in by_s.get(o, {})), None)


def public(d: dict) -> dict:
    return {k: d[k] for k in ("id", "scope", "reason", "note", "actor", "at", "orgnr")}
