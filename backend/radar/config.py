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
    # Doffin-tildelinger i live-modus. Nøkkelen kan også legges på av en proxy, så den kreves ikke her.
    # Journalposter fra eInnsyn for prioriterte kontrakter (åpent API)
    einnsyn: bool = field(default_factory=lambda: _env("EINNSYN", "på").lower() not in ("av", "0", "false"))
    doffin: bool = field(default_factory=lambda: _env("DOFFIN", "på").lower() not in ("av", "0", "false"))
    mistral_api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    # Billigste modell som skriver godt norsk. Bytt til mistral-medium-latest ved behov.
    mistral_model: str = field(default_factory=lambda: _env("MISTRAL_MODEL", "mistral-small-latest"))
    person_salt: str = field(default_factory=lambda: _env("PERSON_SALT", "bytt-meg"))
    brreg_user_agent: str = field(
        default_factory=lambda: _env("BRREG_USER_AGENT", "Anskaffelsesradar/2.0 (kontakt: post@example.no)")
    )
    # Automatisk oppdatering (timer). 0 = av.
    refresh_hours: float = field(default_factory=lambda: float(_env("REFRESH_HOURS", "0")))
    # Tilleggskilder. Tomme verdier = av.
    regnskap: bool = field(default_factory=lambda: _env("REGNSKAP", "1") == "1")
    proff_api_key: str = field(default_factory=lambda: _env("PROFF_API_KEY"))
    proff_base_url: str = field(default_factory=lambda: _env("PROFF_BASE_URL", "https://api.proff.no"))
    proff_auth_scheme: str = field(default_factory=lambda: _env("PROFF_AUTH_SCHEME", "Token"))
    renhold_register_url: str = field(default_factory=lambda: _env("RENHOLD_REGISTER_URL", "https://registerdata.arbeidstilsynet.no/renhold_register.xml"))
    bemanning_register_url: str = field(default_factory=lambda: _env("BEMANNING_REGISTER_URL"))
    # OpenSanctions er CC BY-NC: krever lisens ved kommersiell bruk. Av som standard.
    opensanctions_url: str = field(default_factory=lambda: _env("OPENSANCTIONS_URL"))
    opensanctions_api_key: str = field(default_factory=lambda: _env("OPENSANCTIONS_API_KEY"))
    seed: int = 42

    @property
    def import_dir(self) -> Path:
        """Filer lagt her importeres ved neste oppdatering: aksjonaer*.csv, kandidat*.csv, kofa*.csv/json."""
        return self.data_dir / "import"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radar.sqlite"


settings = Settings()
