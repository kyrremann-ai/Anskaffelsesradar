import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CaseFile } from "../components/CaseFile";
import { Button, CheckBadge, Empty, Icon, PageHead, ScoreStrip, Search, Select, Skeleton, SourceLegend, TierBadge, cx, tierColor } from "../components/ui";
import { STATIC, qs, useApi, type ContractRow, type Meta, type Summary } from "../lib/api";
import { date } from "../lib/format";
import { exportCsv, exportLabels } from "../lib/static";
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
    case_status: get("sak"), supplier: get("supplier"), rank: get("rank") };
  useEffect(() => setLimit(PAGE), [JSON.stringify(filters)]); // eslint-disable-line
  const { data, isLoading, isFetching } = useApi<{ rows: ContractRow[]; total: number }>("/api/contracts", { ...filters, limit });
  const { data: meta } = useApi<Meta>("/api/meta");
  const { data: sum } = useApi<Summary>("/api/summary", { buyers });
  const sel = get("id") || (typeof window !== "undefined" && window.innerWidth >= 1024 ? data?.rows[0]?.id : "") || "";
  // Smal skjerm: saksmappen erstatter listen og filtrene, så start øverst i den
  useEffect(() => {
    if (get("id") && window.innerWidth < 1024) window.scrollTo(0, 0);
  }, [get("id")]); // eslint-disable-line
  const flagItems = [
    ...(sum?.regler ?? []).map((x) => ({ v: `regel:${x.key}`, l: `Kontrollpunkt: ${x.label} (${x.n})` })),
    ...(sum?.n_intents ? [{ v: "type:intensjon", l: `Intensjonskunngjøringer (${sum.n_intents})` }] : []),
    ...(sum?.flag_counts ?? []).map((f) => ({ v: f.key, l: `${f.label} (${f.n})` })),
  ];

  return (
    <div>
      <PageHead title="Arbeidsflate"
        desc="Kontraktene i dine enheter, sortert etter prioritet. Røde ruter er faste kontrollpunkter som gjelder uansett rangering. Velg en kontrakt for å se hvorfor den er flagget, sette status og skrive notat."
        right={STATIC
          ? <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => exportCsv(filters).catch((e) => setExportError(String(e.message)))}><Icon name="download" />Eksporter CSV</Button>
            <Button variant="outline" title="Revisorenes vurderinger (relevant / ikke relevant) som fasit for modellene"
              onClick={() => exportLabels().catch((e) => setExportError(String(e.message)))}><Icon name="download" />Revisorvurderinger</Button>
          </div>
          : <div className="flex flex-wrap gap-2">
            <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-surface px-3 text-sm font-medium transition-colors duration-150 hover:border-field hover:bg-sunk"
              href={`/api/contracts/export.csv${qs(filters)}`}><Icon name="download" />Eksporter CSV</a>
            <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-surface px-3 text-sm font-medium transition-colors duration-150 hover:border-field hover:bg-sunk"
              title="Revisorenes vurderinger (relevant / ikke relevant) som fasit for modellene" href="/api/labels.csv"><Icon name="download" />Revisorvurderinger</a>
          </div>} />
      {exportError && <p className="-mt-2 mb-3 text-sm text-hi">{exportError}</p>}
      <div className={cx("mb-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap", get("id") && "max-lg:hidden")}>
        <Search value={q} onChange={setQ} placeholder="Søk tittel, oppdragsgiver, leverandør" />
        <Select label="Rangering" value={get("rank")} onChange={(v) => set("rank", v)} placeholder="Nasjonal rangering"
          items={[{ v: "gruppe", l: "Innen sammenligningsgruppe" }]} />
        <Select label="Prioritet" value={get("tier")} onChange={(v) => set("tier", v)} placeholder="Alle prioriteter"
          items={["Høy", "Forhøyet", "Lav"].map((t) => ({ v: t, l: `${t} prioritet` }))} />
        <Select label="Flagg" value={get("flag")} onChange={(v) => set("flag", v)} placeholder="Alle flagg" items={flagItems} />
        <Select label="Prosedyre" value={get("method")} onChange={(v) => set("method", v)} placeholder="Alle prosedyrer"
          items={["Åpen", "Begrenset", "Forhandling", "Direkte tildeling", "Annet"].map((m) => ({ v: m, l: m }))} />
        <Select label="Sak" value={get("sak")} onChange={(v) => set("sak", v)} placeholder="Med og uten sak"
          items={[{ v: "uten", l: "Uten sak" }, { v: "med", l: "Med sak" }, { v: "apne", l: "Åpne saker" }, ...(meta?.case_statuses ?? []).map((s) => ({ v: s, l: `Sak: ${s}` }))]} />
        {get("supplier") && <Button variant="outline" onClick={() => set("supplier", "")}>Leverandør {get("supplier")} <Icon name="close" className="h-3.5 w-3.5" /></Button>}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(340px,440px)_minmax(0,1fr)]">
        <section className={cx("min-w-0 self-start overflow-hidden rounded-lg border border-line bg-surface lg:sticky lg:top-[112px]", sel && "hidden lg:block")}>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5 text-xs text-muted">
            <span className="font-medium text-fg">{data ? `${data.total.toLocaleString("nb-NO")} kontrakter` : "…"}{isFetching && <span className="ml-2 font-normal text-muted">oppdaterer …</span>}</span>
            <SourceLegend />
          </div>
          {isLoading ? <div className="space-y-2 p-3">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
            : !data?.rows.length ? <Empty>Ingen kontrakter matcher filtrene.</Empty> : (
              <ul className="max-h-[calc(100vh-190px)] divide-y divide-line overflow-y-auto overscroll-contain">
                {data.rows.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => set("id", r.id)} aria-current={r.id === sel}
                      className={cx("grid w-full grid-cols-[36px_minmax(0,1fr)] gap-3 px-4 py-3 text-left transition-colors duration-150",
                        r.id === sel ? "bg-accent-soft" : "hover:bg-sunk")}>
                      <span className="num pt-px text-right text-lg font-semibold leading-tight" style={{ color: tierColor(get("rank") === "gruppe" ? r.peer_tier : r.tier) }}>{Math.round(r.priority)}</span>
                      <span className="min-w-0">
                        <span className={cx("line-clamp-2 text-sm sm:line-clamp-1", r.id === sel ? "font-semibold" : "font-medium")}>{r.title}</span>
                        <span className="block truncate text-xs text-muted">{r.buyer} · {date(r.pub_date)}</span>
                        <ScoreStrip flags={r.flags} priority={r.priority} size="sm" className="my-2" />
                        <span className="flex flex-wrap items-center gap-1">
                          <TierBadge tier={get("rank") === "gruppe" ? r.peer_tier : r.tier} />
                          {(r.regler ?? []).map((x) => <CheckBadge key={x.key} label={x.label} why={x.why} />)}
                          {r.case_status && <span className="inline-flex items-center gap-1 rounded bg-accent-soft px-1.5 py-px text-xs font-medium text-accent"><Icon name="flag" className="h-3 w-3" />{r.case_status}</span>}
                          {r.klagerisiko != null && r.klagerisiko >= 80 && (
                            <span className="rounded border border-line px-1.5 py-px text-xs text-muted" title="Klagerisiko (eksperimentell): persentil blant kontraktene i radaren, ikke en sannsynlighet. Teller ikke i prioriteten.">
                              Klagerisiko p{Math.round(r.klagerisiko)}</span>)}
                          {(() => {
                            const n = r.flags.filter((f) => f.source !== "datakvalitet" && !f.dismissed).length;
                            return n > 0 && <span className="text-xs text-muted">{n} flagg</span>;
                          })()}
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
        <section className={cx("min-w-0 rounded-lg border border-line bg-surface", !sel && "hidden lg:block")}>
          {sel ? (
            <>
              <button className="mx-3 mt-3 inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm font-medium text-accent hover:bg-sunk lg:hidden" onClick={() => set("id", "")}><Icon name="back" />Tilbake til listen</button>
              <CaseFile key={sel} id={sel} onOpen={(id) => set("id", id)} />
            </>
          ) : <Empty>Velg en kontrakt i listen.</Empty>}
        </section>
      </div>
    </div>
  );
}
