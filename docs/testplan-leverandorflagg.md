# Testplan: alternative forklaringer for leverandørflaggene

Fire leverandørflagg er undersøkelsespunkter, ikke funn: **kontraktsverdi over halve årsomsetningen**, **ingen ansatte registrert**, **samme adresse som annen leverandør** og **felles rolleinnehaver/eier med annen leverandør**. Før de vises sjekkes de mot alternative forklaringer (`backend/radar/supplier_context.py`). Svarene legges på flagget som `checks` og vises under flagget i saksmappen.

Testene ligger i `backend/tests/test_supplier_context.py` (T1–T24) og kjøres med `pytest`.

## Utfall

| Status på sjekken | Betyr | Effekt på flagget |
|---|---|---|
| ok | Forklaringen gjelder ikke | Står |
| justert | Tallet er korrigert (årlig verdi, leverandørens andel) | Står bare hvis det fortsatt slår til |
| forklarer | Forklaringen gjelder | Koblingen/flagget faller bort |
| svekker | Forklaringen kan gjelde | Halv vekt |
| ukjent | Data mangler | Står, `data_note` sier hva som mangler. Vekten endres ikke |

## Tilfeller

| # | Spørsmål | Tilfelle | Data | Forventet |
|---|---|---|---|---|
| T1 | Flerårig verdi mot ett års omsetning? | 4 MNOK over 48 mnd, omsetning 3 MNOK | `contract_months`, `regnskap_aar` | Borte (33 % per år) |
| T2 | Samme | 8 MNOK over 24 mnd, omsetning 3 MNOK | Samme | Flagg: «Årlig verdi 4,0 MNOK er 133 %» |
| T3 | Samme | Varighet ikke oppgitt | Samme | Flagg, full vekt, «Varighet er ikke oppgitt» |
| T4 | Hele rammeavtalen eller én andel? | Ramme 9 MNOK, tre leverandører | `framework`, `winner_ids` | Delt likt: 60 % av 5 MNOK gir flagg, 43 % av 7 MNOK gir ingen |
| T5 | Samme | Rammeavtale, én leverandør | `framework` | Halv vekt (verdien er ofte en øvre ramme) |
| T6 | Riktig regnskapsår? | Tildelt 2023, regnskap 2023 og 2025 / bare 2021 | `regnskap_aar` | Bruker 2023. Bare 2021: merket «Nærmeste regnskap er fra 2021» |
| T7 | Riktig enhet? | Leverandøren er en underenhet | `overordnetEnhet` | Hovedenhetens regnskap brukes |
| T8 | Konsernselskap? | Uten aksjonærdata / 100 % eid av holdingselskap | `owners` | Merket «aksjonærregisteret er ikke lastet» / halv vekt |
| T8b | Samme | Morselskap med konsernregnskap | `regnskap_aar` type KONSERN | Halv vekt, konsernets omsetning og andel vises |
| T9 | Ingen ansatte: konsern eller ikke rapportert? | 0 ansatte i datterselskap / ingen opplysning / har registrerte ansatte, men Brreg viser ikke antallet | `antallAnsatte`, `harRegistrertAntallAnsatte`, `owners` | Halv vekt / merket, ikke tolket som 0 / borte |
| T10 | Ansatte på tildelingstidspunktet? | 0 i dag, 12 ved tildeling / endret etter tildeling uten tidligere tall | `brreg_changes` | Borte / merket «tallet ved tildeling er ukjent» |
| T11 | Samme | Endringslogg dekker ikke tildelingsdato | `brreg_changes_fetched` | Merket «nåværende data» |
| T12 | Felles adresse ved tildeling eller bare i dag? | Den andre flyttet inn etter tildeling | `brreg_changes` felt adresse | Halv vekt, med dato |
| T13 | Samme | Ingen adresseendring siden tildeling | Samme | Full vekt, ingen merknad |
| T14 | Samme | Ingen endringslogg | — | Merket «nåværende data» |
| T15 | Felles rolle ved tildeling? | Rolleendring etter tildeling (feeden sier ikke hvilken) | `brreg_changes` felt roller | Merket, full vekt |
| T16 | Konsern? | Søsterselskaper med samme styreleder | `owners` | Borte |
| T17 | Samme juridiske enhet? | Hovedenhet og underenhet på samme adresse | `overordnetEnhet` | Borte |
| T18 | Samme | Fusjon A→C og fisjon C→B fra fil | `brreg_successors` | Borte, også i kjede |
| T19 | Samme | Slettet enhet med samme navn | `slettedato`, navn | Borte, forklaringen sier «ikke bekreftet» |
| T20 | Delvis forklart | To koblinger, én underenhet | — | Flagg med bare den uforklarte |
| T21 | Datakilde | Klassifisering av Brregs endringer | JSON Patch | adresse, ansatte, ukjent (uten detaljer), slettet, annet |
| T22 | Datakilde | Import av fusjon/fisjon-fil | CSV | Gyldige orgnr importeres, ugyldige hoppes over |
| T23 | Datakilde | Regnskap med SELSKAP og KONSERN | Regnskapsregisteret | Selskapsregnskap foretrekkes, alle år lagres |
| T24 | Ende til ende | `score_all` | Alle | Flaggene lagres med `checks` |

## Kjente hull

- Brregs endringslogg har detaljer (JSON Patch) bare for nyere endringer. Eldre endringer gir status «ukjent», ikke en antakelse.
- Rollefeeden sier bare at roller endret seg, ikke hvilken rolle. Felles roller med endring etter tildeling merkes derfor, men vekten beholdes.
- Fusjon, fisjon og omdanning finnes ikke i det åpne API-et. De må legges inn i `data/import/endringer*.csv` (f.eks. fra Brregs kunngjøringer). Uten fil brukes bare underenhet-forholdet og «slettet enhet med samme navn».
- Konsern som ikke leverer konsernregnskap, oppdages bare via aksjonærregisteret (≥ 50 % eid av et selskap). Til filen er lastet er konsernspørsmålet «ukjent».
- Lik fordeling av rammeavtaler mellom leverandører er en antakelse. Faktisk fordeling krever avropsdata fra oppdragsgiver.
