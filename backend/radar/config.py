"""Konfigurasjon fra miljøvariabler. Ingen hemmeligheter i koden."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("RADAR_DATA_DIR", "data")))
    # "demo" fyller databasen med syntetiske kontrakter ved første oppstart.
    # "live" henter fra TED/Doffin/Brreg.
    mode: str = field(default_factory=lambda: _env("RADAR_MODE", "demo"))
    ted_from: str = field(default_factory=lambda: _env("TED_FROM", "2025-01-01"))
    doffin_api_key: str = field(default_factory=lambda: _env("DOFFIN_API_KEY"))
    mistral_api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    # Billigste modell som skriver godt norsk. Bytt til mistral-medium-latest ved behov.
    mistral_model: str = field(default_factory=lambda: _env("MISTRAL_MODEL", "mistral-small-latest"))
    person_salt: str = field(default_factory=lambda: _env("PERSON_SALT", "bytt-meg"))
    brreg_user_agent: str = field(
        default_factory=lambda: _env("BRREG_USER_AGENT", "Anskaffelsesradar/2.0 (kontakt: post@example.no)")
    )
    # Automatisk oppdatering (timer). 0 = av.
    refresh_hours: float = field(default_factory=lambda: float(_env("REFRESH_HOURS", "0")))
    seed: int = 42

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radar.sqlite"


settings = Settings()
