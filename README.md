# Anskaffelsesradar

Prioriterer offentlige anskaffelser for kommunerevisjon. Data hentes fra TED, Doffin og Brønnøysundregistrene, og hver kontrakt får regelbaserte røde flagg og registerflagg samlet i én prioritet, pluss en eksperimentell klagerisiko trent på ekte KOFA-saker. Revisoren jobber i en arbeidsflate med saker, logg og arbeidspapir.

> Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.
> ML-modellene fra eksamensrapporten (Foros 2026, syntetiske data) er fjernet fra appen: de traff ikke bedre enn tilfeldig på ekte KOFA-saker.

## Hva er nytt i v2

- **Mine enheter**: velg kommunene og foretakene du reviderer. Hele appen filtreres på dem, og nøkkeltall sammenlignes med hele Norge.
- **Arbeidsflate**: prioritert kø og saksmappe side om side. Erstatter Risikoregister og Saksliste.
- **Én prioritetsscore (0–100)** fra røde flagg (CRI) og registerflagg. Klagerisiko vises ved siden av og teller ikke.
- **Brønnøysund**: Enhetsregisteret og roller per leverandør og oppdragsgiver. Gir 11 nye flagg, blant annet stiftet etter kunngjøring, konkurs, ikke MVA-registrert, felles styremedlem med oppdragsgiver og felles roller eller adresse mellom konkurrerende leverandører. Ny side **Leverandører** med koblinger via personer.
- **Konkurs hos rolleinnehavere**: flagg når daglig leder/styre hos leverandøren har rolle i et annet selskap under konkursbehandling (koblet på navn og fødselsdato). Konkurskarantene hentes ikke (ikke åpne data); saksmappen lenker til Brregs oppslag. Se `docs/konkurskarantene.md`.
- **Sporbarhet**: alle endringer på en sak logges med hvem og når. AI-vurderinger lagres med modell, tidspunkt og feltene som ble sendt. Hver sak har et arbeidspapir for utskrift eller PDF.
- **AI via Mistral (EU)** bak en adapter. Uten nøkkel brukes en regelbasert mal. Personnavn sendes aldri til LLM.
- **Ingen tredjepart i nettleseren**: ingen Google Fonts, ingen sporingsskript.
- **Kjører på egen server**: Docker Compose med Caddy (HTTPS) og innlogging med Microsoft Entra ID på Hetzner.
- **Avskriv flagg**: revisor kan avskrive et flagg som falsk positiv med årsakskode (konsern, rammeavtale, navnelikhet, datafeil, forklart). Avskrevne flagg teller ikke i prioriteten, føres i saksloggen og eksporteres som datasett for å kalibrere vektene.

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

## Offentlig demo på vikingsea.no/radar/

`deploy/build_offentlig.sh ut-offentlig/` bygger en versjon uten innlogging med fiktive demodata (`radar.synthetic`) i en egen, midlertidig database, så live-dataene aldri blandes inn. Skriptet stopper hvis eksporten ikke er i demomodus. Saker lagres bare i nettleseren. Kopier `ut-offentlig/` til `radar/` i vikingsea.no-mappen og publiser den på Netlify.

## Demo på radar.vikingsea.no (Cloudflare Pages + Access)

Klienten bygd statisk (VITE_STATIC=1) med dataene som JSON-filer, på eget domene. Bare inviterte e-poster kommer inn (engangskode fra Cloudflare Access, gratis opptil 50 brukere). Saker lagres i Cloudflare D1 via Pages Functions (`deploy/cloudflare/functions`), og loggen viser Access-e-posten til den som endret.

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

Drift (én installasjon per kunde, varsling, sikkerhetskopi med gjenopprettingstest, EU-lagring og revisjonsspor): se [docs/drift.md](docs/drift.md). Databehandlere: [docs/databehandlere.md](docs/databehandlere.md).

## Produksjon (Hetzner)

```bash
cp .env.example .env    # fyll inn DOMAIN, ENTRA_*, OAUTH2_COOKIE_SECRET, PERSON_SALT, ev. MISTRAL_API_KEY og DOFFIN_API_KEY
docker compose up -d --build
```

En CX22 (2 vCPU, 4 GB) holder. Sett `REFRESH_HOURS=24` for daglig oppdatering.

**Innlogging.** Standard er Microsoft Entra ID (OIDC) via oauth2-proxy foran Caddy. De fleste kommuner og revisjonsselskaper har allerede Microsoft 365, så brukerne logger inn med jobbkontoen sin. MFA styres av kundens egen policy for betinget tilgang. E-posten sendes til appen og står i saksloggen og arbeidspapiret. Oppsett:

