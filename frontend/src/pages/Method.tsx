import { Card, PageHead, Skeleton } from "../components/ui";
import { STATIC, useApi, type Meta, type SourceStatus } from "../lib/api";
import { dateTime, int, pct } from "../lib/format";

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
const w1 = (v: number) => v.toLocaleString("nb-NO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const f3 = (v: number) => v.toLocaleString("nb-NO", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

export default function Method() {
  const { data: m } = useApi<Model>("/api/model");
  const { data: meta } = useApi<Meta>("/api/meta");
  if (!m) return <Skeleton className="h-96" />;
  const w = m.weights;
  const o = meta?.observasjonsenhet;
  const dups = o ? Object.values(o.duplikater).reduce((a, b) => a + b, 0) : 0;
  return (
    <div>
      <PageHead title="Modell og metode"
        desc="Prioriteten bygger på regelbaserte røde flagg: Fazekas’ korrupsjonsrisikoindeks (CRI) fra kunngjøringen, og flagg fra Brreg og tilleggskildene. Ved siden av vises klagerisiko, en modell trent på ekte KOFA- og Doffin-data." />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Hva telles: observasjonsenhet" desc="Svaret på «hva er én rad?», før totalsummene brukes">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            <li><b>Kunngjøring:</b> én publisering i TED eller Doffin.</li>
            <li><b>Kontrakt:</b> én kunngjort tildeling. Delkontrakter i samme kunngjøring telles som én. Samme tildeling i Doffin og TED telles én gang
              (samme oppdragsgiver, minst én felles leverandør, publisert innen 14 dager; TED-kunngjøringen beholdes fordi den har tilbudstall og tildelt verdi).
              Rettelser og nye publiseringer i samme TED-forløp med samme leverandør telles én gang.</li>
            <li><b>Anskaffelsesforløp:</b> alle kunngjøringer med samme prosedyre-ID i TED. Et forløp kan ha flere kontrakter, for eksempel delkontrakter
              eller tildelinger i en dynamisk innkjøpsordning.</li>
            <li><b>Intensjonskunngjøringer</b> (planlagt direkte tildeling) er egen kunngjøringstype. De telles ikke som kontrakter og rangeres ikke,
              men vises som fast kontrollpunkt. Endrings- og avlysningskunngjøringer telles ikke.</li>
          </ul>
          {o && (
            <table className="mt-3 w-full text-sm"><tbody className="divide-y divide-line">
              <tr><td className="py-1">Kunngjøringer (TED / Doffin)</td><td className="num text-right">{int(o.kunngjøringer)} ({int(o.per_kilde.ted ?? 0)} / {int(o.per_kilde.doffin ?? 0)})</td></tr>
              {Object.entries(o.duplikater).map(([k, n]) => <tr key={k}><td className="py-1 pl-3 text-muted">− {k}</td><td className="num text-right">{int(n)}</td></tr>)}
              <tr><td className="py-1 pl-3 text-muted">− Intensjonskunngjøringer</td><td className="num text-right">{int(o.per_type.intensjon ?? 0)}</td></tr>
              <tr><td className="py-1 pl-3 text-muted">− Endringer og avlysninger som ikke er koblet</td><td className="num text-right">
                {int(o.kunngjøringer - dups - (o.per_type.intensjon ?? 0) - o.kontrakter)}</td></tr>
              <tr className="font-medium"><td className="py-1">Kontrakter</td><td className="num text-right">{int(o.kontrakter)}</td></tr>
              <tr><td className="py-1">Anskaffelsesforløp</td><td className="num text-right">{int(o.forløp)}</td></tr>
            </tbody></table>)}
          {o && o.duplikater_tvetydige > 0 && <p className="mt-2 text-xs text-muted">{int(o.duplikater_tvetydige)} Doffin-kunngjøringer passet med flere TED-forløp; den nærmeste i tid er valgt.</p>}
        </Card>
        {o && (
          <Card title="Datakvalitet" desc="Hvor mye av tallene som bygger på oppgitte verdier og registertreff">
            <table className="w-full text-sm"><tbody className="divide-y divide-line">
              <tr><td className="py-1">Kontrakter med tildelt verdi</td><td className="num text-right">{int(o.verdi.med_tildelt_verdi)} ({pct(100 * o.verdi.med_tildelt_verdi / o.kontrakter)})</td></tr>
              <tr><td className="py-1">Bare estimert verdi</td><td className="num text-right">{int(o.verdi.bare_estimat)}</td></tr>
              <tr><td className="py-1">Uten verdi</td><td className="num text-right">{int(o.verdi.uten_verdi)}</td></tr>
              <tr><td className="py-1">Rammeavtaler (verdien er ofte maksramme)</td><td className="num text-right">{int(o.verdi.rammeavtaler)}</td></tr>
              {Object.entries(o.verdi.usikker).map(([k, n]) => <tr key={k}><td className="py-1">Usikker verdi: {k}</td><td className="num text-right">{int(n)}</td></tr>)}
              <tr><td className="py-1">Tilbudsfrist kjent (fra TEDs konkurransekunngjøring)</td><td className="num text-right">{int(o.tilbudsfrist_kjent)}</td></tr>
              <tr><td className="py-1">Leverandører med gyldig orgnr</td><td className="num text-right">{int(o.registertreff.gyldig_orgnr)} av {int(o.registertreff.leverandører)} ({pct(100 * o.registertreff.gyldig_orgnr / Math.max(1, o.registertreff.leverandører))})</td></tr>
              <tr><td className="py-1">Av disse funnet i Enhetsregisteret</td><td className="num text-right">{int(o.registertreff.funnet_i_brreg)} ({pct(100 * o.registertreff.funnet_i_brreg / Math.max(1, o.registertreff.gyldig_orgnr))})</td></tr>
              <tr><td className="py-1">Kontrakter uten oppgitt leverandør</td><td className="num text-right">{int(o.registertreff.kontrakter_uten_leverandør)}</td></tr>
            </tbody></table>
            <p className="mt-2 text-xs text-muted">Utenlandske leverandører har ikke norsk orgnr. Usikre verdier brukes ikke i overskridelse og vises som datakvalitet i saksmappen.</p>
          </Card>)}
        <Card title="Faste kontrollpunkter" desc="Slår ut uansett fordeling, og vises ved siden av prioriteten (◆)">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            <li><b>Ett registrert tilbud:</b> kunngjøringen oppgir ett mottatt tilbud.</li>
            <li><b>Direkte tildeling over nasjonal terskelverdi:</b> prosedyren er direkte tildeling og verdien er minst 1,3 mill. kr (FOA § 5-1). Lovlig ved gyldig unntak, som må sjekkes i begrunnelsen.</li>
            <li><b>Intensjonskunngjøring om direkte tildeling:</b> oppdragsgiver har varslet en kontrakt uten konkurranse.</li>
            <li><b>Frist under lovens minstefrist:</b> under 30 dager fra konkurransekunngjøringen ble sendt til TED til tilbudsfristen eller fristen for forespørsel
              om deltakelse (15 dager ved oppgitt hastebehov). Gjelder åpen og begrenset anbudskonkurranse, konkurranse med forhandling og konkurransepreget dialog.
              Fristen kan lovlig kortes ned ved forhåndskunngjøring, som ikke er sjekket.</li>
          </ul>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Slik beregnes prioriteten">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm">
            <li><b>CRI</b> (0–100): sum av vektene for utløste røde flagg fra kunngjøringen, delt på maks mulig sum.</li>
            <li><b>Register-tillegg</b>: {w?.brreg_per_weight ?? 8} poeng per vektpoeng i flaggene fra Brreg og tilleggskildene, maks {w?.brreg_cap ?? 20}.</li>
            <li>Ved lik poengsum kommer større kontrakter først.</li>
            <li><b>Sammenligningsgruppe:</b> samme poengsum rangeres også innen oppdragsgivertype × sektor (for eksempel «Helseforetak × Helse»).
              Oppdragsgivertypen tas fra organisasjonsformen i Brreg. Grupper med under 30 kontrakter bruker bare oppdragsgivertypen.
              Velg «Innen sammenligningsgruppe» i arbeidsflaten. Hvert flagg viser hvor mange poeng det bidrar med.</li>
          </ol>
          <p className="mt-3 rounded bg-sunk p-2 font-mono text-xs">prioritet = (CRI + register) / 1,2</p>
          <p className="mt-2 text-sm text-muted">Maskinlæringsmodellene fra eksamensrapporten (trent på syntetiske data) er fjernet fordi de ikke traff bedre enn tilfeldig på ekte KOFA-saker. Revisorenes vurderinger i saksmappen samles inn som fasit for nye modeller.</p>
          <p className="mt-2 text-sm text-muted"><b>Prioritet er en relativ rangering, ikke en sannsynlighet.</b> Høy = topp 5 % nasjonalt, forhøyet = persentil 80–95. Også et datasett med bare uproblematiske anskaffelser har en topp 5 %, så nivået sier hva som bør sjekkes først, ikke at noe er galt.</p>
          <p className="mt-2 text-sm text-muted">
            <b>AI-vurdering:</b> {STATIC
              ? "I denne demoen skriver Claude (Anthropic) vurderingen. For saker med høy prioritet lages den hver natt og vises med dato; i artifacten kan brukeren også be om en ny på sin egen Claude-konto."
              : "Skrives av språkmodellen som er satt opp på serveren (Mistral i EU eller Claude)."} Modellen får bare
            kontraktsfelt og flagg, aldri personnavn fra registrene. Vurderingen er beslutningsstøtte, ikke en konklusjon.
          </p>
        </Card>
        <Card title="Røde flagg fra kunngjøringen" desc="Vekter fra Fazekas et al. (2013), som i rapporten">
          <ul className="divide-y divide-line text-sm">{m.flags.map((f) => (
            <li key={f.key} className="flex justify-between py-1.5"><span>{f.label}</span><span className="num text-muted">{w1(f.weight)}</span></li>))}
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
              <span className="num text-muted">{w1(f.weight)}</span></li>))}
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
            <li><b>Dataminimering:</b> bare normalisert navn, fødselsår, kommune, valg og om personen ble valgt lagres fra kandidatlistene. Parti lagres ikke (særlig kategori, GDPR art. 9), og fødselsdato hashes fra Brreg. Personnavn sendes aldri til språkmodellen.</li>
            <li><b>Treff er ikke bevis:</b> navn og fødselsår gir en <i>ubekreftet mulig kobling</i>, aldri en bekreftet identitet. Kandidat og innvalgt vises hver for seg. Aksjonærregisteret er et årsskiftebilde, ikke dagens eierskap.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
