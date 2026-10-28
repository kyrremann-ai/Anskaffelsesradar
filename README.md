# Anskaffelsesradar

Prioriterer offentlige anskaffelser for kommunerevisjon. Data hentes fra TED, Doffin og Brønnøysundregistrene, og hver kontrakt får regelbaserte røde flagg og registerflagg samlet i én prioritet, pluss en eksperimentell klagerisiko trent på ekte KOFA-saker. Revisoren jobber i en arbeidsflate med saker, logg og arbeidspapir.

> Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.
> ML-modellene fra eksamensrapporten (Foros 2026, syntetiske data) er fjernet fra appen: de traff ikke bedre enn tilfeldig på ekte KOFA-saker.

## Hva er nytt i v2

- **Mine enheter**: velg kommunene og foretakene du reviderer. Hele appen filtreres på dem, og nøkkeltall sammenlignes med hele Norge.
- **Arbeidsflate**: prioritert kø og saksmappe side om side. Erstatter Risikoregister og Saksliste.
- **Én prioritetsscore (0–100)** fra røde flagg (CRI) og registerflagg. Klagerisiko vises ved siden av og teller ikke.
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
  synthetic.py       fiktive demodata (+ rapportens treningsgenerator, brukt som baseline i kofa_eval)
  scoring.py         røde flagg (CRI), registerflagg, prioritet, klagerisiko
  klagerisiko.py     modell for KOFA-klage, lagret i models/klagerisiko.json
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

## Artifact-versjon (uten server)

Klienten kan bygges som en artifact på claude.ai. Da regner nettleseren ut oversikt og filtre fra JSON-filer, og saker lagres i artifactens delte database.

```bash
RADAR_MODE=live deploy/build_artifact.sh ut/    # eller RADAR_DEMO=1 for demodata
```

Publiser `ut/index.html` med alle `*.json` i mappen som filer (`meta.json` og dataene delt i `contracts-N.json` og `cards-N.json`, under grensen på 16 MB per fil), og kapabilitetene `db`, `user` (profile) og `downloads`. En daglig rutine kjører skriptet og publiserer på nytt til samme lenke.

Begrensninger: brukerne må ha tilgang via claude.ai, dataene lagres hos Anthropic, AI-vurdering og Doffin live er av, og arbeidspapir/CSV lastes ned som filer.

## Demo på radar.vikingsea.no (Cloudflare Pages + Access)

Samme klient og JSON-filer som artifacten, men på eget domene. Bare inviterte e-poster kommer inn (engangskode fra Cloudflare Access, gratis opptil 50 brukere). Saker lagres i Cloudflare D1 via Pages Functions (`deploy/cloudflare/functions`), og loggen viser Access-e-posten til den som endret.

Engangsoppsett:
1. Cloudflare API-token med Account: Pages Edit, D1 Edit, Access: Apps and Policies Edit, Access: Audit Logs Read, og Zone (vikingsea.no): DNS Edit + Zone Read.
2. GitHub-hemmeligheter: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `DOFFIN_API_KEY`.
3. Actions → «Radar-tilgang» → `setup` (Pages-prosjekt, D1, CNAME `radar`, Access-apper).
4. Actions → «Radar på Cloudflare» bygger og publiserer. Den kjører så hver natt kl. 04.

Tilgang: Actions → «Radar-tilgang» → `grant`/`revoke` + e-post, eller lokalt:

