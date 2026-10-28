import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CaseFile } from "../components/CaseFile";
import { Button, Empty, PageHead, Search, Select, Skeleton, TierBadge, cx, scoreColor } from "../components/ui";
import { STATIC, qs, useApi, type ContractRow, type Meta, type Summary } from "../lib/api";
import { date } from "../lib/format";
import { exportCsv } from "../lib/static";
import { useScope } from "../lib/scope";

const PAGE = 40;

/** Arbeidsflate: prioritert kø til venstre, saksmappe til høyre. Erstatter Risikoregister + Saksliste. */
export default function Workbench() {
  const { param: buyers } = useScope();
  const [sp, setSp] = useSearchParams();
  const get = (k: string) => sp.get(k) ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    if (k !== "id") n.delete("id");
    setSp(n, { replace: k !== "id" });
  };
  const [q, setQ] = useState(get("q"));
  useEffect(() => { const t = setTimeout(() => q !== get("q") && set("q", q), 350); return () => clearTimeout(t); }, [q]); // eslint-disable-line
  const [limit, setLimit] = useState(PAGE);
  const [exportError, setExportError] = useState("");
  const filters = { buyers, q: get("q"), tier: get("tier"), flag: get("flag"), method: get("method"), sector: get("sector"),
    case_status: get("sak"), supplier: get("supplier") };
  useEffect(() => setLimit(PAGE), [JSON.stringify(filters)]); // eslint-disable-line
  const { data, isLoading, isFetching } = useApi<{ rows: ContractRow[]; total: number }>("/api/contracts", { ...filters, limit });
  const { data: meta } = useApi<Meta>("/api/meta");
  const { data: sum } = useApi<Summary>("/api/summary", { buyers });
  const sel = get("id") || (typeof window !== "undefined" && window.innerWidth >= 1024 ? data?.rows[0]?.id : "") || "";
  const flagItems = (sum?.flag_counts ?? []).map((f) => ({ v: f.key, l: `${f.label} (${f.n})` }));

  return (
    <div>
      <PageHead title="Arbeidsflate"
        desc="Kontraktene i dine enheter, sortert etter prioritet. Velg en kontrakt for å se hvorfor den er flagget, sette status og skrive notat."
        right={STATIC
          ? <Button variant="outline" onClick={() => exportCsv(filters).catch((e) => setExportError(String(e.message)))}>Eksporter CSV</Button>
          : <a className="inline-flex items-center rounded-md border border-line bg-surface px-3 py-1.5 text-sm hover:bg-sunk"
            href={`/api/contracts/export.csv${qs(filters)}`}>Eksporter CSV</a>} />
      {exportError && <p className="-mt-2 mb-3 text-sm text-hi">{exportError}</p>}
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <Search value={q} onChange={setQ} placeholder="Søk tittel, oppdragsgiver, leverandør" />
        <Select label="Prioritet" value={get("tier")} onChange={(v) => set("tier", v)} placeholder="Alle prioriteter"
          items={["Høy", "Forhøyet", "Lav"].map((t) => ({ v: t, l: `${t} prioritet` }))} />
        <Select label="Flagg" value={get("flag")} onChange={(v) => set("flag", v)} placeholder="Alle flagg" items={flagItems} />
        <Select label="Prosedyre" value={get("method")} onChange={(v) => set("method", v)} placeholder="Alle prosedyrer"
          items={["Åpen", "Begrenset", "Forhandling", "Direkte tildeling", "Annet"].map((m) => ({ v: m, l: m }))} />
        <Select label="Sak" value={get("sak")} onChange={(v) => set("sak", v)} placeholder="Med og uten sak"
          items={[{ v: "uten", l: "Uten sak" }, { v: "med", l: "Med sak" }, ...(meta?.case_statuses ?? []).map((s) => ({ v: s, l: `Sak: ${s}` }))]} />
        {get("supplier") && <Button variant="outline" onClick={() => set("supplier", "")}>Leverandør {get("supplier")} ✕</Button>}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
        <section className={cx("min-w-0 rounded-md border border-line bg-surface", sel && "hidden lg:block")}>
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5 text-xs text-muted">
            <span>{data ? `${data.total.toLocaleString("nb-NO")} kontrakter` : "…"}</span>
            {isFetching && <span>Oppdaterer …</span>}
          </div>
          {isLoading ? <div className="space-y-2 p-3">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
            : !data?.rows.length ? <Empty>Ingen kontrakter matcher filtrene.</Empty> : (
              <ul className="max-h-[calc(100vh-260px)] divide-y divide-line overflow-y-auto">
                {data.rows.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => set("id", r.id)} aria-current={r.id === sel}
                      className={cx("grid w-full grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-3 text-left hover:bg-sunk", r.id === sel && "bg-accent-soft shadow-[inset_3px_0_0_var(--accent)]")}>
                      <span className="num text-lg font-bold" style={{ color: scoreColor(r.priority) }}>{Math.round(r.priority)}</span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium">{r.title}</span>
                        <span className="block truncate text-xs text-muted">{r.buyer} · {date(r.pub_date)}</span>
                        <span className="mt-1 flex flex-wrap items-center gap-1">
                          <TierBadge tier={r.tier} />
                          {r.case_status && <span className="rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent">{r.case_status}</span>}
                          {r.flags.slice(0, 2).map((f) => <span key={f.key} className="truncate rounded bg-sunk px-1.5 py-0.5 text-xs text-muted">{f.label}</span>)}
                          {r.flags.length > 2 && <span className="text-xs text-muted">+{r.flags.length - 2}</span>}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
                {data.total > data.rows.length && (
                  <li className="p-3 text-center"><Button variant="outline" onClick={() => setLimit(limit + PAGE)}>Vis flere</Button></li>
                )}
              </ul>
            )}
        </section>
        <section className={cx("min-w-0 rounded-md border border-line bg-surface", !sel && "hidden lg:block")}>
          {sel ? (
            <>
              <button className="px-5 pt-4 text-sm text-accent lg:hidden" onClick={() => set("id", "")}>← Tilbake til listen</button>
              <CaseFile key={sel} id={sel} onOpen={(id) => set("id", id)} />
            </>
          ) : <Empty>Velg en kontrakt i listen.</Empty>}
        </section>
      </div>
    </div>
  );
}
