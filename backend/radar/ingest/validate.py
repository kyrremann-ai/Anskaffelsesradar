"""Skjemavalidering i ingest-laget.

Offentlige feeder endrer felt og koder uten varsel. Uten validering gir en formatendring bare «0 treff»,
som ser ut som at alt er i orden. Her valideres hver rad med Pydantic før den lagres, og kilden får
en advarsel (vises på metodesiden og i radar.check) når:
- rader ikke passer skjemaet (de forkastes, første feil vises),
- et kjernefelt mangler i nesten alle rader (typisk omdøpt felt hos kilden),
- kilden gir langt færre rader enn forventet.

Bruk i en adapter:  rows = validate.check("regnskap", rows, RegnskapRow, core=("driftsinntekter",))
Pipelinen samler advarslene per kilde (se pipeline._source).
"""
from __future__ import annotations

from typing import Annotated, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Orgnr = Annotated[str, Field(pattern=r"^\d{9}$")]
Year = Annotated[int, Field(ge=1850, le=2100)]

# Advarsler for kilden som kjører nå. pipeline._source setter og leser listen.
current: list[str] | None = None


def warn(msg: str) -> None:
    if current is not None and msg not in current:
        current.append(msg)
    print(f"Advarsel: {msg}", flush=True)


class _Row(BaseModel):
    model_config = ConfigDict(extra="allow")


class RegnskapRow(_Row):
    orgnr: Orgnr
    aar: Year | None = None
    driftsinntekter: float | None = None
    driftsresultat: float | None = None
    aarsresultat: float | None = None
    egenkapital: float | None = None
    gjeld: float | None = None
    eiendeler: float | None = None


class RegnskapAarRow(_Row):
    orgnr: Orgnr
    aar: Year
    type: Annotated[str, Field(pattern=r"^(SELSKAP|KONSERN)$")]
    driftsinntekter: float | None = None
    egenkapital: float | None = None


class RegisterRow(_Row):
    orgnr: Orgnr
    navn: str = ""
    status: Annotated[str, Field(min_length=1)]


class SanctionRow(_Row):
    id: Annotated[str, Field(min_length=1)]
    schema_: str | None = Field(None, alias="schema")
    name: Annotated[str, Field(min_length=1)]
    name_norm: Annotated[str, Field(min_length=1)]
    birth_year: Year | None = None
    orgnr: Orgnr | None = None
    is_sanction: Annotated[int, Field(ge=0, le=1)]
    datasets: str = ""


class OwnerRow(_Row):
    orgnr: Orgnr
    owner_name: Annotated[str, Field(min_length=1)]
    owner_orgnr: Orgnr | None = None
    owner_birth_year: Year | None = None
    share_pct: Annotated[float, Field(ge=0, le=100)] | None = None

    @field_validator("owner_orgnr", mode="before")
    @classmethod
    def _empty_none(cls, v):
        return v or None


def check(source: str, rows: Iterable[dict], model: type[BaseModel], core: tuple[str, ...] = (),
          min_rows: int = 0, min_core_share: float = 0.05) -> list[dict]:
    """Validerer radene. Gyldige rader returneres uendret (som dict); ugyldige forkastes med advarsel."""
    rows = list(rows)
    ok, bad, first = [], 0, None
    for r in rows:
        try:
            model.model_validate(r)
            ok.append(r)
        except ValidationError as e:
            bad += 1
            if first is None:
                err = e.errors()[0]
                first = f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
    if bad:
        warn(f"{source}: {bad} av {len(rows)} rader passet ikke skjemaet og ble forkastet ({first})")
    for f in core:
        have = sum(r.get(f) is not None for r in ok)
        if len(ok) >= 20 and have / len(ok) < min_core_share:
            warn(f"{source}: feltet «{f}» mangler i {len(ok) - have} av {len(ok)} rader. Mulig formatendring hos kilden.")
    if len(ok) < min_rows:
        warn(f"{source}: bare {len(ok)} gyldige rader (forventet minst {min_rows}). Mulig formatendring eller tom feed.")
    return ok