1. Registrer en app i kundens Entra ID (App registrations). Redirect-URI: `https://<DOMAIN>/oauth2/callback`. Lag en klienthemmelighet.
2. Fyll inn `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `OAUTH2_COOKIE_SECRET` og `ALLOWED_EMAIL_DOMAINS` i `.env`.
3. Krev MFA for appen med betinget tilgang i Entra ID.
4. `REQUIRE_USER=1` gjør at endringer uten innlogget bruker avvises (401).

Caddy fjerner `X-Remote-User` fra klientens forespørsel før identiteten settes, og appen har ingen åpen port, så identiteten kan ikke forfalskes. For en rask test uten Entra: `COMPOSE_PROFILES=` og `CADDYFILE=Caddyfile.basic` (felles passord, ingen 2FA, ikke for persondata).

**SQLite.** Databasen kjører med WAL og 30 sekunders `busy_timeout`, så nattlig `pipeline.py` og brukere i appen kan skrive samtidig uten låsefeil.

**PERSON_SALT må aldri endres** uten full re-indeksering. Personnøklene (`sha256(navn|fødselsdato|salt)`) kobler samme person på tvers av virksomheter. Med nytt salt får nye roller andre nøkler enn de lagrede, og koblingene brytes uten feilmelding. Pipelinen lagrer derfor et fingeravtrykk av saltet og stopper hvis det endres. Må saltet byttes (f.eks. ved lekkasje):

```bash
PERSON_SALT=<nytt> python -m radar.reindex_persons --ja   # sletter roller, merker alle enheter som utdatert
PERSON_SALT=<nytt> python -m radar.pipeline               # henter alle roller på nytt (20–40 min)
```

Ta vare på saltet sammen med sikkerhetskopien. En gjenopprettet database kan ikke oppdateres videre med et annet salt.

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
| POST | `/api/contracts/{id}/flags/{key}/dismiss` | avskriv flagg (`reason`, `note`, `scope`: kontrakt \| leverandor) |
| DELETE | `/api/dismissals/{id}` | angre avskrivning (raden beholdes med hvem og når) |
| GET | `/api/dismissals.csv` | alle avskrivninger med vekt, for kalibrering |
| GET | `/api/cases/{id}/arbeidspapir` | utskriftsvennlig arbeidspapir |
| GET | `/api/doffin/latest?q=` | Doffin live |
| GET | `/api/model` | modellytelse, viktigste felt, vekter |
| POST | `/api/refresh` | start innhenting og scoring |

## Tilleggskilder

Hver kilde kjøres for seg ved «Hent nye data». En kilde som feiler stopper ikke de andre, og status vises på metodesiden.

Alle rader valideres med Pydantic før de lagres (`radar/ingest/validate.py`). Kilden får status «Hentet, med avvik» og en advarsel på metodesiden og i `radar.check` når rader ikke passer skjemaet, når et kjernefelt mangler i nesten alle rader (typisk omdøpt felt hos kilden), når en hel feed gir langt færre rader enn forrige gang, eller når Proff svarer uten eiere. En tom eller omformatert feed tømmer ikke registeret som fungerte i går.

**OpenSanctions og PEP.** EU- og FN-listene er åpne uten kommersielle begrensninger, men dekker bare sanksjoner, ikke PEP. Uten OpenSanctions-lisens forsvinner derfor PEP-flagget. Det finnes ikke noe åpent norsk PEP-register. Flaggene gir et tillegg i prioriteten (se `radar/enrich.py`).

| Kilde | Hvordan | Tilgang | Flagg |
|---|---|---|---|
| Regnskapsregisteret | API per orgnr (`REGNSKAP=1`), alle år og konsernregnskap | Åpent | årlig kontraktsverdi > 50 % av omsetning i tildelingsåret, negativ egenkapital, AS uten regnskap |
| Brreg endringslogg | `oppdateringer/enheter` og `oppdateringer/roller`, bare for leverandører med felles adresse/roller eller uten ansatte | Åpent | avgjør om adresse, ansatte og roller gjaldt på tildelingstidspunktet |
| Fusjon/fisjon/omdanning | Fil i `data/import/endringer*.csv` (`fra_orgnr;til_orgnr;type;dato`) | Kunngjøringer i Brreg | to orgnr regnes som samme virksomhet |
| Aksjonærregisteret (Skatteetaten) | Uttrekk `data/import/aksjonaer_<år>.csv.gz` (flere år side om side), lages med `python -m radar.ingest.owners <fil> --extract` | Fil fra Skatteetaten. Personopplysninger: ligger i privat datarepo (`RADAR_DATA_REPO`), aldri i dette repoet | felles eier med konkurrent (direkte eller via holdingselskap, inntil tre ledd), oppdragsgiver eier leverandør, eier har rolle hos oppdragsgiver, eier er lokalpolitiker (ubekreftet), konsernforhold |
| Proff API | `PROFF_API_KEY` | Betalt avtale med Proff. Vi skraper ikke proff.no | eiere, som over |
| Renholdsregisteret | `RENHOLD_REGISTER_URL` (XML) | Åpent (NLOD) | leverandør på renhold (CPV 9091) ikke godkjent |
| Godkjente bedriftshelsetjenester | `BHT_REGISTER_URL` (XML) | Åpent (NLOD) | leverandør på bedriftshelsetjeneste (CPV 85147) ikke godkjent |
| Bemanningsregisteret | `BEMANNING_REGISTER_URL` | Åpent (NLOD) | leverandør på vikar/bemanning (CPV 7962) ikke godkjent |
| Kandidatlister (valg.no) | Fil i `data/import/kandidat*.csv` | Åpent | mulig kobling til lokalpolitiker (navn + fødselsår, samme kommune) |
| OpenSanctions | `OPENSANCTIONS_URL` + `OPENSANCTIONS_LISENS` | CC BY-NC. All bruk i kommersiell virksomhet krever lisens, også compliance-screening. Av som standard; kunden tar med egen lisens | sanksjonert selskap/person, PEP blant roller/eiere |
| KOFA | Fil i `data/import/kofa*.csv` eller `.json`, lages med `python -m radar.ingest.kofa_scrape` | Offentlige avgjørelser (åvl. § 14) | konstatert brudd. Koblede saker brukes som ekte fasit i treningen (fra 30 per klasse) |

KOFA-skraperen henter fra klagenemndssekretariatet.no (www.klagenemndssekretariatet.no må være tillatt i nettverket). Den følger robots.txt, venter 3 sek mellom kall og cacher sidene i `data/cache/kofa/`. Saker uten realitetsavgjørelse (avvist, trukket) tas ikke med. `python -m radar.ingest.kofa_link` kobler sakene til Doffin-kunngjøringer (også konkurranser uten tildeling), og `python -m radar.kofa_models` sammenligner modellvalg med kryssvalidert AUC og 95 % konfidensintervall. `python -m radar.klagerisiko hent|test` måler **klagerisiko**: hvor sannsynlig det er at en konkurranse havner i KOFA (ikke korrupsjonsrisiko). Mål modellene mot KOFA-fasiten med `python -m radar.kofa_eval`: AUC for syntetisk modell testet på KOFA, modell trent bare på KOFA (CV) og begge sammen.

Filimport kan også kjøres for hånd: `python -m radar.ingest.owners fil.csv --year 2024`, `python -m radar.ingest.valg fil.csv`, `python -m radar.ingest.kofa fil.csv`.

Personvern: kandidatlister, PEP og personlige eiere er personopplysninger. Treff vises som «mulig kobling» og må verifiseres. Gjør en DPIA før produksjon, og send aldri slike data til en LLM.

## Kjente begrensninger

- Klagerisiko er trent på KOFA-saker koblet til Doffin. Koblingen har en konfidens (oppdragsgivers orgnr fra Enhetsregisteret, navn, ordoverlapp, tid og Doffin-ID i teksten), og bare koblinger med konfidens ≥ `KOFA_MIN_CONFIDENCE` (0,7) brukes i treningen. Navnesøk alene ga en delvis kunstig kommune-effekt, fordi kommuner har entydige navn og statlige etater ikke har det. Lagrede koblinger fra før konfidens fantes: `python -m radar.ingest.kofa_link --rescore`, så `python -m radar.klagerisiko eksporter`. KOFA oppgir ikke CPV, så CPV inngår ikke i koblingen.
- Rammeavtaler oppgir ofte maksverdi. Overskridelsesflagget får derfor halv vekt på rammeavtaler.
- Leverandørflaggene (omsetning, ansatte, adresse, roller, eiere) sjekkes mot alternative forklaringer før de vises: se `radar/supplier_context.py` og testplanen i `docs/testplan-leverandorflagg.md`. Brregs endringslogg har detaljer om hva som endret seg bare for nyere endringer (fra ca. 2026), og rollefeeden sier bare *at* roller endret seg. Fusjon og fisjon finnes ikke i det åpne API-et og må legges inn som fil.
- Reelle rettighetshavere er ikke åpent. Eierskap dekkes av Aksjonærregisteret (årlig fil, ett år gammel) og eventuelt Proff API.
- Adapterne for Regnskapsregisteret, Arbeidstilsynet, OpenSanctions og Proff er testet mot fixtures. Kjør Actions → «Kildesjekk» for en verifisering mot live-tjenestene (rapporten viser antall rader, skjemaavvik og eksempelfelt per kilde). Proff-feltnavn må bekreftes mot deres dokumentasjon når avtalen er på plass.
- Matching av personer på tvers av virksomheter bruker navn og fødselsdato (hashet med salt). Se «PERSON_SALT må aldri endres» over.
- Avskrivninger justerer ikke vektene automatisk. Revisorer avskriver også ubeleilige funn, så vektene bør endres etter en manuell gjennomgang av `dismissals.csv`.
