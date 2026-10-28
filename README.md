# Anskaffelsesradar

Prioriterer offentlige anskaffelser for kommunerevisjon. Data hentes fra TED, Doffin og Brønnøysundregistrene, og hver kontrakt får røde flagg, tre ML-modeller og avviksdeteksjon samlet i én prioritet. Revisoren jobber i en arbeidsflate med saker, logg og arbeidspapir.

> Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.
> Modellene er trent på syntetiske data, som i eksamensrapporten (Foros 2026). Neste steg er ekte fasit fra KOFA.

## Hva er nytt i v2

- **Mine enheter**: velg kommunene og foretakene du reviderer. Hele appen filtreres på dem, og nøkkeltall sammenlignes med hele Norge.
- **Arbeidsflate**: prioritert kø og saksmappe side om side. Erstatter Risikoregister og Saksliste.
- **Én prioritetsscore (0–100)**. ML, CRI og avvik vises som delscorer.
- **Brønnøysund**: Enhetsregisteret og roller per leverandør og oppdragsgiver. Gir 11 nye flagg, blant annet stiftet etter kunngjøring, konkurs, ikke MVA-registrert, felles styremedlem med oppdragsgiver og felles roller eller adresse mellom konkurrerende leverandører. Ny side **Leverandører** med koblinger via personer.
- **Sporbarhet**: alle endringer på en sak logges med hvem og når. AI-vurderinger lagres med modell, tidspunkt og feltene som ble sendt. Hver sak har et arbeidspapir for utskrift eller PDF.
- **AI via Mistral (EU)** bak en adapter. Uten nøkkel brukes en regelbasert mal. Personnavn sendes aldri til LLM.
- **Ingen tredjepart i nettleseren**: ingen Google Fonts, ingen sporingsskript.
- **Kjører på egen server**: Docker Compose med Caddy (HTTPS og passord) på Hetzner.

## Arkitektur

```
backend/radar/
  ingest/ted.py      TED Search API v3 → eForms-XML per kunngjøring (cache på disk)
  ingest/doffin.py   Doffin Public API v2 (live-visning, krever gratis nøkkel)
  ingest/brreg.py    Enhetsregisteret + roller, Brreg-flagg
  synthetic.py       treningsgenerator fra rapporten + fiktive demodata
  scoring.py         røde flagg (CRI), LR/RF/GB, Isolation Forest, prioritet
  llm.py             Mistral-adapter + regelbasert fallback
  pipeline.py        innhenting → scoring («Hent nye data», cron, CLI)
  api.py             FastAPI, serverer også den bygde klienten
frontend/            React + Vite + Tailwind + Recharts
```

SQLite i `/data/radar.sqlite`. Ingen ORM, så det er enkelt å bytte til Postgres senere.

## Kjør lokalt

```bash
# API (demodata lastes automatisk første gang)
cd backend && pip install -r requirements-dev.txt
uvicorn radar.api:app --reload            # http://127.0.0.1:8000/api/summary
pytest -q

# Klient
cd frontend && npm install && npm run dev # http://localhost:5173 (proxy til API)
```

Ekte data: `RADAR_MODE=live python -m radar.pipeline`. Første kjøring henter rundt 7 000 kunngjøringer fra TED og 5 500 orgnr fra Brreg, og tar 20–40 minutter. Etter det hentes bare nye.

## Produksjon (Hetzner)

```bash
cp .env.example .env    # fyll inn DOMAIN, BASIC_AUTH_*, PERSON_SALT, ev. MISTRAL_API_KEY og DOFFIN_API_KEY
docker compose up -d --build
```

En CX22 (2 vCPU, 4 GB) holder. Sett `REFRESH_HOURS=24` for daglig oppdatering.

## Kostnad

- TED, Brreg og Doffin er gratis.
- Mistral small koster typisk under 1 øre per vurdering. Vurderinger kjøres bare når revisor ber om det, og lagres.
- Hetzner CX22 koster rundt 5 € per måned.

## API

