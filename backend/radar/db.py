"""SQLite-lag. Én fil, ingen ORM. Byttes enkelt til Postgres senere."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,              -- TED publiseringsnummer eller demo-id
    source TEXT NOT NULL,             -- ted | demo
    url TEXT,
    title TEXT,
    pub_date TEXT,
    buyer_id TEXT,                    -- orgnr når kjent, ellers slug av navn
    buyer TEXT,
    region TEXT,
    sector TEXT,
    cpv TEXT,
    nature TEXT,                      -- Varer | Tjenester | Bygg og anlegg
    method TEXT,                      -- Åpen | Begrenset | Forhandling | Direkte tildeling | Annet
    accelerated INTEGER DEFAULT 0,
    justification TEXT,
    framework INTEGER DEFAULT 0,
    num_bidders REAL,
    est_value REAL,                   -- NOK
    award_value REAL,                 -- NOK
    tender_duration_days REAL,
    contract_months REAL,
    winner_ids TEXT,                  -- JSON-liste med orgnr
    winner_names TEXT,                -- JSON-liste
    winner_size TEXT,
    raw TEXT,                         -- JSON fra kilden, for sporbarhet
    fetched_at TEXT                   -- når denne versjonen av kunngjøringen ble hentet
);
CREATE INDEX IF NOT EXISTS ix_contracts_buyer ON contracts(buyer_id);

-- Observasjonsenhet: én rad i contracts er én kunngjøring. Her kobles kunngjøringene til forløp,
-- og duplikater (samme tildeling i TED og Doffin, rettelser) peker på kunngjøringen som telles. Bygges på nytt hver kjøring.
CREATE TABLE IF NOT EXISTS observasjon (
    contract_id TEXT PRIMARY KEY,
    notice_type TEXT,                 -- tildeling | intensjon | endring | avlyst
    procedure_id TEXT,                -- anskaffelsesforløp (TED procedure-identifier, ellers egen id)
    duplicate_of TEXT,                -- kunngjøringen som telles, hvis denne er et duplikat
    dup_rule TEXT,                    -- hvilken regel som slo dem sammen
    value_note TEXT,                  -- verdikontroll: hvorfor verdien er usikker
    cn_ref TEXT,                      -- konkurransekunngjøringen fristen er hentet fra
    cn_accelerated INTEGER            -- akselerert frist oppgitt i konkurransekunngjøringen
);

-- Konkurransekunngjøringer fra TED (bare fristen), koblet til tildelingene via prosedyre-ID
CREATE TABLE IF NOT EXISTS ted_competition (
    procedure_id TEXT PRIMARY KEY,
    publication_number TEXT,
    dispatch_date TEXT,               -- sendt til TED; lovens minstefrister regnes herfra
    deadline TEXT,                    -- tidligste tilbudsfrist / frist for forespørsel om deltakelse
    procedure TEXT,
    accelerated INTEGER
);

CREATE TABLE IF NOT EXISTS scores (
    contract_id TEXT PRIMARY KEY REFERENCES contracts(id),
    priority REAL, tier TEXT, percentile REAL,
    cri REAL, klagerisiko REAL,
    repeat_pair_count REAL, prev_contract REAL, winner_market_share_pct REAL,
    award_over_estimate_pct REAL, data_coverage INTEGER,
    flags TEXT,                       -- JSON-liste [{key,label,weight,source}]
    regler TEXT                       -- faste kontrollpunkter [{key,label,why}]
);

CREATE TABLE IF NOT EXISTS brreg_entities (
    orgnr TEXT PRIMARY KEY, fetched_at TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS brreg_roles (
    orgnr TEXT, rolle TEXT, rolle_navn TEXT, type TEXT, person_key TEXT, navn TEXT, er_doed INTEGER,
    fodselsaar INTEGER               -- bare år, brukes til matching mot kandidatlister og PEP
);
CREATE INDEX IF NOT EXISTS ix_roles_org ON brreg_roles(orgnr);
CREATE INDEX IF NOT EXISTS ix_roles_person ON brreg_roles(person_key);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT UNIQUE REFERENCES contracts(id),
    title TEXT, buyer TEXT, score REAL,
    status TEXT DEFAULT 'Ny', assignee TEXT DEFAULT '', note TEXT DEFAULT '',
    vurdering TEXT DEFAULT '', vurdering_grunn TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT
);
-- Revisjonsspor: hver endring på en sak, hvem og når.
CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER REFERENCES cases(id),
    at TEXT, actor TEXT, field TEXT, old TEXT, new TEXT
);
-- AI-vurderinger lagres med modell, tidspunkt og hvilke felt som ble sendt.
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT, at TEXT, provider TEXT, model TEXT, input TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Regnskapsregisteret (siste år per orgnr)
CREATE TABLE IF NOT EXISTS regnskap (
    orgnr TEXT PRIMARY KEY, aar INTEGER, driftsinntekter REAL, driftsresultat REAL, aarsresultat REAL,
    egenkapital REAL, gjeld REAL, eiendeler REAL, fetched_at TEXT
);
-- Eiere/aksjonærer (Proff eller annen kilde)
CREATE TABLE IF NOT EXISTS owners (
    orgnr TEXT, owner_name TEXT, owner_orgnr TEXT, owner_birth_year INTEGER, share_pct REAL,
    source TEXT, year INTEGER, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_owners_org ON owners(orgnr);
-- Offentlige godkjenningsregistre (renhold, bemanning)
CREATE TABLE IF NOT EXISTS registers (
    register TEXT, orgnr TEXT, navn TEXT, status TEXT, PRIMARY KEY (register, orgnr)
);
-- Kandidatlister fra valg (navn normalisert + fødselsår)
CREATE TABLE IF NOT EXISTS politicians (
    -- Ikke parti: politisk oppfatning er en særlig kategori (GDPR art. 9), og trengs ikke for koblingen
    navn_norm TEXT, fodselsaar INTEGER, kommunenr TEXT, kommune TEXT, valgt INTEGER, valg TEXT
);
CREATE INDEX IF NOT EXISTS ix_pol ON politicians(navn_norm, fodselsaar);
-- Sanksjons- og PEP-lister (OpenSanctions, filtrert på Norge)
CREATE TABLE IF NOT EXISTS sanctions (
    id TEXT PRIMARY KEY, schema TEXT, name TEXT, name_norm TEXT, birth_year INTEGER, orgnr TEXT,
    is_sanction INTEGER, datasets TEXT
);
CREATE INDEX IF NOT EXISTS ix_sanc_name ON sanctions(name_norm);
-- KOFA-avgjørelser
CREATE TABLE IF NOT EXISTS kofa_cases (
    saksnr TEXT PRIMARY KEY, dato TEXT, innklaget TEXT, innklaget_orgnr TEXT, leverandor TEXT,
    tittel TEXT, brudd INTEGER, ulovlig_direkte INTEGER, url TEXT
);
CREATE TABLE IF NOT EXISTS kofa_matches (
    saksnr TEXT, contract_id TEXT, score REAL, PRIMARY KEY (saksnr, contract_id)
);
-- eInnsyn: oppdragsgivers enhet (tom streng = ikke i eInnsyn) og journalposter per kontrakt
CREATE TABLE IF NOT EXISTS einnsyn_enhet (orgnr TEXT PRIMARY KEY, enhet_id TEXT);
CREATE TABLE IF NOT EXISTS einnsyn (contract_id TEXT PRIMARY KEY, items TEXT, at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    if (c := {r[1] for r in con.execute("PRAGMA table_info(observasjon)")}) and "cn_ref" not in c:
        con.execute("DROP TABLE observasjon")  # bygges på nytt hver kjøring
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(brreg_roles)")}
    if "fodselsaar" not in cols:  # databaser fra før v2.1
        con.execute("ALTER TABLE brreg_roles ADD COLUMN fodselsaar INTEGER")
    if "klagerisiko" not in {r[1] for r in con.execute("PRAGMA table_info(scores)")}:  # fra v2.4
        con.execute("ALTER TABLE scores ADD COLUMN klagerisiko REAL")
    if "fetched_at" not in {r[1] for r in con.execute("PRAGMA table_info(contracts)")}:  # fra v2.5
        con.execute("ALTER TABLE contracts ADD COLUMN fetched_at TEXT")
    if "parti" in {r[1] for r in con.execute("PRAGMA table_info(politicians)")}:  # partiet fjernet (GDPR art. 9)
        con.execute("ALTER TABLE politicians DROP COLUMN parti")
        con.commit()
        con.execute("VACUUM")  # så de slettede verdiene ikke blir liggende i ledige sider
    if "regler" not in {r[1] for r in con.execute("PRAGMA table_info(scores)")}:  # faste kontrollpunkter, fra v2.5
        con.execute("ALTER TABLE scores ADD COLUMN regler TEXT")
    cols = {r[1] for r in con.execute("PRAGMA table_info(cases)")}
    for c in ("vurdering", "vurdering_grunn"):  # revisorens vurdering, fra v2.3
        if c not in cols:
            con.execute(f"ALTER TABLE cases ADD COLUMN {c} TEXT DEFAULT ''")
    return con


@contextmanager
def tx(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def norm_name(s: str | None) -> str:
    """Normaliserer navn for matching: små bokstaver, uten tegnsetting og selskapsform."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKC", (s or "").lower())
    s = re.sub(r"[^\wæøå ]+", " ", s)
    s = re.sub(r"\b(as|asa|ans|da|sa|ba|nuf|ks|iks|kf)\b", " ", s)
    return " ".join(s.split())


def get_meta(con, key: str, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


CONTRACT_COLS = [
    "id", "source", "url", "title", "pub_date", "buyer_id", "buyer", "region", "sector", "cpv", "nature",
    "method", "accelerated", "justification", "framework", "num_bidders", "est_value", "award_value",
    "tender_duration_days", "contract_months", "winner_ids", "winner_names", "winner_size", "raw", "fetched_at",
]


def upsert_contracts(con, rows: list[dict]) -> int:
    sql = f"INSERT OR REPLACE INTO contracts({','.join(CONTRACT_COLS)}) VALUES ({','.join('?' * len(CONTRACT_COLS))})"
    vals, t = [], now()
    for r in rows:
        r = dict(r)
        r.setdefault("fetched_at", t)
        for k in ("winner_ids", "winner_names"):
            r[k] = json.dumps(r.get(k) or [], ensure_ascii=False)
        r["raw"] = json.dumps(r.get("raw"), ensure_ascii=False) if r.get("raw") is not None else None
        vals.append([r.get(c) for c in CONTRACT_COLS])
    with tx(con):
        con.executemany(sql, vals)
    return len(vals)
