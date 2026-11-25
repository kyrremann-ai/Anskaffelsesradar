"""Fakturakontroll: kundens kjøp per leverandør sammenlignet med kunngjorte kontrakter.

Observasjonene er utgangspunkt for kontroll, ikke konklusjoner. Terskelverdien gjelder samlet anslått verdi
for hele kontraktsperioden (opptil fire år), og kjøpet kan være dekket av en kontrakt inngått før radarens
periode, en felles innkjøpsordning eller et unntak. Radaren ser bare kontrakter kunngjort i TED og Doffin.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from .ingest.brreg import PUBLIC_FORMS
from .scoring import NATIONAL_THRESHOLD

# Nedre grense for anskaffelsesloven (§ 2): 100 000 kr ekskl. mva, hevet til 500 000 kr fra 1.7.2026
# (lov 6. mars 2026 nr. 8). Kjøp før og etter endringen vurderes hver for seg mot grensen som gjaldt.
LAW_CHANGE = date(2026, 7, 1)
LIMIT_BEFORE, LIMIT_FROM = 100_000, 500_000


def _kr(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def lower_limit(d: date) -> int:
    """Anskaffelseslovens nedre grense (kr ekskl. mva) som gjaldt på datoen."""
    return LIMIT_FROM if d >= LAW_CHANGE else LIMIT_BEFORE


OVER_CONTRACT = 1.2  # fakturert mer enn 20 % over kunngjort kontraktsverdi


def _max_12m(lines: list[tuple[date, float]]) -> tuple[float, date | None]:
    """Høyeste sum innenfor et vindu på 12 måneder, og vinduets startdato."""
    best, start, j, run = 0.0, None, 0, 0.0
    for i, (d, a) in enumerate(lines):
        run += a
        while lines[j][0] <= d - timedelta(days=365):
            run -= lines[j][1]
            j += 1
        if run > best:
            best, start = run, lines[j][0]
    return best, start


def analyse(con, buyers: set[str] | None = None) -> dict:
    inv = defaultdict(list)
    names, accounts = {}, defaultdict(lambda: defaultdict(float))
    for r in con.execute("SELECT buyer_id, supplier_orgnr, supplier_name, amount, date, account FROM invoices"):
        if buyers and r["buyer_id"] not in buyers:
            continue
        k = (r["buyer_id"], r["supplier_orgnr"])
        inv[k].append((date.fromisoformat(r["date"]), r["amount"]))
        names.setdefault(r["supplier_orgnr"], r["supplier_name"])
        accounts[k][r["account"] or "–"] += r["amount"]
    if not inv:
        return {"rows": [], "period": None, "radar_from": None}

    # Kunngjorte kontrakter og intensjoner per oppdragsgiver og leverandør (duplikater er slått sammen)
    contracts = defaultdict(list)
    for r in con.execute("""SELECT c.id, c.source, c.url, c.title, c.pub_date, c.buyer_id, c.winner_ids, c.award_value, c.est_value, c.framework,
                                   COALESCE(o.notice_type, 'tildeling') AS notice_type
                            FROM contracts c LEFT JOIN observasjon o ON o.contract_id = c.id
                            WHERE o.duplicate_of IS NULL AND COALESCE(o.notice_type, 'tildeling') IN ('tildeling', 'intensjon')"""):
        for w in json.loads(r["winner_ids"] or "[]"):
            contracts[(r["buyer_id"], w)].append(dict(r))
    radar_from = con.execute("SELECT MIN(pub_date) AS d FROM contracts").fetchone()["d"]
    forms = {r["orgnr"]: json.loads(r["data"] or "{}").get("enhet", {}).get("organisasjonsform", {}).get("kode")
             for r in con.execute("SELECT orgnr, data FROM brreg_entities")}

    rows, all_dates = [], []
    for (buyer, sup), lines in inv.items():
        lines.sort()
        all_dates += [lines[0][0], lines[-1][0]]
        total = sum(a for _, a in lines)
        peak, peak_from = _max_12m(lines)
        cs = contracts.get((buyer, sup), [])
        awarded = [c for c in cs if c["notice_type"] == "tildeling"]
        value = sum(c["award_value"] or c["est_value"] or 0 for c in awarded)
        checks = []
        public = forms.get(sup) in PUBLIC_FORMS
        if not cs and not public:
            if peak >= NATIONAL_THRESHOLD:
                checks.append({"key": "uten_kontrakt_over_terskel", "label": "Kjøp over nasjonal terskelverdi uten kunngjort kontrakt",
                               "why": f"{peak:,.0f} kr på 12 måneder fra {peak_from}; ingen kontrakt eller intensjon i TED/Doffin".replace(",", " ")})
            else:
                # Hver periode mot grensen som gjaldt da, så et vindu ikke blander gammel og ny grense
                for part in ([x for x in lines if x[0] < LAW_CHANGE], [x for x in lines if x[0] >= LAW_CHANGE]):
                    if not part:
                        continue
                    p, p_from = _max_12m(part)
                    limit = lower_limit(part[0][0])
                    if p >= limit:
                        when = "fra 1.7.2026" if limit == LIMIT_FROM else "før 1.7.2026"
                        checks.append({"key": "uten_kontrakt_over_lovgrense", "label": f"Kjøp over {_kr(limit)} kr uten kunngjort kontrakt",
                                       "why": f"{_kr(p)} kr på 12 måneder fra {p_from}; anskaffelsesloven gjelder fra "
                                              f"{_kr(limit)} kr ekskl. mva ({when}, § 2)"})
                        break
        if value and not any(c["framework"] for c in awarded) and total > OVER_CONTRACT * value:
            checks.append({"key": "over_kontraktsverdi", "label": "Fakturert mer enn kunngjort kontraktsverdi",
                           "why": f"{total:,.0f} kr fakturert mot {value:,.0f} kr kunngjort".replace(",", " ")})
        rows.append({
            "buyer_id": buyer, "orgnr": sup, "name": names.get(sup), "public": public,
            "n": len(lines), "total": round(total, 2), "max_12m": round(peak, 2), "first": lines[0][0].isoformat(),
            "last": lines[-1][0].isoformat(), "accounts": sorted(({"account": k, "sum": round(v, 2)} for k, v in accounts[(buyer, sup)].items()),
                                                                 key=lambda x: -x["sum"])[:3],
            "contracts": [{"id": c["id"], "source": c["source"], "url": c["url"], "title": c["title"], "pub_date": c["pub_date"],
                           "value": c["award_value"] or c["est_value"], "type": c["notice_type"]} for c in cs],
            "checks": checks,
        })
    rows.sort(key=lambda r: (-len(r["checks"]), -r["max_12m"]))
    return {"rows": rows, "period": [min(all_dates).isoformat(), max(all_dates).isoformat()], "radar_from": radar_from}
