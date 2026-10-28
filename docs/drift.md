# Drift av Cloudflare-versjonen

Gjelder radar.vikingsea.no og kundeinstallasjoner på Cloudflare Pages. Se også [databehandlere.md](databehandlere.md).

## Én installasjon per kunde

Hver kunde har eget Pages-prosjekt, egen D1-database og egne Access-apper, listet i `deploy/kunder.json`.
Alle som er logget inn i en installasjon ser alle saker i den, så to kunder skal aldri dele installasjon.

Ny kunde:
1. Legg til en linje i `deploy/kunder.json`, f.eks.
   `"kommune-x": {"project": "anskaffelsesradar-kommune-x", "domain": "kommune-x.radar.vikingsea.no", "d1": "radar-saker-kommune-x-eu"}`.
   Domenet må ligge i en sone Cloudflare-tokenet har tilgang til.
2. Actions → «Radar-tilgang» → `setup`, kunde `kommune-x`.
3. Actions → «Radar-tilgang» → `grant` med kunde og e-post for hver bruker.
4. Nattjobben publiserer til alle kunder i fila. Kjør «Radar på Cloudflare» for hånd for å publisere med en gang.

## Varsling

| Hva | Hvor | Blir rød når |
|---|---|---|
| Kilder og alder | «Radar på Cloudflare», steg «Sjekk kilder og alder på data» (`python -m radar.check`) | en kilde har status «feil», data er eldre enn 36 timer, eller nyeste kunngjøring er eldre enn 10 dager |
| Backup | samme jobb, steg «Publiser, sikkerhetskopi og gjenopprettingstest» | eksport eller gjenoppretting feiler, eller `BACKUP_PASSPHRASE` mangler |
| Nattjobben kjørte ikke | «Radar-vakt» kl. 08 | ingen vellykket «Radar på Cloudflare» de siste 30 timene |

GitHub sender e-post når en planlagt jobb feiler, til den som sist endret tidsplanen i workflowen.
Sjekk at varsler for Actions er på under GitHub → Settings → Notifications.
Kjøreloggen (siste 60 kjøringer, med kilder som feilet) ligger i databasen under `meta.runs`.

## Sikkerhetskopi

Hver natt, for hver kunde:
1. `cf_radar.py backup` eksporterer sakene og historikken fra D1 med `wrangler d1 export` og teller radene.
2. `cf_radar.py restore-test` gjenoppretter eksporten i en midlertidig D1-database, sammenligner antall rader i `cases` og `case_history`, og sletter testdatabasen. Avvik gjør jobben rød.
3. Eksporten krypteres med `BACKUP_PASSPHRASE` (AES256, gpg) og lagres som artifact i GitHub i 90 dager, altså utenfor Cloudflare.

D1 har i tillegg innebygd gjenoppretting til et tidspunkt (Time Travel) innen Cloudflare.

### Gjenopprette fra GitHub-kopien

```bash
# Last ned «saker-backup-<run>» fra jobben i Actions og pakk ut
gpg -d demo-saker.sql.gpg > demo-saker.sql            # passord: BACKUP_PASSPHRASE
npx wrangler@3 d1 create radar-saker-gjenopprettet --jurisdiction eu
npx wrangler@3 d1 execute radar-saker-gjenopprettet --remote --file demo-saker.sql
# Kontroller, og koble så Pages-prosjektet til den nye databasen:
RADAR_D1=radar-saker-gjenopprettet python deploy/cf_radar.py setup
```

Skriv ned dato og tidsbruk hver gang dette øves. Øv minst én gang før pilot og én gang i kvartalet.

## EU-lagring

Nye databaser lages med jurisdiksjon `eu`. Den første databasen (`radar-saker`) ble laget uten.
Flytt den én gang med Actions → «Radar-tilgang» → `migrer-eu` (kunde `demo`). Den kopierer sakene til
`radar-saker-eu`, sjekker at antallet stemmer, kobler Pages til den nye og lar den gamle stå.
Slett `radar-saker` i Cloudflare når neste nattkjøring har gått grønt. Til flyttingen er gjort, skriver backup-steget en advarsel.

## Revisjonsspor

- Aktør er e-posten Cloudflare Access har logget inn (`Cf-Access-Authenticated-User-Email`), satt på serveren.
- Logglinjene lages på serveren ut fra hvilke felt som faktisk endret seg (`functions/api/cases/_db.js`). Linjer klienten sender, brukes ikke.
- Hver lagret versjon av en sak legges i `case_history` (hvem, når, hele saken).
- Saker kan ikke slettes. Avslutt dem med en status.
- Når revisor setter en vurdering, lagres prioritet, flagg, programversjon og datatidspunkt sammen med den, og vises i arbeidspapiret.
