import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Meter, PageHead, Skeleton } from "../components/ui";
import { STATIC, useApi, type SourceStatus } from "../lib/api";
import { dateTime } from "../lib/format";

type Model = {
  train: { n: number; positives: number; seed: number; synthetic: boolean; kofa?: number };
  metrics: { name: string; auc: number; cv_auc: number; cv_sd: number; recall: number; roc: [number, number][] }[];
  importance: { feature: string; value: number }[]; features: string[]; excluded: string[];
  weights: { mode?: string; ml: number; cri: number; anomaly: number; brreg_per_weight: number; brreg_cap: number } | null;
  flags: { key: string; label: string; weight: number }[];
  extra_flags: { key: string; label: string; weight: number; source: string }[];
  sources: Record<string, SourceStatus>;
};
const STATUS: Record<string, [string, string]> = {
  ok: ["Hentet", "text-lo"], demo: ["Demodata", "text-mid"], av: ["Ikke satt opp", "text-muted"], feil: ["Feil", "text-hi"],
};
const COLORS = ["var(--accent)", "var(--mid)", "var(--hi)"];
const f3 = (v: number) => v.toLocaleString("nb-NO", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

export default function Method() {
  const { data: m } = useApi<Model>("/api/model");
  if (!m) return <Skeleton className="h-96" />;
  const roc = m.metrics[0].roc.map((_, i) => Object.fromEntries([["fpr", m.metrics[0].roc[i][0]], ...m.metrics.map((x) => [x.name, x.roc[i][1]])]));
  const maxImp = Math.max(...m.importance.map((i) => i.value));
  const w = m.weights;
  const mlCounts = w?.mode === "ml";
  return (
    <div>
      <PageHead title="Modell og metode"
        desc={mlCounts
          ? "Tre klassifikatorer, røde flagg fra Fazekas sin korrupsjonsrisikoindeks (CRI), avviksdeteksjon og Brreg-flagg, kombinert til én prioritet. Bygget på eksamensrapporten «Detecting Corruption in Public Procurement Using Machine Learning» (Foros, 2026)."
          : "Prioriteten bygger på regelbaserte røde flagg: Fazekas sin korrupsjonsrisikoindeks (CRI) fra kunngjøringen, og flagg fra Brreg og tilleggskildene. ML-modellene fra eksamensrapporten «Detecting Corruption in Public Procurement Using Machine Learning» (Foros, 2026) er under utvikling."} />
      {!mlCounts && (
        <div className="mb-4 rounded-md border border-accent/40 bg-accent-soft p-4 text-sm">
          <strong>ML er under utvikling.</strong> Modellene treffer ennå ikke godt nok på ekte KOFA-saker, så de teller ikke i prioriteten.
          ML-scoren vises i saksmappen som tilleggsinformasjon. Modellene skal trenes på avgjørelser fra KOFA og på revisorenes egne vurderinger («relevant» / «ikke relevant» i saksmappen).
        </div>
      )}
      <div className="mb-4 rounded-md border border-mid/40 bg-mid-soft p-4 text-sm text-mid">
        <strong>Viktig:</strong> Modellene er trent på {(m.train.n - (m.train.kofa ?? 0)).toLocaleString("nb-NO")} syntetiske anbud ({m.train.positives} merket mistenkelige), som i rapporten.
        Treffsikkerheten under gjelder syntetiske data og sier ikke hvor godt modellen treffer på ekte kontrakter. {m.train.kofa ? ` I tillegg brukes ${m.train.kofa} kontrakter koblet til KOFA-avgjørelser${m.sources?.KOFA?.status === "demo" ? " (demodata)" : ""}.` : " Neste steg er å trene på ekte fasit fra KOFA-avgjørelser."}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <Card title={mlCounts ? "Modellytelse på holdout-testsett" : "ML-modellene (under utvikling)"} desc={`Seed ${m.train.seed}, 25 % holdout, 5-fold stratifisert CV`}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[440px] text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-normal">Modell</th><th className="pb-2 text-right font-normal">AUC</th><th className="pb-2 text-right font-normal">CV-AUC</th><th className="pb-2 text-right font-normal">Recall</th></tr></thead>
              <tbody>{m.metrics.map((x) => (
                <tr key={x.name} className="border-t border-line"><td className="py-1.5">{x.name}</td><td className="num text-right">{f3(x.auc)}</td>
                  <td className="num text-right">{f3(x.cv_auc)} ± {f3(x.cv_sd)}</td><td className="num text-right">{f3(x.recall)}</td></tr>))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 h-56">
            <ResponsiveContainer>
              <LineChart data={roc} margin={{ left: -18, right: 8, top: 4 }}>
                <CartesianGrid stroke="var(--line)" />
                <XAxis dataKey="fpr" type="number" domain={[0, 1]} tick={{ fontSize: 11, fill: "var(--muted)" }} tickFormatter={(v) => v.toFixed(1)} />
                <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: "var(--muted)" }} />
                <Tooltip formatter={(v: number) => v.toFixed(2)} labelFormatter={(v) => `Falsk positiv-rate ${Number(v).toFixed(2)}`} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {m.metrics.map((x, i) => <Line key={x.name} dataKey={x.name} stroke={COLORS[i]} dot={false} strokeWidth={2} />)}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Viktigste felt (Random Forest)">
          <ul className="space-y-2">{m.importance.map((i) => (
            <li key={i.feature}><div className="flex justify-between text-sm"><span>{i.feature}</span><span className="num text-xs text-muted">{f3(i.value)}</span></div>
              <Meter value={i.value} max={maxImp} color="var(--accent)" className="mt-1" /></li>))}
          </ul>
          <p className="mt-3 text-xs text-muted">Ikke med i modellen fordi TED ikke har feltet: {m.excluded.join(", ")}.</p>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Slik beregnes prioriteten">
          {mlCounts ? (<>
            <ol className="list-decimal space-y-1.5 pl-5 text-sm">
              <li><b>ML-score</b> (0–100): snittet av tre modeller (logistisk regresjon, Random Forest, Gradient Boosting).</li>
              <li><b>CRI</b> (0–100): sum av vektene for utløste røde flagg fra kunngjøringen, delt på maks mulig sum.</li>
              <li><b>Avvik</b> (0–100): Isolation Forest på de samme feltene. Fanger uvanlige kombinasjoner som reglene ikke dekker.</li>
              <li><b>Register-tillegg</b>: {w?.brreg_per_weight ?? 8} poeng per vektpoeng i flaggene fra Brreg og tilleggskildene, maks {w?.brreg_cap ?? 20}.</li>
            </ol>
            <p className="mt-3 rounded bg-sunk p-2 font-mono text-xs">prioritet = ({w?.ml} × ML + {w?.cri} × CRI + {w?.anomaly} × avvik + register) / 1,2</p>
          </>) : (<>
            <ol className="list-decimal space-y-1.5 pl-5 text-sm">
              <li><b>CRI</b> (0–100): sum av vektene for utløste røde flagg fra kunngjøringen, delt på maks mulig sum.</li>
              <li><b>Register-tillegg</b>: {w?.brreg_per_weight ?? 8} poeng per vektpoeng i flaggene fra Brreg og tilleggskildene, maks {w?.brreg_cap ?? 20}.</li>
              <li>Ved lik poengsum kommer større kontrakter først.</li>
            </ol>
            <p className="mt-3 rounded bg-sunk p-2 font-mono text-xs">prioritet = (CRI + register) / 1,2</p>
            <p className="mt-2 text-sm text-muted">ML-score og avvik (Isolation Forest) beregnes fortsatt og vises i saksmappen, men teller ikke før modellene er validert mot KOFA.</p>
          </>)}
          <p className="mt-2 text-sm text-muted">Høy prioritet = topp 5 % nasjonalt, forhøyet = persentil 80–95. Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.</p>
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
          <p className="mt-2 text-xs text-muted">«Ingen reell konkurranse» gjelder både kontrakter med ett tilbud og direkte tildelinger uten oppgitt tilbudstall. Overskridelse på rammeavtaler får halv vekt, fordi de ofte oppgir maksverdi.</p>
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
