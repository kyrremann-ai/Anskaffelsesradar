# Databehandlere og underleverandører

Oversikt for DPIA og databehandleravtale med kunden. Vikingsea AS er databehandler for kunden (kommunerevisjonen).
Oppdateres når en leverandør eller et lagringssted endres. Sist gjennomgått: 2026-10-28.

## Cloudflare-versjonen (pilot)

| Leverandør | Rolle | Data | Lagringssted | Overføringsgrunnlag | Avtale |
|---|---|---|---|---|---|
| Cloudflare, Inc. | Hosting (Pages), database (D1), innlogging (Access, engangskode på e-post) | Saker, notater, vurderinger, logg; e-postadresser og innloggingslogg; offentlige kontraktsdata og Brreg-roller (navn, fødselsår) i JSON-filene | D1 med jurisdiksjon EU (etter `migrer-eu`). Pages-filer og Access-logg i Cloudflares globale nett | EU–US Data Privacy Framework, SCC i Cloudflares DPA | Cloudflare DPA (del av Self-Serve Subscription Agreement) |
| GitHub, Inc. (Microsoft) | Kjører nattjobben (Actions), lagrer kryptert sikkerhetskopi (90 dager) og hurtigbuffer med innhentede data | Kryptert kopi av saker; i hurtigbufferen: offentlige kontraktsdata, Brreg-roller, kandidatlister | USA/globalt | DPF, SCC i GitHub DPA | GitHub Data Protection Agreement |

AI-vurdering er av i Cloudflare-versjonen. Ingen data sendes til en språkmodell.

## Artifact-versjonen (bare demo, ingen kundedata)

| Leverandør | Rolle | Data | Lagringssted | Overføringsgrunnlag |
|---|---|---|---|---|
| Anthropic PBC | Hosting av siden og artifactens database, Claude-vurdering via brukerens egen konto | Saker og notater i demo, kontraktsfelt uten personnavn til Claude | USA | DPF/SCC i Anthropics vilkår |

## Serverversjonen (Hetzner, ikke i drift)

| Leverandør | Rolle | Data | Lagringssted |
|---|---|---|---|
| Hetzner Online GmbH | Server og lagring | Alt | Tyskland/Finland |
| Mistral AI SAS | AI-vurdering (standard for kunder) | Kontraktsfelt og flagg, aldri personnavn (`llm.py:build_input`) | EU |
| Anthropic PBC | AI-vurdering hvis `LLM_PROVIDER=claude` (bare demo) | Som over | USA |
| Let's Encrypt (ISRG) | TLS-sertifikat | Domenenavn | USA |

## Ikke databehandlere

Datakildene (TED, Doffin/DFØ, Brønnøysundregistrene, eInnsyn, valg.no, Arbeidstilsynet, KOFA) er kilder vi henter offentlige data fra. Vi sender ikke kundedata til dem.

## Gjenstår før pilot

- Lenke til signert DPA for hver leverandør (lagres utenfor repoet).
- DPIA som bruker denne oversikten.
