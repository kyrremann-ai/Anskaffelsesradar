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
| GET | `/api/suppliers`, `/api/suppliers/{orgnr}` | leverandører, roller, koblinger |
| GET/POST/PATCH/DELETE | `/api/cases…` | saker med logg |
| GET | `/api/cases/{id}/arbeidspapir` | utskriftsvennlig arbeidspapir |
| GET | `/api/doffin/latest?q=` | Doffin live |
| GET | `/api/model` | modellytelse, viktigste felt, vekter |
| POST | `/api/refresh` | start innhenting og scoring |

## Kjente begrensninger

- Treningsdata er syntetiske (rapportens generator). `amendment_count` og `subcontractor_pct` er tatt ut av modellen fordi TED ikke har dem.
- Rammeavtaler oppgir ofte maksverdi. Overskridelsesflagget får derfor halv vekt på rammeavtaler.
- Reelle rettighetshavere og Aksjonærregisteret er ikke åpne. Eierskap dekkes foreløpig bare via roller.
- Matching av personer på tvers av virksomheter bruker navn og fødselsdato (hashet med salt).
