import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Card, Empty, PageHead, Search, Skeleton, TierBadge, cx, tierColor } from "../components/ui";
import { useApi, type ContractRow, type SupplierCard } from "../lib/api";
import { date, int, nok } from "../lib/format";
import { useScope } from "../lib/scope";

type SupplierRow = {
  orgnr: string; name: string; n: number; n_buyers: number; total_value: number; avg_priority: number; n_high: number;
  brreg_flags: string[]; orgform: string | null; stiftet: string | null; ansatte: number | null; in_brreg: boolean;
};

export function Suppliers() {
  const { param: buyers } = useScope();
  const [q, setQ] = useState("");
  const [flagged, setFlagged] = useState(false);
  const { data } = useApi<{ rows: SupplierRow[]; total: number }>("/api/suppliers", { buyers, q, only_flagged: flagged || undefined });
  return (
    <div>
      <PageHead title="Leverandører"
        desc="Alle leverandører med tildelinger i dine enheter, beriket med Enhetsregisteret og roller fra Brønnøysundregistrene. Sortert etter antall Brreg-flagg." />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Search value={q} onChange={setQ} placeholder="Søk navn eller orgnr" />
        <label className="flex min-h-9 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} />Bare med Brreg-flagg</label>
        {data && <span className="text-xs text-muted">{int(data.total)} leverandører</span>}
      </div>
      {!data ? <Skeleton className="h-96" /> : !data.rows.length ? <Empty>Ingen treff.</Empty> : (
        <div className="overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full text-sm sm:min-w-[820px]">
            <thead className="bg-sunk text-left text-xs text-muted">
              <tr><th className="px-3 py-2.5 font-medium sm:min-w-[200px]">Leverandør</th><th className="hidden px-3 font-medium sm:table-cell">Brreg-flagg</th>
                <th className="hidden px-3 text-right font-medium sm:table-cell">Kontrakter</th><th className="hidden px-3 text-right font-medium sm:table-cell">Oppdragsgivere</th>
                <th className="hidden px-3 text-right font-medium sm:table-cell">Verdi</th><th className="px-3 text-right font-medium" title="Snitt av prioriteten (0–100) for leverandørens kontrakter">Snitt&shy;prioritet</th></tr>
            </thead>
            <tbody>
              {data.rows.map((s) => (
                <tr key={s.orgnr} className="border-t border-line hover:bg-sunk">
                  <td className="px-3 py-2">
                    <Link to={`/leverandorer/${s.orgnr}`} className="font-medium hover:underline">{s.name}</Link>
                    <div className="font-mono text-xs text-muted">{s.orgnr}{s.stiftet ? ` · stiftet ${s.stiftet.slice(0, 4)}` : ""}{s.in_brreg ? "" : " · ikke hentet fra Brreg"}</div>
                    <div className="num mt-1 text-xs text-muted sm:hidden">{int(s.n)} kontrakter · {int(s.n_buyers)} oppdragsgivere · {nok(s.total_value)}</div>
                    {s.brreg_flags.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1 sm:hidden">{s.brreg_flags.map((f) => <span key={f} className="rounded bg-mid-soft px-1.5 py-0.5 text-xs text-mid">{f}</span>)}</div>}
                  </td>
                  <td className="hidden px-3 py-2 sm:table-cell">
                    <div className="flex flex-wrap gap-1">{s.brreg_flags.map((f) => <span key={f} className="rounded bg-mid-soft px-1.5 py-0.5 text-xs text-mid">{f}</span>)}</div>
                  </td>
                  <td className="num hidden px-3 text-right sm:table-cell">{int(s.n)}</td>
                  <td className="num hidden px-3 text-right sm:table-cell">{int(s.n_buyers)}</td>
                  <td className="num hidden whitespace-nowrap px-3 text-right sm:table-cell">{nok(s.total_value)}</td>
                  <td className="num px-3 text-right font-semibold">{Math.round(s.avg_priority)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type SupplierDetail = SupplierCard & {
  contracts: ContractRow[];
  links: { orgnr: string; name: string; via: string; rolle: string; is_buyer: boolean }[];
};

export function SupplierPage() {
  const { orgnr = "" } = useParams();
  const { param: buyers } = useScope();
  const { data: d } = useApi<SupplierDetail>(`/api/suppliers/${orgnr}`, { buyers });
  if (!d) return <Skeleton className="h-96" />;
  const b = d.brreg;
  return (
    <div>
      <PageHead title={b?.navn ?? orgnr} desc={<span className="font-mono">{orgnr} · <a className="hover:underline" href={d.brreg_url} target="_blank" rel="noreferrer">Åpne i Brønnøysundregistrene ↗</a></span>}
        right={<Link to={`/arbeid?supplier=${orgnr}`} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-surface">Kontraktene i arbeidsflaten →</Link>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Enhetsregisteret">
          {!b ? <p className="text-sm text-muted">Ikke hentet ennå.</p> : (
            <dl className="space-y-1 text-sm">
              {[["Status", b.kind === "slettet" ? `Slettet ${b.slettedato ?? ""}` : b.konkurs ? "Konkurs" : b.under_avvikling ? "Under avvikling" : "Aktiv"],
                ["Selskapsform", b.orgform], ["Stiftet", date(b.stiftet)], ["Ansatte", int(b.ansatte)],
                ["MVA-registrert", b.mva == null ? "–" : b.mva ? "Ja" : "Nei"], ["NACE", `${b.nace ?? "–"} ${b.nace_tekst ?? ""}`], ["Adresse", b.adresse || "–"]]
                .map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-muted">{k}</dt>
                  <dd className={cx("text-right", k === "Status" && v !== "Aktiv" && "font-medium text-hi")}>{v}</dd></div>)}
            </dl>
          )}
        </Card>
        <Card title="Roller">
          {!d.roles.length ? <p className="text-sm text-muted">Ingen roller registrert.</p> : (
            <ul className="space-y-1 text-sm">{d.roles.map((r, i) => <li key={i} className="flex justify-between gap-3"><span>{r.navn}</span><span className="text-xs text-muted">{r.rolle_navn}</span></li>)}</ul>
          )}
        </Card>
        <Card title="Regnskap" desc={d.regnskap?.aar ? `Regnskapsregisteret, ${d.regnskap.aar}` : "Regnskapsregisteret"}>
          {!d.regnskap || d.regnskap.driftsinntekter == null ? <p className="text-sm text-muted">Ikke registrert eller ikke hentet.</p> : (
            <dl className="space-y-1 text-sm">
              {([["Driftsinntekter", d.regnskap.driftsinntekter], ["Driftsresultat", d.regnskap.driftsresultat], ["Årsresultat", d.regnskap.aarsresultat],
                ["Egenkapital", d.regnskap.egenkapital], ["Gjeld", d.regnskap.gjeld]] as [string, number | null][])
                .map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-muted">{k}</dt>
                  <dd className={cx("num text-right", v != null && v < 0 && "text-hi")}>{nok(v)}</dd></div>)}
            </dl>
          )}
        </Card>
        <Card title="Eiere" desc="Aksjonærregisteret (Skatteetaten)">
          {!d.owners.length ? <p className="text-sm text-muted">Ingen eierdata importert.</p> : (
            <ul className="space-y-1 text-sm">{d.owners.map((o, i) => (
              <li key={i} className="flex justify-between gap-3">
                {o.orgnr ? <Link to={`/leverandorer/${o.orgnr}`} className="hover:underline">{o.name}</Link> : <span>{o.name}</span>}
                <span className="num text-xs text-muted">{o.share_pct != null ? `${o.share_pct.toLocaleString("nb-NO")} %` : "–"}</span>
              </li>))}
            </ul>
          )}
        </Card>
        <Card title="Registre og lister">
          <dl className="space-y-1 text-sm">
            {Object.entries(d.registers).map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-muted capitalize">{k}sregisteret</dt><dd>{v}</dd></div>)}
            <div className="flex justify-between gap-3"><dt className="text-muted">OpenSanctions</dt>
              <dd className={cx(d.sanctions && "text-hi")}>{d.sanctions ? d.sanctions.datasets : "Ingen treff"}</dd></div>
          </dl>
        </Card>
        <Card title="Koblinger via personer" desc="Andre virksomheter der samme personer har kontrollroller">
          {!d.links.length ? <p className="text-sm text-muted">Ingen koblinger funnet.</p> : (
            <ul className="space-y-1.5 text-sm">
              {d.links.map((l, i) => (
                <li key={i}>
                  <Link to={l.is_buyer ? `/arbeid` : `/leverandorer/${l.orgnr}`} className={cx("font-medium hover:underline", l.is_buyer && "text-hi")}>{l.name}</Link>
                  {l.is_buyer && <span className="ml-1 rounded bg-hi-soft px-1 text-xs text-hi">oppdragsgiver</span>}
                  <div className="text-xs text-muted">via {l.via} ({l.rolle})</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card title={`Kontrakter (${d.contracts.length})`} className="mt-4">
        <ul className="-my-2 divide-y divide-line">
          {d.contracts.map((r) => (
            <li key={r.id}>
              <Link to={`/arbeid?supplier=${orgnr}&id=${encodeURIComponent(r.id)}`} className="-mx-2 flex items-center gap-4 rounded px-2 py-2.5 hover:bg-sunk">
                <span className="num w-9 shrink-0 text-right font-bold" style={{ color: tierColor(r.tier) }}>{Math.round(r.priority)}</span>
                <span className="min-w-0 flex-1"><span className="line-clamp-2 text-sm font-medium sm:line-clamp-1">{r.title}</span>
                  <span className="block truncate text-xs text-muted">{r.buyer} · {date(r.pub_date)} · {nok(r.award_value)}</span></span>
                <TierBadge tier={r.tier} />
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