| Metode | Sti | |
|---|---|---|
| GET | `/api/summary?buyers=` | nøkkeltall, månedlig, flagg, sektor/region/oppdragsgiver |
| GET | `/api/contracts?buyers=&q=&tier=&flag=&case_status=&supplier=` | prioritert liste |
| GET | `/api/contracts/{id}` | saksmappe: flagg, drivere, leverandør (Brreg), sak, AI |
| POST | `/api/contracts/{id}/assess` | ny AI-vurdering (lagres) |
| GET | `/api/contracts/export.csv` | CSV (semikolon, norsk desimal) |
| GET | `/api/suppliers`, `/api/suppliers/{orgnr}` | leverandører, roller, koblinger, regnskap, eiere, registre |
| GET/POST/PATCH/DELETE | `/api/cases…` | saker med logg |
| GET | `/api/cases/{id}/arbeidspapir` | utskriftsvennlig arbeidspapir |
| GET | `/api/doffin/latest?q=` | Doffin live |
| GET | `/api/model` | modellytelse, viktigste felt, vekter |
| POST | `/api/refresh` | start innhenting og scoring |

## Tilleggskilder

Hver kilde kjøres for seg ved «Hent nye data». En kilde som feiler stopper ikke de andre, og status vises på metodesiden. Flaggene gir et tillegg i prioriteten (se `radar/enrich.py`).

| Kilde | Hvordan | Tilgang | Flagg |
|---|---|---|---|
| Regnskapsregisteret | API per orgnr (`REGNSKAP=1`) | Åpent | kontrakt > 50 % av omsetning, negativ egenkapital, AS uten regnskap |
| Aksjonærregisteret (Skatteetaten) | Fil i `data/import/aksjonaer<år>.csv` | Fil fra Skatteetaten eller aksjeeiere.no | felles eier med konkurrent, eier har rolle hos oppdragsgiver |
| Proff API | `PROFF_API_KEY` | Betalt avtale med Proff. Vi skraper ikke proff.no | eiere, som over |
| Renholdsregisteret | `RENHOLD_REGISTER_URL` (XML) | Åpent (NLOD) | leverandør på renhold (CPV 9091) ikke godkjent |
| Bemanningsregisteret | `BEMANNING_REGISTER_URL` | Åpent (NLOD) | leverandør på vikar/bemanning (CPV 7962) ikke godkjent |
| Kandidatlister (valg.no) | Fil i `data/import/kandidat*.csv` | Åpent | mulig kobling til lokalpolitiker (navn + fødselsår, samme kommune) |
| OpenSanctions | `OPENSANCTIONS_URL` | CC BY-NC: lisens kreves ved salg | sanksjonert selskap/person, PEP blant roller/eiere |
| KOFA | Fil i `data/import/kofa*.csv` eller `.json` | Kuratert eksport | konstatert brudd. Koblede saker brukes som ekte fasit i treningen (fra 30 per klasse) |

Filimport kan også kjøres for hånd: `python -m radar.ingest.owners fil.csv --year 2024`, `python -m radar.ingest.valg fil.csv`, `python -m radar.ingest.kofa fil.csv`.

Personvern: kandidatlister, PEP og personlige eiere er personopplysninger. Treff vises som «mulig kobling» og må verifiseres. Gjør en DPIA før produksjon, og send aldri slike data til en LLM.

## Kjente begrensninger

- Treningsdata er syntetiske (rapportens generator). `amendment_count` og `subcontractor_pct` er tatt ut av modellen fordi TED ikke har dem.
- Rammeavtaler oppgir ofte maksverdi. Overskridelsesflagget får derfor halv vekt på rammeavtaler.
- Reelle rettighetshavere er ikke åpent. Eierskap dekkes av Aksjonærregisteret (årlig fil, ett år gammel) og eventuelt Proff API.
- Adapterne for Regnskapsregisteret, Arbeidstilsynet, OpenSanctions og Proff er testet mot fixtures, ikke mot live-tjenestene. Proff-feltnavn må bekreftes mot deres dokumentasjon når avtalen er på plass.
- Matching av personer på tvers av virksomheter bruker navn og fødselsdato (hashet med salt).
