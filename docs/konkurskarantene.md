# Konkurs og konkurskarantene

Vurdert 12.11.2026. Ikke juridisk rådgivning; bør gjennomgås av advokat før kundebruk.

## Konkurskarantene: ikke hentet, bare manuell sjekk

- Konkurskarantene registreres i Konkursregisteret ([konkursloven § 144 annet ledd](https://lovdata.no/lov/1984-06-08-58/§144)).
- Det finnes ingen API eller bulkfil. Datasettet er merket «Non-public access» på data.norge.no og er ikke under NLOD.
- [Konkursregisterforskriften § 10](https://lovdata.no/forskrift/1993-08-23-824/§10): enhver kan spørre om en navngitt person (etternavn og ett fornavn) og får navn, fødselsår og bostedskommune. Mer enn det får bare domstoler, politi/påtalemyndighet, bostyrere, Finanstilsynet, skatte- og avgiftsmyndighetene og forskere. Kommunerevisjoner er ikke på listen.
- [§ 12](https://lovdata.no/forskrift/1993-08-23-824/§12): utløpt karantene, tilbakelevert bo og tvangsakkord vises bare for de samme myndighetene.
- Å hente «alle» med automatiske oppslag ville i praksis lage et skyggeregister som ordningen er laget for å hindre, og krever automatisering av en personlig ID-porten-innlogging. Det gjør vi ikke.

Radaren har derfor bare en lenke til [konkurskarantene.brreg.no/oppslag](https://konkurskarantene.brreg.no/oppslag) i saksmappen og på leverandørsiden. Revisoren slår opp selv, og radaren lagrer ikke svaret.

## Flagget «Rolleinnehaver har rolle i annet selskap under konkursbehandling»

- Kilde: Enhetsregisteret (`/enheter?konkurs=true`) og rolle-API-et, åpne data under NLOD. Kode: `backend/radar/ingest/brreg_konkurs.py`.
- Kobling: `person_key` = hash av navn og full fødselsdato med salt, samme nøkkel som leverandørenes roller. Personer uten fødselsdato tas ikke med.
- Bare ledende roller (daglig leder, styre, innehaver, deltaker, komplementar, norsk representant). Fratrådte og avregistrerte roller tas ikke med.
- Dataminimering: rollene i konkursselskapene lagres bare som `person_key`, aldri som navn. Navnet vises bare via leverandørens egne roller.
- Lagring: konkurser eldre enn fem år slettes (samme frist som [§ 11](https://lovdata.no/forskrift/1993-08-23-824/§11) for offentlig tilgang).
- Dekning: Enhetsregisteret viser bare konkurser som ikke er slettet ennå. Radaren beholder dem fra de først ble sett, så dekningen øker over tid.
- Vekt 1,0. Flagget sier eksplisitt at det ikke sier noe om konkurskarantene.
- Slås av med `BRREG_KONKURS=0`. Første kjøring gjør ca. 3 000 rolleoppslag, senere bare for nye konkurser.

## Neste steg (ikke gjort)

Spørre Brreg om tilgang for revisjoner, og om konkurskarantene inngår i XML-abonnementet på kunngjøringer. Venter til pilot.
