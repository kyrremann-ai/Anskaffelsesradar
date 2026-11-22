# Anskaffelsesradar: designsystem

Arbeidsverktøy for kommunerevisorer, brukt ved skrivebordet i dagslys på bærbar PC og ekstern skjerm. Lys modus er standard, og mørk modus følger systemet.

## Idé
**Prioriteten forklarer seg selv.** Hver poengsum tegnes som summen av flaggene (*poengstripen*). Revisoren ser hvor prioriteten kommer fra før hun leser teksten. Appen bruker ikke et rutenett av nøkkeltallskort, og den har ikke mørk sidemeny.

## Farger (`frontend/src/index.css`)
- **Skall**: granskog `--shell #1f3b33` i toppfeltet med navigasjon. Grunnen er nøytral og grønnstikk `--bg #f2f3ef`, og flatene er hvite.
- **Handling og valg**: `--accent #1f5c4a` (knapper, valgt rad med `--accent-soft`). Brukes ikke til pynt.
- **Prioritet**: Høy `--hi` (vermilion), Forhøyet `--mid` (oker), Lav nøytral. Grønt (`--lo`) betyr bare «OK» i kildestatus og sjekker.
- **Flaggkilder** (poengstripe, flaggliste, frekvensdiagram): kunngjøring `--src-kunngjoring` (blå), Brreg `--src-brreg` (umbra), andre registre `--src-andre` (plomme). Avskrevne flagg vises skravert (`.strip-dismissed`).

## Typografi
Systemfonten alene. Det er et bevisst valg: ingen eksterne fonter i offentlig sektor. Fast rem-skala: sidetittel 22 px, saksmappetittel 20 px, korttittel 15 px, tekst 14 px og metadata 12 px. Alle tall står med `tabular-nums`. Overskrifter skrives i vanlig setningsform, uten merkelapper i store bokstaver over dem.

## Komponenter (`components/ui.tsx`)
- `ScoreStrip`: signaturen. Lengden viser prioriteten (0–100), og hvert segment er ett flaggs `bidrag` i kildens farge. Finnes i tre størrelser: `sm` i køen og på oversikten, `lg` i saksmappen.
- `KpiRow`/`Kpi`: én rad med skillelinjer, der tallet står først og etiketten under.
- `Icon`: egne SVG-ikoner med strektykkelse 1,75. Unicode-tegn brukes ikke som ikoner.
- Paneler (`Card`) har 1 px kant og 8 px radius. Innholdet i saksmappen er flatt, med tonede felt (`bg-sunk`, `bg-hi-soft`, `bg-accent-soft`) i stedet for kort inni kort.
- Overganger varer 150–200 ms og viser bare tilstand. `prefers-reduced-motion` skrur dem av.

## Nettleserflater
Markering, markør, avkrysningsbokser, rullefelt, fokusring og avstand under lenker bruker paletten.
