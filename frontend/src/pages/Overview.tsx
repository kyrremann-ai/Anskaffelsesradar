import { Link } from "react-router-dom";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Empty, Kpi, Meter, PageHead, Skeleton, TierBadge, scoreColor } from "../components/ui";
import { useApi, type ContractRow, type Meta, type Summary } from "../lib/api";
import { MONTHS, date, int, nok, pct } from "../lib/format";
import { useScope } from "../lib/scope";

const tick = { fontSize: 11, fill: "var(--muted)" };

function vs(mine: number | null | undefined, nat: number | null | undefined) {
  if (mine == null || nat == null) return null;
  const d = mine - nat;
  return <span>Norge: {pct(nat)} <span style={{ color: d > 2 ? "var(--hi)" : d < -2 ? "var(--lo)" : undefined }}>({d > 0 ? "+" : ""}{d.toLocaleString("nb-NO", { maximumFractionDigits: 1 })} pp)</span></span>;
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
  const sources = `${meta?.sources?.Doffin?.status === "ok" ? "TED og Doffin" : "TED"}, tildelt`;
  const open = Object.entries(s.cases).filter(([k]) => !k.startsWith("Avsluttet")).reduce((a, [, n]) => a + n, 0);
  return (
    <div>
      <PageHead title={sel.length ? "Oversikt for dine enheter" : "Oversikt"}
        desc={`${int(s.n)} tildelte kontrakter fra ${int(s.n_buyers)} oppdragsgivere, ${date(s.period[0])} til ${date(s.period[1])}.${sel.length ? " Tallene sammenlignes med hele Norge." : " Velg «Mine enheter» øverst for å se bare dine kommuner og foretak."}`}
        right={<Link to="/arbeid" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-surface hover:opacity-90">Åpne arbeidsflaten →</Link>} />

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 2xl:grid-cols-6">
        <Kpi to="/arbeid" label="Kontrakter" value={int(s.n)} sub={sources} />
        <Kpi to="/arbeid?tier=Høy" label="Høy prioritet" value={int(s.n_high)} sub="Topp 5 % nasjonalt" />
        <Kpi to="/arbeid?tier=Forhøyet" label="Forhøyet" value={int(s.n_elevated)} sub="Persentil 80–95" />
        <Kpi to="/arbeid?flag=single_bid" label="Kun ett tilbud" value={pct(s.single_bid_pct)} sub="Av kontrakter med tilbudstall" compare={vs(s.single_bid_pct, s.national?.single_bid_pct)} />
        <Kpi to={`/arbeid?method=${encodeURIComponent("Direkte tildeling")}`} label="Direkte tildelinger" value={pct(s.direct_pct)} sub="Uten konkurranse" compare={vs(s.direct_pct, s.national?.direct_pct)} />
        <Kpi to="/arbeid?sak=apne" label="Åpne saker" value={int(open)} sub={`Tildelt verdi ${nok(s.total_value)}`} />
      </div>
      <p className="mt-2 text-xs text-muted">Bare {pct(s.coverage_pct, 0)} av kontraktene oppgir både estimat og tildelt verdi. Rammeavtaler oppgir ofte maksverdi.</p>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card title="Kontrakter per måned" desc="Flagget = høy eller forhøyet prioritet">
          <div className="h-64">
            <ResponsiveContainer>
              <ComposedChart data={monthly} margin={{ left: -18, right: 4, top: 4 }}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} interval={2} />
                <YAxis tick={tick} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="n" name="Alle tildelinger" fill="var(--accent)" fillOpacity={0.25} radius={[3, 3, 0, 0]} />
                <Bar dataKey="flagged" name="Flagget" fill="var(--mid)" radius={[3, 3, 0, 0]} />
                <Line dataKey="direct" name="Direkte tildelinger" stroke="var(--hi)" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Hyppigste røde flagg" desc="Klikk for å se kontraktene i arbeidsflaten">
          <ul className="space-y-2.5">
            {s.flag_counts.slice(0, 10).map((f) => (
              <li key={f.key}>
                <Link to={`/arbeid?flag=${f.key}`} className="group block">
                  <div className="flex justify-between gap-2 text-sm"><span className="truncate group-hover:underline">{f.label}</span><span className="num text-muted">{int(f.n)}</span></div>
                  <Meter value={f.n} max={maxFlag} color="var(--accent)" className="mt-1" />
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card title="Høyest prioritert uten sak" right={<Link to="/arbeid?sak=uten" className="text-xs text-accent hover:underline">Alle</Link>}>
          {top ? (
            <ul className="-my-2 divide-y divide-line">
              {top.rows.map((r) => (
                <li key={r.id}>
                  <Link to={`/arbeid?id=${encodeURIComponent(r.id)}`} className="-mx-2 flex items-center gap-4 rounded px-2 py-2.5 hover:bg-sunk">
                    <span className="num w-9 shrink-0 text-right text-base font-bold" style={{ color: scoreColor(r.priority) }}>{Math.round(r.priority)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{r.title}</span>
                      <span className="block truncate text-xs text-muted">{r.buyer} · {r.flags.map((f) => f.label).slice(0, 3).join(" · ")}</span>
                    </span>
                    <TierBadge tier={r.tier} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : <Skeleton className="h-64" />}
        </Card>
        <Card title="Prioritet per sektor">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted"><th className="pb-2 font-normal">Sektor</th><th className="pb-2 text-right font-normal">Antall</th><th className="pb-2 text-right font-normal">Ett tilbud</th><th className="w-24 pb-2 pl-3 font-normal">Snitt</th></tr></thead>
            <tbody>
              {s.by_sector.slice(0, 10).map((g) => (
                <tr key={g.name} className="border-t border-line">
                  <td className="py-1.5 pr-2">{g.name}</td>
                  <td className="num text-right">{int(g.n)}</td>
                  <td className="num text-right">{pct(g.single_bid_pct, 0)}</td>
                  <td className="pl-3"><div className="flex items-center gap-2"><Meter value={g.avg_score} className="flex-1" /><span className="num w-6 text-right text-xs">{Math.round(g.avg_score)}</span></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
