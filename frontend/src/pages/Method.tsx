import { Card, PageHead, Skeleton } from "../components/ui";
import { STATIC, useApi, type SourceStatus } from "../lib/api";
import { dateTime } from "../lib/format";

type Model = {
  weights: { brreg_per_weight: number; brreg_cap: number } | null;
  flags: { key: string; label: string; weight: number }[];
  extra_flags: { key: string; label: string; weight: number; source: string }[];
  sources: Record<string, SourceStatus>;
  klagerisiko?: { metrics: { auc: number; lo: number; hi: number; n: number; positive: number }; trained?: string; coef: Record<string, number> } | null;
};
const KR_LABELS: Record<string, string> = {
  log_est_value: "Estimert verdi", tender_days: "Tilbudsfrist", framework: "Rammeavtale", works: "Bygg og anlegg", services: "Tjenester",
  eos: "Over EØS-terskel", buyer_kommune: "Kommune", buyer_fylke: "Fylkeskommune", buyer_helse: "Helseforetak",
  "buyer_innkjøpssamarbeid": "Innkjøpssamarbeid", buyer_stat: "Statlig virksomhet",
};
const STATUS: Record<string, [string, string]> = {
  ok: ["Hentet", "text-lo"], demo: ["Demodata", "text-mid"], av: ["Ikke satt opp", "text-muted"], feil: ["Feil", "text-hi"],
};
const f3 = (v: number) => v.toLocaleString("nb-NO", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

export default function Method() {
  const { data: m } = useApi<Model>("/api/model");
  if (!m) return <Skeleton className="h-96" />;
  const w = m.weights;
  return (
    <div>
      <PageHead title="Modell og metode"
        desc="Prioriteten bygger på regelbaserte røde flagg: Fazekas sin korrupsjonsrisikoindeks (CRI) fra kunngjøringen, og flagg fra Brreg og tilleggskildene. Ved siden av vises klagerisiko, en modell trent på ekte KOFA- og Doffin-data." />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Slik beregnes prioriteten">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm">
            <li><b>CRI</b> (0–100): sum av vektene for utløste røde flagg fra kunngjøringen, delt på maks mulig sum.</li>
            <li><b>Register-tillegg</b>: {w?.brreg_per_weight ?? 8} poeng per vektpoeng i flaggene fra Brreg og tilleggskildene, maks {w?.brreg_cap ?? 20}.</li>
            <li>Ved lik poengsum kommer større kontrakter først.</li>
          </ol>
          <p className="mt-3 rounded bg-sunk p-2 font-mono text-xs">prioritet = (CRI + register) / 1,2</p>
          <p className="mt-2 text-sm text-muted">Maskinlæringsmodellene fra eksamensrapporten (trent på syntetiske data) er fjernet fordi de ikke traff bedre enn tilfeldig på ekte KOFA-saker. Revisorenes vurderinger i saksmappen samles inn som fasit for nye modeller.</p>
          <p className="mt-2 text-sm text-muted"><b>Prioritet er en relativ rangering, ikke en sannsynlighet.</b> Høy = topp 5 % nasjonalt, forhøyet = persentil 80–95. Også et datasett med bare uproblematiske anskaffelser har en topp 5 %, så nivået sier hva som bør sjekkes først, ikke at noe er galt.</p>
          <p className="mt-2 text-sm text-muted">
            <b>AI-vurdering:</b> {STATIC
              ? "I denne demoen skriver Claude (Anthropic) vurderingen, på den innloggede brukerens Claude-konto."
              : "Skrives av språkmodellen som er satt opp på serveren (Mistral i EU eller Claude)."} Modellen får bare
            kontraktsfelt og flagg, aldri personnavn fra registrene. Vurderingen er beslutningsstøtte, ikke en konklusjon.
          </p>
        </Card>
        <Card title="Røde flagg fra kunngjøringen" desc="Vekter fra Fazekas et al. (2013), som i rapporten">
          <ul className="divide-y divide-line text-sm">{m.flags.map((f) => (
            <li key={f.key} className="flex justify-between py-1.5"><span>{f.label}</span><span className="num text-muted">{f.weight.toLocaleString("nb-NO")}</span></li>))}
          </ul>
          <p className="mt-2 text-xs text-muted">Flaggene beskriver hva kunngjøringen viser, ikke en tolkning. «Ett registrert tilbud» krever at kunngjøringen oppgir ett tilbud. Direkte tildeling har eget flagg. Overskridelse på rammeavtaler får halv vekt, fordi de ofte oppgir maksverdi.</p>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Datakilder" desc="Status fra siste oppdatering. En kilde som feiler stopper ikke de andre.">
          <ul className="divide-y divide-line text-sm">
            {Object.entries(m.sources ?? {}).map(([k, s]) => (
              <li key={k} className="flex items-center justify-between gap-3 py-1.5">
                <span>{k}{s.note && <span className="ml-1 text-xs text-muted">({s.note})</span>}</span>
                <span className="text-right text-xs">
                  <span className={STATUS[s.status]?.[1]}>{STATUS[s.status]?.[0] ?? s.status}</span>
                  {s.rows != null && <span className="num ml-2 text-muted">{s.rows.toLocaleString("nb-NO")} rader</span>}
                  {s.error && <span className="block text-hi" title={s.error}>{s.error.slice(0, 60)}</span>}
                  <span className="block text-muted">{dateTime(s.at)}</span>
                </span>
              </li>))}
            {!Object.keys(m.sources ?? {}).length && <li className="py-1.5 text-muted">Ingen oppdatering kjørt ennå.</li>}
          </ul>
        </Card>
        <Card title="Flagg fra tilleggskildene" desc="Personer matches på navn og fødselsår. Treff er mulige koblinger og må verifiseres.">
          <ul className="divide-y divide-line text-sm">{(m.extra_flags ?? []).map((f) => (
            <li key={f.key} className="flex justify-between gap-3 py-1.5"><span>{f.label}<span className="block text-xs text-muted">{f.source}</span></span>
              <span className="num text-muted">{f.weight.toLocaleString("nb-NO")}</span></li>))}
          </ul>
        </Card>
        {m.klagerisiko && (
          <Card title="Klagerisiko (eksperimentell)" desc="Rangering (persentil) av hvor mye en anskaffelse ligner dem som er klaget inn til KOFA. Ikke en sannsynlighet. Vises ved siden av flaggene og teller ikke i prioriteten.">
            <p className="text-sm">
              Logistisk regresjon trent på {m.klagerisiko.metrics.positive} Doffin-konkurranser som ble klaget inn til KOFA, mot et tilfeldig utvalg
              av {(m.klagerisiko.metrics.n - m.klagerisiko.metrics.positive).toLocaleString("nb-NO")} konkurranser fra 2019–2026. Kryssvalidert AUC{" "}
              <span className="num">{f3(m.klagerisiko.metrics.auc)}</span> (95 % KI {m.klagerisiko.metrics.lo.toFixed(2).replace(".", ",")}–{m.klagerisiko.metrics.hi.toFixed(2).replace(".", ",")}),
              med oppdragsgiverne holdt adskilt. De 10 % med høyest score inneholder omtrent 26 % av KOFA-sakene. Scoren er persentil blant kontraktene i radaren.
            </p>
            <ul className="mt-2 divide-y divide-line text-sm">{Object.entries(m.klagerisiko.coef).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6).map(([k, v]) => (
              <li key={k} className="flex justify-between py-1.5"><span>{KR_LABELS[k] ?? k}</span><span className="num text-muted">{v > 0 ? "øker" : "senker"}</span></li>))}
            </ul>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted">
              <li>Måler <b>klagerisiko, ikke korrupsjon</b>. Store konkurranser med mange tilbydere klages oftere inn, mens direkte anskaffelser uten konkurranse sjelden blir klaget inn.</li>
              <li>KOFA-sakene er koblet til kunngjøringer med navnesøk. Kommuner er lettere å koble på navn enn statlige etater, så kommune-effekten kan være delvis kunstig.</li>
              <li>Om KOFA fant brudd eller ikke lar seg ikke forutsi fra kunngjøringen (AUC rundt 0,5), så modellen skiller ikke på utfallet.</li>
              <li>Teller ikke i prioriteten før revisorenes vurderinger viser at den treffer.</li>
            </ul>
          </Card>)}
        <Card title="Leverandørflagg: alternative forklaringer" desc="Sjekkes før flagget vises. Svarene står under hvert flagg i saksmappen.">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            <li><b>Flerårig kontrakt mot ett års omsetning:</b> verdien deles på varigheten (årlig verdi) og sammenlignes med regnskapet fra tildelingsåret. Uten oppgitt varighet står det i flagget.</li>
            <li><b>Rammeavtale:</b> med flere leverandører deles verdien likt (faktisk fordeling er ukjent). Med én leverandør er verdien ofte en øvre ramme, og flagget får halv vekt.</li>
            <li><b>Konsern:</b> datterselskap (≥ 50 % eid av et selskap) eller morselskap med konsernregnskap gir halv vekt på omsetnings- og ansatteflagget. Felles roller, eiere eller adresse innenfor samme konsern teller ikke.</li>
            <li><b>Tidspunkt:</b> Brregs endringslogg avgjør om adresse, ansatte og roller var de samme ved tildeling. Adresse endret etter tildeling gir halv vekt. Uten logg står det «nåværende data» i flagget.</li>
            <li><b>Samme juridiske enhet:</b> hovedenhet og underenhet, registrert fusjon/fisjon/omdanning (fil) og slettet enhet med samme navn regnes ikke som to leverandører.</li>
          </ul>
          <p className="mt-2 text-xs text-muted">Mangler data, sier flagget hva som mangler i stedet for å gjette. Vekten endres ikke av manglende data.</p>
        </Card>
        <Card title="Personopplysninger og rettslig grunnlag" desc="Gjelder aksjonærregisteret, kandidatlister og roller i Brreg">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            <li><b>Formål:</b> å prioritere hvilke anskaffelser revisor bør se nærmere på. Ikke å vurdere enkeltpersoner.</li>
            <li><b>Grunnlag:</b> revisjonens lovpålagte oppgave (kommuneloven kap. 24), jf. personvernforordningen art. 6 nr. 1 bokstav e. Anskaffelsesradar AS behandler opplysningene som databehandler for revisjonen. Grunnlaget og en vurdering av personvernkonsekvenser (DPIA) skal være på plass før personopplysninger tas i bruk hos en kunde.</li>
            <li><b>Dataminimering:</b> bare normalisert navn, fødselsår, kommune og parti lagres fra kandidatlistene, og fødselsdato hashes fra Brreg. Personnavn sendes aldri til språkmodellen.</li>
            <li><b>Treff er ikke bevis:</b> navn og fødselsår gir en <i>ubekreftet mulig kobling</i>, aldri en bekreftet identitet. Kandidat og innvalgt vises hver for seg. Aksjonærregisteret er et årsskiftebilde, ikke dagens eierskap.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
