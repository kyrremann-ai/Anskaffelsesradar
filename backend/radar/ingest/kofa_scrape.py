"""Henter KOFA-avgjørelser fra klagenemndssekretariatet.no og skriver kofa.csv for kofa.py.

Avgjørelser fra offentlige organer er ikke vernet (åndsverkloven § 14). Skraperen er høflig:
den leser robots.txt og stopper hvis stien er sperret, venter `--delay` sekunder mellom
forespørsler (standard 3), sender en User-Agent med kontaktadresse og cacher hver side på
disk, så en ny kjøring bare henter det som mangler.

    python -m radar.ingest.kofa_scrape                  # liste + detaljer fra 2024
    python -m radar.ingest.kofa_scrape --since 2020-01-01 --out data/import/kofa.csv

Listen (10 saker per side, ~500 sider) gir saksnr, dato, innklaget, saken gjelder og avgjørelse.
Sakssiden gir klager, gebyr, type anskaffelse og sammendrag. Detaljer hentes bare for saker
som gir fasit (brudd / ikke brudd) og er nyere enn `--since`.
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin

import httpx

BASE = "https://www.klagenemndssekretariatet.no"
LIST_PATH = "/klagenemda-for-offentlige-anskaffelser-kofa/innkomne-avgjorte-saker"
LIST_QUERY = "?status=Avgjort&widget=single"
COLUMNS = ["saksnr", "dato", "innklaget", "innklaget_orgnr", "leverandor", "tittel", "brudd", "ulovlig_direkte", "url",
           "avgjorelse", "type_sak", "saken_gjelder", "gebyr", "type_anskaffelse"]
# Rekkefølgen i tabellen når overskriftene ikke kan leses
LIST_COLS = ["dato", "saksnr", "type_sak", "innklaget", "saken_gjelder", "avgjorelse", "status", "saksbehandler"]
HEADERS = {"dato": "dato", "saknr": "saksnr", "saksnr": "saksnr", "type": "type_sak", "innklaget": "innklaget",
           "saken gjelder": "saken_gjelder", "avgjørelse": "avgjorelse", "status": "status", "saksbeh": "saksbehandler"}
DETAIL_LABELS = {"Innklaget": "innklaget", "Klager": "klager", "Avgjørelse": "avgjorelse", "Type sak": "type_sak",
                 "Saken gjelder": "saken_gjelder", "Avsluttet": "avsluttet", "Type anskaffelse": "type_anskaffelse",
                 "Konkurranseform": "konkurranseform", "Regelverk": "regelverk"}
SAK_RE = re.compile(r"/sak/(\d{4})-(\d+)/?$")


def label(avgjorelse: str) -> int | None:
    """1 = brudd, 0 = ikke brudd, None = ingen realitetsavgjørelse (avvist, trukket, hevet ...)."""
    a = avgjorelse.lower()
    if "ikke brudd" in a:
        return 0
    if "brudd" in a or "overtredelsesgebyr" in a:
        return 1
    return None


class _Doc(HTMLParser):
    """Samler tabellrader (celletekst + lenker) og en flat tekstversjon av siden."""

    BLOCK = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "dt", "dd", "br", "section", "span", "strong", "b"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], list[str], bool]] = []  # (celler, lenker, er_overskrift)
        self.lines: list[str] = []
        self._row = self._cell = None
        self._links: list[str] = []
        self._head = False
        self._buf: list[str] = []
        self._skip = 0

    def _flush(self):
        t = " ".join("".join(self._buf).split())
        if t:
            self.lines.append(t)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag in self.BLOCK:
            self._flush()
        if tag == "tr":
            self._row, self._links, self._head = [], [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._head |= tag == "th"
        elif tag == "a" and self._row is not None:
            href = dict(attrs).get("href")
            if href:
                self._links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        if tag in self.BLOCK:
            self._flush()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append((self._row, self._links, self._head))
            self._row = None

    def handle_data(self, data):
        if self._skip:
            return
        self._buf.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _doc(html: str) -> _Doc:
    d = _Doc()
    d.feed(html)
    d._flush()
    return d


def _iso(d: str) -> str:
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(d.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def parse_list(html: str) -> list[dict]:
    """Rader fra listesiden. Kolonner leses fra overskriften, ellers fast rekkefølge."""
    d = _doc(html)
    cols = LIST_COLS
    out = []
    for cells, links, head in d.rows:
        if head:
            mapped = [next((v for k, v in HEADERS.items() if c.lower().rstrip(".").startswith(k)), "") for c in cells]
            if "saksnr" in mapped:
                cols = mapped
            continue
        sak = next((urljoin(BASE, h) for h in links if SAK_RE.search(h)), None)
        if not sak:
            continue
        r = {k: v for k, v in zip(cols, cells) if k}
        y, n = SAK_RE.search(sak).groups()
        r["saksnr"] = r.get("saksnr") or f"{y}/{n}"
        r["dato"] = _iso(r.get("dato", ""))
        r["url"] = sak
        out.append(r)
    return out


def last_page(html: str) -> int:
    nums = [int(n) for n in re.findall(r"/page/(\d+)", html)]
    return max(nums, default=1)


def parse_detail(html: str) -> dict:
    """Felt fra sakssiden: «Etikett: verdi» på samme linje eller verdien på neste linje."""
    lines = _doc(html).lines
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        for lab, key in DETAIL_LABELS.items():
            if key in out:
                continue
            m = re.match(rf"^{re.escape(lab)}\s*:?\s*(.*)$", line)
            if not m:
                continue
            val = m.group(1).strip()
            if not val and i + 1 < len(lines) and lines[i + 1].rstrip(":") not in DETAIL_LABELS:
                val = lines[i + 1]
            if val:
                out[key] = val
    text = "\n".join(lines)
    g = re.search(r"gebyr[^\n]{0,80}?(\d{1,3}(?:[\s .]\d{3})+|\d{4,})\s*(?:kroner|kr)", text, re.I)
    if g:
        out["gebyr"] = re.sub(r"\D", "", g.group(1))
    # Sammendraget: første lange avsnitt som ikke er en etikett
    out["sammendrag"] = next((l for l in lines if len(l) > 80 and not any(l.startswith(k) for k in DETAIL_LABELS)
                              and "informasjonskapsler" not in l.lower()), "")
    return out


def to_row(lst: dict, det: dict) -> dict:
    avg = det.get("avgjorelse") or lst.get("avgjorelse", "")
    lab = label(avg)
    gjelder = det.get("saken_gjelder") or lst.get("saken_gjelder", "")
    return {
        "saksnr": lst["saksnr"], "dato": lst.get("dato", ""), "innklaget": det.get("innklaget") or lst.get("innklaget", ""),
        "innklaget_orgnr": "", "leverandor": det.get("klager", ""), "tittel": det.get("sammendrag", "")[:300],
        "brudd": "" if lab is None else ("ja" if lab else "nei"),
        "ulovlig_direkte": "ja" if "ulovlig direkte" in gjelder.lower() else "nei", "url": lst.get("url", ""),
        "avgjorelse": avg, "type_sak": det.get("type_sak") or lst.get("type_sak", ""), "saken_gjelder": gjelder,
        "gebyr": det.get("gebyr", ""), "type_anskaffelse": det.get("type_anskaffelse", ""),
    }


class Client:
    def __init__(self, cache: Path, delay: float, user_agent: str):
        self.cache, self.delay, self._last = cache, delay, 0.0
        self.http = httpx.Client(headers={"User-Agent": user_agent}, timeout=60, follow_redirects=True)
        self.robots = robotparser.RobotFileParser()
        r = self.http.get(BASE + "/robots.txt")
        self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        self.user_agent = user_agent
        cache.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, key: str, refresh: bool = False) -> str:
        f = self.cache / f"{key}.html"
        if f.exists() and not refresh:
            return f.read_text(encoding="utf-8")
        if not self.robots.can_fetch(self.user_agent, url):
            raise PermissionError(f"robots.txt sperrer {url}")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            r = self.http.get(url)
            self._last = time.monotonic()
            if r.status_code in (429, 503):
                time.sleep(int(r.headers.get("Retry-After", 30 * (attempt + 1))))
                continue
            r.raise_for_status()
            f.write_text(r.text, encoding="utf-8")
            return r.text
        raise RuntimeError(f"Ga opp etter gjentatte 429/503: {url}")


def scrape(client: Client, since: str, max_pages: int | None = None, log=print) -> list[dict]:
    first = client.get(BASE + LIST_PATH + LIST_QUERY, "list-1", refresh=True)
    pages = min(last_page(first), max_pages or 10**6)
    listed = parse_list(first)
    for p in range(2, pages + 1):
        # Nyeste først: sider som er eldre enn since trengs ikke til detaljer, men gir fortsatt rader
        html = client.get(f"{BASE}{LIST_PATH}/page/{p}{LIST_QUERY}", f"list-{p}", refresh=p <= 3)
        listed += parse_list(html)
        if p % 25 == 0:
            log(f"Liste: side {p}/{pages}, {len(listed)} saker")
    seen, rows = set(), []
    want = [r for r in listed if r["saksnr"] not in seen and not seen.add(r["saksnr"])]
    todo = {r["saksnr"] for r in want if r.get("dato", "") >= since and label(r.get("avgjorelse", "")) is not None}
    log(f"{len(want)} saker i listen, henter detaljer for {len(todo)} fra {since}")
    for i, r in enumerate(want, 1):
        det = {}
        if r["saksnr"] in todo:
            try:
                det = parse_detail(client.get(r["url"], "sak-" + r["saksnr"].replace("/", "-")))
            except httpx.HTTPError as e:
                log(f"{r['saksnr']}: {e}")
        rows.append(to_row(r, det))
        if i % 100 == 0:
            log(f"Detaljer: {i}/{len(want)}")
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    from ..config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=settings.import_dir / "kofa.csv")
    ap.add_argument("--since", default="2024-01-01", help="hent saksdetaljer for saker avgjort fra denne datoen")
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--max-pages", type=int)
    a = ap.parse_args()
    c = Client(settings.data_dir / "cache" / "kofa", a.delay, settings.brreg_user_agent)
    rows = scrape(c, a.since, a.max_pages)
    # Alle saker til cache, bare saker med fasit til importmappen (kofa.py leser tom «brudd» som nei)
    write_csv(rows, settings.data_dir / "cache" / "kofa_alle.csv")
    rows = [r for r in rows if r["brudd"]]
    write_csv(rows, a.out)
    n1 = sum(r["brudd"] == "ja" for r in rows)
    n0 = sum(r["brudd"] == "nei" for r in rows)
    print(f"Skrev {len(rows)} saker til {a.out} ({n1} brudd, {n0} ikke brudd)")