```bash
python deploy/cf_radar.py invite kari@firma.no Kari   # bruker ~/.config/havbruk/env som Havbruk Signal
RADAR_DEMO=1 deploy/build_pages.sh ut-pages/ && python deploy/cf_radar.py deploy ut-pages/
```

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
| Regnskapsregisteret | API per orgnr (`REGNSKAP=1`), alle år og konsernregnskap | Åpent | årlig kontraktsverdi > 50 % av omsetning i tildelingsåret, negativ egenkapital, AS uten regnskap |
| Brreg endringslogg | `oppdateringer/enheter` og `oppdateringer/roller`, bare for leverandører med felles adresse/roller eller uten ansatte | Åpent | avgjør om adresse, ansatte og roller gjaldt på tildelingstidspunktet |
| Fusjon/fisjon/omdanning | Fil i `data/import/endringer*.csv` (`fra_orgnr;til_orgnr;type;dato`) | Kunngjøringer i Brreg | to orgnr regnes som samme virksomhet |
| Aksjonærregisteret (Skatteetaten) | Fil i `data/import/aksjonaer<år>.csv` | Fil fra Skatteetaten eller aksjeeiere.no | felles eier med konkurrent, eier har rolle hos oppdragsgiver |
| Proff API | `PROFF_API_KEY` | Betalt avtale med Proff. Vi skraper ikke proff.no | eiere, som over |
| Renholdsregisteret | `RENHOLD_REGISTER_URL` (XML) | Åpent (NLOD) | leverandør på renhold (CPV 9091) ikke godkjent |
| Bemanningsregisteret | `BEMANNING_REGISTER_URL` | Åpent (NLOD) | leverandør på vikar/bemanning (CPV 7962) ikke godkjent |
| Kandidatlister (valg.no) | Fil i `data/import/kandidat*.csv` | Åpent | mulig kobling til lokalpolitiker (navn + fødselsår, samme kommune) |
| OpenSanctions | `OPENSANCTIONS_URL` | CC BY-NC: lisens kreves ved salg | sanksjonert selskap/person, PEP blant roller/eiere |
| KOFA | Fil i `data/import/kofa*.csv` eller `.json`, lages med `python -m radar.ingest.kofa_scrape` | Offentlige avgjørelser (åvl. § 14) | konstatert brudd. Koblede saker brukes som ekte fasit i treningen (fra 30 per klasse) |

KOFA-skraperen henter fra klagenemndssekretariatet.no (www.klagenemndssekretariatet.no må være tillatt i nettverket). Den følger robots.txt, venter 3 sek mellom kall og cacher sidene i `data/cache/kofa/`. Saker uten realitetsavgjørelse (avvist, trukket) tas ikke med. `python -m radar.ingest.kofa_link` kobler sakene til Doffin-kunngjøringer (også konkurranser uten tildeling), og `python -m radar.kofa_models` sammenligner modellvalg med kryssvalidert AUC og 95 % konfidensintervall. `python -m radar.klagerisiko hent|test` måler **klagerisiko**: hvor sannsynlig det er at en konkurranse havner i KOFA (ikke korrupsjonsrisiko). Mål modellene mot KOFA-fasiten med `python -m radar.kofa_eval`: AUC for syntetisk modell testet på KOFA, modell trent bare på KOFA (CV) og begge sammen.

Filimport kan også kjøres for hånd: `python -m radar.ingest.owners fil.csv --year 2024`, `python -m radar.ingest.valg fil.csv`, `python -m radar.ingest.kofa fil.csv`.

Personvern: kandidatlister, PEP og personlige eiere er personopplysninger. Treff vises som «mulig kobling» og må verifiseres. Gjør en DPIA før produksjon, og send aldri slike data til en LLM.

## Kjente begrensninger

- Klagerisiko er trent på KOFA-saker koblet til Doffin med navnesøk. Kommuner er lettere å koble enn statlige etater, så kommune-effekten kan være delvis kunstig.
- Rammeavtaler oppgir ofte maksverdi. Overskridelsesflagget får derfor halv vekt på rammeavtaler.
- Leverandørflaggene (omsetning, ansatte, adresse, roller, eiere) sjekkes mot alternative forklaringer før de vises: se `radar/supplier_context.py` og testplanen i `docs/testplan-leverandorflagg.md`. Brregs endringslogg har detaljer om hva som endret seg bare for nyere endringer (fra ca. 2026), og rollefeeden sier bare *at* roller endret seg. Fusjon og fisjon finnes ikke i det åpne API-et og må legges inn som fil.
- Reelle rettighetshavere er ikke åpent. Eierskap dekkes av Aksjonærregisteret (årlig fil, ett år gammel) og eventuelt Proff API.
- Adapterne for Regnskapsregisteret, Arbeidstilsynet, OpenSanctions og Proff er testet mot fixtures, ikke mot live-tjenestene. Proff-feltnavn må bekreftes mot deres dokumentasjon når avtalen er på plass.
- Matching av personer på tvers av virksomheter bruker navn og fødselsdato (hashet med salt).
