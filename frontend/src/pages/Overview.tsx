import { Link } from "react-router-dom";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Empty, Icon, Kpi, KpiRow, Meter, PageHead, ScoreStrip, Skeleton, SourceLegend, SOURCE_COLOR, TierBadge, sourceKey, tierColor } from "../components/ui";
import { useApi, type ContractRow, type Meta, type Summary } from "../lib/api";
import { MONTHS, date, int, nok, pct } from "../lib/format";
import { useScope } from "../lib/scope";

const tick = { fontSize: 11, fill: "var(--muted)" };

function diff(label: string, mine: number, other: number) {
  const d = mine - other;
  return <span>{label}: {pct(other)} <span style={{ color: d > 2 ? "var(--hi)" : d < -2 ? "var(--lo)" : undefined }}>({d > 0 ? "+" : ""}{d.toLocaleString("nb-NO", { maximumFractionDigits: 1 })} pp)</span></span>;
}

// Sammenligning med hele Norge, og med kommuner i samme KOSTRA-gruppe når valget har kommuner
function vs(mine: number | null | undefined, nat: number | null | undefined, kostra?: number | null, grupper?: number[]) {
  if (mine == null || nat == null) return null;
  const k = kostra != null && grupper?.length ? diff(`KOSTRA-gruppe ${grupper.join(", ")}`, mine, kostra) : null;
  return <>{diff("Norge", mine, nat)}{k && <><br />{k}</>}</>;
}

