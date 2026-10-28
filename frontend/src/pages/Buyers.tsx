import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Card, Meter, PageHead, Search, Skeleton, cx } from "../components/ui";
import { useApi, type Summary } from "../lib/api";
import { int, nok, pct } from "../lib/format";
import { useScope } from "../lib/scope";

const SORTS = [["avg_score", "Snittprioritet"], ["n", "Antall"], ["single_bid_pct", "Ett tilbud"], ["direct_pct", "Direkte"]] as const;

export default function Buyers() {
  const { param: buyers, buyers: mine, setBuyers } = useScope();
  const { data: s } = useApi<Summary>("/api/summary", { buyers });
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<(typeof SORTS)[number][0]>("avg_score");
  const rows = useMemo(() => (s?.by_buyer ?? []).filter((b) => b.name.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => ((b[sort] ?? -1) as number) - ((a[sort] ?? -1) as number)), [s, q, sort]);
  if (!s) return <Skeleton className="h-96" />;
  const pts = (s.by_buyer ?? []).map((b) => ({ x: b.direct_pct ?? 0, y: b.single_bid_pct ?? 0, z: b.n, name: b.name }));
  return (
    <div>
      <PageHead title="Oppdragsgivere"
        desc={`Konkurranse og flagg for ${int(s.by_buyer.length)} oppdragsgivere${mine.length ? "" : " med minst 8 tildelinger"}. Mønstre over tid sier mer enn enkeltkontrakter.`} />
      <Card title="Konkurranse per oppdragsgiver" desc="Andel direkte tildelinger mot andel med ett tilbud. Størrelse = antall kontrakter." className="mb-4">
        <div className="h-72">
          <ResponsiveContainer>
            <ScatterChart margin={{ left: -10, right: 10, top: 6, bottom: 6 }}>
              <CartesianGrid stroke="var(--line)" />
              <XAxis type="number" dataKey="x" name="Direkte" unit=" %" tick={{ fontSize: 11, fill: "var(--muted)" }} />
              <YAxis type="number" dataKey="y" name="Ett tilbud" unit=" %" tick={{ fontSize: 11, fill: "var(--muted)" }} />
              <ZAxis dataKey="z" range={[20, 400]} />
              <Tooltip content={({ payload }) => payload?.[0] ? (
                <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
                  <div className="font-medium">{payload[0].payload.name}</div>
                  <div className="num text-muted">{payload[0].payload.z} kontrakter · direkte {pct(payload[0].payload.x)} · ett tilbud {pct(payload[0].payload.y)}</div>
                </div>) : null} />
              <Scatter data={pts} fill="var(--accent)" fillOpacity={0.55} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Search value={q} onChange={setQ} placeholder="Søk oppdragsgiver" />
        <div className="flex flex-wrap gap-1 text-xs" role="group" aria-label="Sorter etter">
          {SORTS.map(([k, l]) => (
            <button key={k} onClick={() => setSort(k)} aria-pressed={sort === k} className={cx("rounded-md px-2.5 py-2", sort === k ? "bg-accent text-surface" : "bg-sunk text-muted hover:text-fg")}>{l}</button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border border-line bg-surface">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-sunk text-left text-xs text-muted">
            <tr>{["Oppdragsgiver", "Kontrakter", "Flagget", "Ett tilbud", "Direkte", "Tildelt verdi", "Snittprioritet", ""].map((h, i) =>
              <th key={i} className={cx("whitespace-nowrap px-3 py-2.5 font-medium", i > 0 && i < 6 && "text-right", i === 0 && "sticky left-0 z-10 bg-sunk")}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.buyer_id} className="group border-t border-line hover:bg-sunk">
                <td className="sticky left-0 z-10 max-w-[45vw] bg-surface px-3 py-2 group-hover:bg-sunk sm:max-w-none"><Link to={`/arbeid`} onClick={() => setBuyers([b.buyer_id!])} className="font-medium hover:underline" title="Sett som mine enheter og åpne arbeidsflaten">{b.name}</Link></td>
                <td className="num px-3 text-right">{int(b.n)}</td>
                <td className="num px-3 text-right">{int(b.n_flagged)}</td>
                <td className="num px-3 text-right">{pct(b.single_bid_pct, 0)}</td>
                <td className="num px-3 text-right">{pct(b.direct_pct, 0)}</td>
                <td className="num whitespace-nowrap px-3 text-right">{nok(b.total_value)}</td>
                <td className="px-3"><div className="flex items-center gap-2"><Meter value={b.avg_score} className="w-20" /><span className="num text-xs">{Math.round(b.avg_score)}</span></div></td>
                <td className="px-3 text-right">
                  <button className="-my-1 whitespace-nowrap rounded px-2 py-1.5 text-xs text-accent hover:underline"
                    onClick={() => setBuyers(mine.includes(b.buyer_id!) ? mine.filter((x) => x !== b.buyer_id) : [...mine, b.buyer_id!])}>
                    {mine.includes(b.buyer_id!) ? "Fjern fra mine" : "Legg til i mine"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
