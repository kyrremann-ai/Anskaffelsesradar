# CLAUDE.md

Anskaffelsesradar prioriterer offentlige anskaffelser for kommunerevisjon. Backend: FastAPI + SQLite (`backend/radar/`). Klient: React + Vite + Tailwind (`frontend/`). Demo: statisk bygg på Cloudflare Pages + D1 (`deploy/`). Arkitektur, kilder og API står i `README.md`; designsystemet i `DESIGN.md`. Kode, UI-tekst og commit-meldinger skrives på norsk.

## Kommandoer

```bash
cd backend && pip install -r requirements-dev.txt && pytest -q   # ca. 100 tester, ~30 s
cd backend && uvicorn radar.api:app --reload                     # demodata lastes første gang
cd frontend && npm ci && npm run build                           # tsc -b + vite build (det CI kjører)
node --test deploy/cloudflare/test/*.test.js                     # Pages Functions (D1-sakslogg)
RADAR_DEMO=1 deploy/build_pages.sh ut-pages/                     # bygg Cloudflare-versjonen lokalt
```

CI (`.github/workflows/ci.yml`) kjører de tre testkommandoene over. Kjør dem før PR.

## Regler for flagg, prioritet og tekst

Brukerne er revisorer som vil utfordre sterke formuleringer. Gjelder alle nye flagg, UI-tekster og tall:

- Flagg sier hva dataene viser, ikke en tolkning: «Ett registrert tilbud», ikke «Ingen reell konkurranse». Personkoblinger prefikses «Navnetreff:» og er alltid «ubekreftet mulig kobling».
- Prioritet er en relativ rangering (Høy = topp 5 %, Forhøyet = persentil 80–95), aldri en sannsynlighet. Ikke skriv «risiko %», «sannsynlig», «risikoprofil» eller «varselsignal» (bruk «flagg»).
- Manglende registertreff er datakvalitet (`brreg_missing`, vekt 0, grå), ikke en egenskap ved leverandøren.
- Klagerisiko (KOFA-modellen) vises for seg og teller aldri i prioriteten.
- Hver sak skal vise: hvilke flagg som slo ut, kildedata per flagg, bidrag, manglende data og sammenligningsgruppe.
- Nye leverandørflagg sjekkes mot alternative forklaringer i `radar/supplier_context.py` (se `docs/testplan-leverandorflagg.md`).
- Prioritetstall fargelegges med `tierColor(tier)` i `frontend/src/components/ui.tsx`, aldri med faste terskler på poengsummen.

## Personvern og datakilder (ikke forhandlingsbart)

- Aksjonærregisteret og andre personopplysninger skal **aldri** inn i dette (offentlige) repoet. De ligger i det private datarepoet (`RADAR_DATA_REPO`) og leses av byggejobben. `data/` er gitignored; fixtures i `backend/tests/fixtures/` er fiktive.
- Send aldri personnavn til LLM. `llm.build_input` bestemmer hvilke felt som sendes; utvid den ikke med person- eller eierdata.
- Lagre eller vis aldri politisk parti (GDPR art. 9). Kandidatlister beholder bare navn, fødselsår, kommune, valgt/kandidat og valg.
- Ikke skrap proff.no (vilkår + databasevern). Eierdata kommer fra aksjonærregisteret eller Proffs betalte API.
- Konkurskarantene har ingen bulk-tilgang: bare lenke til Brregs oppslag, ikke innhøsting (`docs/konkurskarantene.md`).
- Arbeidstilsynets API gir 403 fra sky-IP-er med vilje. Ikke rut rundt det med proxy/Workers; bemanningsdata importeres som fil (`data/import/bemanning*.json`).
- OpenSanctions er CC BY-NC og skal være av som standard.

## Fallgruver

- `PERSON_SALT` må aldri endres uten `python -m radar.reindex_persons --ja` og full ny innhenting. Pipelinen stopper med vilje hvis fingeravtrykket ikke stemmer; ikke fjern den sjekken.
- `export_static` deler data i `contracts-N.json`/`cards-N.json` à 2 500 rader. Klienten (`frontend/src/lib/static.ts`) leser fillisten fra `meta.json`.
- `python -m radar.ai_vurderinger <export> <store>` beholder bare oppføringer for kontrakter den finner i `contracts-*.json`. En tom eller feil eksportmappe tømmer lageret. Sjekk antall oppføringer før lageret pushes til grenen `ai-vurderinger`.
- Grenen `ai-vurderinger` er et datalager som skrives av nattjobben og leses av «Radar på Cloudflare». Ikke merge den inn i main eller slett den.
- API-serveren deler én SQLite-tilkobling mellom tråder; bare `frame()` er låst. Ny kode som skriver fra flere tråder trenger egen tilkobling eller lås.
- Doffin tåler 10 nedlastinger i minuttet (`DOFFIN_XML_PER_RUN`). TED-XML ligger bak en WAF, så TED-rader bygges fra søke-API-ets felt.
- Rammeavtaler oppgir ofte maksverdi: overskridelsesflagget har halv vekt der.
