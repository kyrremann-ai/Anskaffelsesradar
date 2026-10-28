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
    raw TEXT                          -- JSON fra kilden, for sporbarhet
);
CREATE INDEX IF NOT EXISTS ix_contracts_buyer ON contracts(buyer_id);

CREATE TABLE IF NOT EXISTS scores (
    contract_id TEXT PRIMARY KEY REFERENCES contracts(id),
    priority REAL, tier TEXT, percentile REAL,
    ml_score REAL, ml_lr REAL, ml_rf REAL, ml_gb REAL,
    cri REAL, anomaly REAL,
    repeat_pair_count REAL, prev_contract REAL, winner_market_share_pct REAL,
    award_over_estimate_pct REAL, data_coverage INTEGER,
    flags TEXT                        -- JSON-liste [{key,label,weight,source}]
);

CREATE TABLE IF NOT EXISTS brreg_entities (
    orgnr TEXT PRIMARY KEY, fetched_at TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS brreg_roles (
    orgnr TEXT, rolle TEXT, rolle_navn TEXT, type TEXT, person_key TEXT, navn TEXT, er_doed INTEGER
);
CREATE INDEX IF NOT EXISTS ix_roles_org ON brreg_roles(orgnr);
CREATE INDEX IF NOT EXISTS ix_roles_person ON brreg_roles(person_key);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT UNIQUE REFERENCES contracts(id),
    title TEXT, buyer TEXT, score REAL,
    status TEXT DEFAULT 'Ny', assignee TEXT DEFAULT '', note TEXT DEFAULT '',
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
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


@contextmanager
def tx(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def get_meta(con, key: str, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


CONTRACT_COLS = [
    "id", "source", "url", "title", "pub_date", "buyer_id", "buyer", "region", "sector", "cpv", "nature",
    "method", "accelerated", "justification", "framework", "num_bidders", "est_value", "award_value",
    "tender_duration_days", "contract_months", "winner_ids", "winner_names", "winner_size", "raw",
]


def upsert_contracts(con, rows: list[dict]) -> int:
    sql = f"INSERT OR REPLACE INTO contracts({','.join(CONTRACT_COLS)}) VALUES ({','.join('?' * len(CONTRACT_COLS))})"
    vals = []
    for r in rows:
        r = dict(r)
        for k in ("winner_ids", "winner_names"):
            r[k] = json.dumps(r.get(k) or [], ensure_ascii=False)
        r["raw"] = json.dumps(r.get("raw"), ensure_ascii=False) if r.get("raw") is not None else None
        vals.append([r.get(c) for c in CONTRACT_COLS])
    with tx(con):
        con.executemany(sql, vals)
    return len(vals)
