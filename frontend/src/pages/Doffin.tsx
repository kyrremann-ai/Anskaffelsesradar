import { useEffect, useState } from "react";
import { Button, Empty, PageHead, Search, Skeleton, cx } from "../components/ui";
import { useApi, type Meta } from "../lib/api";
import { date, int, nok } from "../lib/format";

type Hit = { id: string; url: string; title: string; buyer: string; type: string; pub_date: string; deadline: string | null;
  est_value: number | null; flags: { key: string; label: string }[] };

export default function Doffin() {
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [only, setOnly] = useState(false);
  useEffect(() => { const t = setTimeout(() => setDq(q), 350); return () => clearTimeout(t); }, [q]);
  const { data: meta } = useApi<Meta>("/api/meta");
  const { data, isLoading, isError, error, refetch, isFetching } = useApi<{ total: number; hits: Hit[]; error?: string }>(
    "/api/doffin/latest", { q: dq }, { refetchInterval: 120_000, enabled: !!meta?.doffin });
  const hits = (data?.hits ?? []).filter((h) => !only || h.flags.length);
  return (
    <div>
      <PageHead title="Doffin live" desc="De nyeste kunngjøringene fra Doffin, hentet direkte. Tidlige varselsignaler før kontrakten tildeles, også under EØS-terskel."
        right={<Button variant="outline" onClick={() => refetch()} disabled={isFetching || !meta?.doffin}>↻ Oppdater</Button>} />
      {meta && !meta.doffin ? (
        <Empty>Doffin-nøkkel mangler. Hent en gratis nøkkel i DFØs API-portal og sett <code>DOFFIN_API_KEY</code> i <code>.env</code>.</Empty>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Search value={q} onChange={setQ} placeholder="Søk i Doffin, f.eks. «IT-drift»" />
            <button onClick={() => setOnly(!only)} className={cx("rounded-md px-3 py-2 text-xs", only ? "bg-accent text-surface" : "bg-sunk text-muted")}>Kun med varselsignal</button>
            {data && <span className="ml-auto text-xs text-muted">{int(data.total)} kunngjøringer i Doffin</span>}
          </div>
          {isLoading ? <Skeleton className="h-96" /> : isError ? <Empty>{String(error?.message)}</Empty> : !hits.length ? <Empty>Ingen treff.</Empty> : (
            <ul className="divide-y divide-line rounded-md border border-line bg-surface">
              {hits.map((h) => (
                <li key={h.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <a href={h.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">{h.title}</a>
                    <span className="num text-xs text-muted">{date(h.pub_date)}{h.deadline ? ` · frist ${date(h.deadline)}` : ""}</span>
                  </div>
                  <div className="text-xs text-muted">{h.buyer} · {h.type}{h.est_value ? ` · ${nok(h.est_value)}` : ""}</div>
                  {h.flags.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{h.flags.map((f) => <span key={f.key} className="rounded bg-mid-soft px-1.5 py-0.5 text-xs text-mid">{f.label}</span>)}</div>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