export default function Overview() {
  const { param: buyers, buyers: sel } = useScope();
  const { data: s } = useApi<Summary>("/api/summary", { buyers }, { refetchInterval: (q) => (q.state.data?.n ? false : 3000) });
  const { data: meta } = useApi<Meta>("/api/meta");
  const { data: top } = useApi<{ rows: ContractRow[] }>("/api/contracts", { buyers, limit: 8, case_status: "uten" });
  if (!s) return <div className="space-y-4"><Skeleton className="h-10 w-80" /><Skeleton className="h-24" /><Skeleton className="h-72" /></div>;
  if (!s.n) return <Empty>{s.loading ? "Laster data og kjører modellene første gang. Det tar et øyeblikk …" : "Ingen kontrakter ennå. Trykk «Hent nye data»."}</Empty>;
  const monthly = s.monthly.map((m) => ({ ...m, label: `${MONTHS[Number(m.month.slice(5)) - 1]} ${m.month.slice(2, 4)}` }));
  const maxFlag = Math.max(...s.flag_counts.map((f) => f.n));
  // Skalert mot høyeste sektor, så forskjellene synes (snittprioriteten ligger langt under 100)
  const maxSector = Math.max(1, ...s.by_sector.slice(0, 10).map((g) => g.avg_score));
  const o = meta?.observasjonsenhet;
  const sources = `${meta?.sources?.Doffin?.status === "ok" ? "TED og Doffin" : "TED"}, tildelt`;
  const open = Object.entries(s.cases).filter(([k]) => !k.startsWith("Avsluttet")).reduce((a, [, n]) => a + n, 0);
  return (
    <div>
      <PageHead title={sel.length ? "Oversikt for dine enheter" : "Oversikt"}
        desc={`${int(s.n)} tildelte kontrakter fra ${int(s.n_buyers)} oppdragsgivere, ${date(s.period[0])} til ${date(s.period[1])}.${sel.length ? ` Tallene sammenlignes med hele Norge${s.kostra ? ` og med ${int(s.kostra.n_kommuner)} kommuner i samme KOSTRA-gruppe (SSB 2024)` : ""}.` : " Velg «Mine enheter» øverst for å se bare dine kommuner og foretak."}`}
        right={<Link to="/arbeid" className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-sm font-medium text-surface transition-colors duration-150 hover:bg-[color-mix(in_srgb,var(--accent)_88%,black)]">Åpne arbeidsflaten <Icon name="arrow" /></Link>} />

      <KpiRow>
        <Kpi to="/arbeid" label="Kontrakter" value={int(s.n)} sub={sources} />
        <Kpi to="/arbeid?tier=Høy" label="Høy prioritet" value={int(s.n_high)} sub="Topp 5 % nasjonalt (rangering, ikke sannsynlighet)" />
        <Kpi to="/arbeid?tier=Forhøyet" label="Forhøyet" value={int(s.n_elevated)} sub="Persentil 80–95" />
        <Kpi to="/arbeid?flag=single_bid" label="Ett registrert tilbud" value={pct(s.single_bid_pct)} sub="Av kontrakter med oppgitt antall tilbud" compare={vs(s.single_bid_pct, s.national?.single_bid_pct, s.kostra?.single_bid_pct, s.kostra?.grupper)} />
        <Kpi to={`/arbeid?method=${encodeURIComponent("Direkte tildeling")}`} label="Direkte tildelinger" value={pct(s.direct_pct)} sub="Andel av kontraktene" compare={vs(s.direct_pct, s.national?.direct_pct, s.kostra?.direct_pct, s.kostra?.grupper)} />
        <Kpi to="/arbeid?sak=apne" label="Åpne saker" value={int(open)} sub="Saker som ikke er avsluttet" />
      </KpiRow>
      <p className="mt-2.5 max-w-[120ch] text-xs leading-relaxed text-muted">
        {o && !sel.length && <>Én kontrakt = én kunngjort tildeling. {int(o.kunngjøringer)} kunngjøringer fra TED og Doffin gir {int(o.kontrakter)} kontrakter
          i {int(o.forløp)} anskaffelsesforløp: {int(Object.values(o.duplikater).reduce((a, b) => a + b, 0))} duplikater er slått sammen, og{" "}
          {int(o.per_type.intensjon ?? 0)} intensjonskunngjøringer telles for seg. </>}
        Samlet tildelt verdi: {nok(s.total_value)}. Bare {pct(s.coverage_pct, 0)} av kontraktene oppgir både estimat og tildelt verdi. Rammeavtaler oppgir ofte maksverdi. <Link to="/metode" className="underline">Metode</Link>
      </p>
      {s.regler.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          <span className="mr-1 text-xs text-muted" title="Slår ut uansett hvordan resten av datasettet ser ut, i motsetning til prioriteten">Faste kontrollpunkter</span>
          {s.regler.map((x) => <Link key={x.key} to={`/arbeid?flag=regel:${x.key}`} className="inline-flex items-center gap-1.5 rounded-md border border-[color-mix(in_srgb,var(--hi)_38%,transparent)] px-2 py-1 text-hi transition-colors duration-150 hover:bg-hi-soft"><Icon name="rule" className="h-3.5 w-3.5" />{x.label} <span className="num font-semibold">{int(x.n)}</span></Link>)}
        </div>)}

      <div className="mt-6 grid items-start gap-5 lg:grid-cols-[1.55fr_1fr]">
        <Card title="Høyest prioritert uten sak" desc={<>Stripen viser hva prioriteten består av. <SourceLegend className="mt-1.5" /></>}
          right={<Link to="/arbeid?sak=uten" className="-my-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-accent hover:bg-sunk">Se alle <Icon name="arrow" className="h-3.5 w-3.5" /></Link>}>
          {top ? (
            <ul className="-mx-2 -my-1">
              {top.rows.map((r) => (
                <li key={r.id}>
                  <Link to={`/arbeid?id=${encodeURIComponent(r.id)}`} className="group flex items-start gap-4 rounded-md px-2 py-2.5 transition-colors duration-150 hover:bg-sunk">
                    <span className="num w-8 shrink-0 pt-px text-right text-lg font-semibold leading-tight" style={{ color: tierColor(r.tier) }}>{Math.round(r.priority)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-3">
                        <span className="line-clamp-2 text-sm font-medium group-hover:underline sm:line-clamp-1">{r.title}</span>
                        <span className="shrink-0 max-sm:hidden"><TierBadge tier={r.tier} /></span>
                      </span>
                      <span className="block truncate text-xs text-muted">{r.buyer} · {r.flags.filter((f) => f.source !== "datakvalitet").map((f) => f.label).slice(0, 3).join(" · ")}</span>
                      <ScoreStrip flags={r.flags} priority={r.priority} size="sm" className="mt-2" />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : <Skeleton className="h-64" />}
        </Card>
        <Card title="Hyppigste røde flagg" desc="Fargen viser kilden. Velg et flagg for å se kontraktene.">
          <ul className="space-y-2.5">
            {s.flag_counts.slice(0, 10).map((f) => (
              <li key={f.key}>
                <Link to={`/arbeid?flag=${f.key}`} className="group block">
                  <div className="flex justify-between gap-2 text-sm"><span className="truncate group-hover:underline">{f.label}</span><span className="num text-muted">{int(f.n)}</span></div>
                  <Meter value={f.n} max={maxFlag} color={SOURCE_COLOR[sourceKey(f.source ?? "kunngjøring")]} className="mt-1" />
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[1.55fr_1fr]">
        <Card title="Kontrakter per måned" desc="Flagget = høy eller forhøyet prioritet">
          <div className="h-64">
            <ResponsiveContainer>
              <ComposedChart data={monthly} margin={{ left: -18, right: 16, top: 4 }}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={18} />
                <YAxis tick={tick} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: "var(--fg)" }}>{v}</span>} />
                <Bar dataKey="n" name="Alle tildelinger" fill="color-mix(in srgb, var(--accent) 22%, var(--surface))" radius={[2, 2, 0, 0]} />
                <Bar dataKey="flagged" name="Flagget" fill="var(--accent)" radius={[2, 2, 0, 0]} />
                <Line dataKey="direct" name="Direkte tildelinger" stroke="var(--hi)" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Prioritet per sektor" desc="Snittprioritet per sektor. Rangeringen er nasjonal, så forskjeller kan skyldes hvordan sektorene kjøper, ikke risiko.">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted"><th className="pb-2 font-normal">Sektor</th><th className="pb-2 text-right font-normal">Antall</th><th className="pb-2 text-right font-normal">Ett tilbud</th><th className="w-24 pb-2 pl-3 font-normal">Snitt</th></tr></thead>
            <tbody>
              {s.by_sector.slice(0, 10).map((g) => (
                <tr key={g.name} className="border-t border-line">
                  <td className="py-1.5 pr-2">{g.name}</td>
                  <td className="num text-right">{int(g.n)}</td>
                  <td className="num text-right">{pct(g.single_bid_pct, 0)}</td>
                  <td className="pl-3"><div className="flex items-center gap-2"><Meter value={g.avg_score} max={maxSector} className="flex-1" /><span className="num w-6 text-right text-xs">{Math.round(g.avg_score)}</span></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
