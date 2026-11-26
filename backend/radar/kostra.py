"""SSB KOSTRA-grupper: kommuner gruppert etter folkemengde og økonomiske rammebetingelser.

Brukes til å sammenligne en kommune med lignende kommuner i stedet for med hele landet.
Grupperingen for 2024 ligger i referansedata/kostra_grupper_2024.csv (fra SSB, 357 kommuner, gruppe 1–17).
En nyere fil kan legges i data/import/ som kostra*.csv (kommunenr;kommune;kostragruppe) eller kostra*.xlsx
(kolonnene komnr, komnavn, kostragr). Nyeste årstall i filnavnet vinner.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

BUNDLED = Path(__file__).parent / "referansedata" / "kostra_grupper_2024.csv"


def _rows_xlsx(path: Path) -> list[list]:
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _rows_csv(path: Path) -> list[list]:
    text = path.read_text(encoding="utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";" if ";" in text.split("\n", 1)[0] else ","))


def parse(rows: list[list]) -> dict[str, int]:
    """Kommunenr (4 sifre) -> KOSTRA-gruppe. Første rad er overskrifter; kolonnene finnes på navn."""
    if not rows:
        return {}
    head = [str(h or "").strip().lower() for h in rows[0]]
    k = next(i for i, h in enumerate(head) if h.startswith(("komnr", "kommunenr", "knr")))
    g = next(i for i, h in enumerate(head) if "kostra" in h or h.startswith("gruppe"))
    out = {}
    for r in rows[1:]:
        nr = re.sub(r"\D", "", str(r[k] if k < len(r) and r[k] is not None else ""))
        grp = re.sub(r"\D", "", str(r[g] if g < len(r) and r[g] is not None else ""))
        if nr and grp and len(nr) <= 4:
            out[nr.zfill(4)] = int(grp)
    return out


def load(import_dir: Path | None = None) -> tuple[dict[str, int], str]:
    """Grupperingen som brukes, og hvilken fil den kom fra."""
    files = sorted([*import_dir.glob("kostra*.csv"), *import_dir.glob("kostra*.xlsx")]) if import_dir and import_dir.exists() else []
    files = sorted(files, key=lambda f: max(re.findall(r"20\d\d", f.name) or ["0"]))
    for f in reversed(files):
        try:
            m = parse(_rows_xlsx(f) if f.suffix == ".xlsx" else _rows_csv(f))
        except Exception:  # noqa: BLE001 - feil fil skal ikke stoppe scoringen
            continue
        if len(m) >= 300:
            return m, f.name
    if not BUNDLED.exists():  # uten gruppering faller sammenligningen tilbake på oppdragsgivertype
        return {}, ""
    return parse(_rows_csv(BUNDLED)), BUNDLED.name
