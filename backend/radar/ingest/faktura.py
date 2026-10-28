"""Kundens fakturadata (leverandørreskontro / hovedbok) som CSV eller XLSX.

Kunngjorte kontrakter i TED og Doffin viser ikke direkte kjøp og kjøp under terskel. Fakturaene gjør det:
summen per leverandør over 12 måneder sammenlignes med kontraktene radaren kjenner for samme oppdragsgiver.

Forventede kolonner (overskriftene matches løst): leverandørens orgnr, beløp (ekskl. mva), fakturadato,
konto/art. Valgfritt: leverandørnavn, oppdragsgivers orgnr (ellers tatt fra filnavnet, f.eks.
faktura_944444444.csv), fakturanummer.

Personvern og taushet: fakturadata er kundens egne opplysninger. De lagres bare i kundens database,
sendes aldri til en språkmodell eller ekstern tjeneste, og eksporteres bare som summer per leverandør.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..db import tx
from .valg import _find, read_rows

ORGNR = re.compile(r"\d{9}")


def _amount(s: str) -> float | None:
    s = str(s or "").strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:  # 1.234,50 eller 1,234.50
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str) -> str | None:
    s = str(s or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse(rows: list[list[str]], buyer_id: str | None = None) -> tuple[list[dict], int]:
    """Fakturalinjer og antall linjer som ble hoppet over (mangler orgnr, beløp eller dato)."""
    h = [c.strip().lower() for c in rows[0]]
    i_org = _find(h, "leverandør orgnr", "leverandor orgnr", "leverandørens orgnr", "orgnr leverandør", "orgnr", "organisasjonsnummer")
    i_navn = _find(h, "leverandørnavn", "leverandornavn", "leverandør", "leverandor", "navn")
    i_bel = _find(h, "beløp ekskl. mva", "beløp", "belop", "netto", "amount")
    i_dato = _find(h, "fakturadato", "bilagsdato", "dato", "date")
    i_konto = _find(h, "konto", "art", "kontoart")
    i_buyer = _find(h, "oppdragsgiver orgnr", "virksomhet orgnr", "enhet orgnr", "kjøper orgnr")
    i_nr = _find(h, "fakturanummer", "fakturanr", "bilagsnr")
    out, skipped = [], 0
    for r in rows[1:]:
        def g(i, r=r):
            return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""
        org = (ORGNR.search(g(i_org).replace(" ", "")) or [None])[0]
        b = (ORGNR.search(g(i_buyer).replace(" ", "")) or [None])[0] or buyer_id
        amount, d = _amount(g(i_bel)), _date(g(i_dato))
        if not org or amount is None or not d or not b:
            skipped += 1
            continue
        out.append({"buyer_id": b, "supplier_orgnr": org, "supplier_name": g(i_navn) or None, "amount": amount,
                    "date": d, "account": g(i_konto) or None, "invoice_no": g(i_nr) or None})
    return out, skipped


def import_file(con, f: Path, log=print) -> int:
    buyer = (ORGNR.search(f.stem) or [None])[0]
    rows, skipped = parse(read_rows(f.read_bytes(), f.name), buyer)
    with tx(con):
        con.execute("DELETE FROM invoices WHERE source_file=?", (f.name,))
        con.executemany("INSERT INTO invoices(buyer_id, supplier_orgnr, supplier_name, amount, date, account, invoice_no, source_file) "
                        "VALUES (?,?,?,?,?,?,?,?)", [(r["buyer_id"], r["supplier_orgnr"], r["supplier_name"], r["amount"], r["date"],
                                                     r["account"], r["invoice_no"], f.name) for r in rows])
    log(f"  {f.name}: {len(rows)} fakturalinjer" + (f", {skipped} uten orgnr, beløp eller dato" if skipped else ""))
    return len(rows)
