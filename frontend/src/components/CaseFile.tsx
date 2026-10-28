import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { STATIC, api, queryClient, useApi, type ContractDetail, type Flag, type Meta, type SupplierCard } from "../lib/api";
import { saveWorkpaper } from "../lib/static";
import { date, dateTime, int, nok, pct, signedPct } from "../lib/format";
import { Button, Meter, Skeleton, TierBadge, cx, scoreColor } from "./ui";

function Md({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm leading-relaxed">
      {text.split("\n").filter((l) => l.trim()).map((line, i) => {
        const bullet = /^\s*[-*•]\s+/.test(line);
        const parts = line.replace(/^\s*[-*•]\s+/, "").replace(/^#+\s*/, "").split(/(\*\*[^*]+\*\*)/g)
          .map((p, j) => (p.startsWith("**") ? <strong key={j}>{p.slice(2, -2)}</strong> : <span key={j}>{p}</span>));
        return bullet ? <div key={i} className="flex gap-2 pl-1"><span className="text-muted">–</span><span>{parts}</span></div>
          : <p key={i} className={i ? "pt-1.5" : ""}>{parts}</p>;
      })}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-dashed border-line py-1.5 text-sm last:border-0">
      <dt className="shrink-0 text-muted">{k}</dt>
      <dd className="num min-w-0 text-right [overflow-wrap:anywhere]">{v ?? "–"}</dd>
    </div>
  );
}

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function FlagList({ flags }: { flags: Flag[] }) {
  if (!flags.length) return <p className="text-sm text-muted">Ingen regelbaserte flagg utløst.</p>;
  const groups = [["kunngjøring", "Fra kunngjøringen"], ["brreg", "Fra Brønnøysundregistrene"]] as const;
  return (
    <div className="space-y-3">
      {groups.map(([src, label]) => {
        const fl = flags.filter((f) => f.source === src);
        if (!fl.length) return null;
        return (
          <div key={src}>
            <div className="mb-1 text-xs text-muted">{label}</div>
            <ul className="space-y-1">
              {fl.map((f) => (
                <li key={f.key} className="rounded-md border border-line px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex items-center gap-2"><span className="text-mid" aria-hidden>▲</span>{f.label}</span>
                    <span className="num shrink-0 text-xs text-muted">vekt {f.weight.toLocaleString("nb-NO")}</span>
                  </div>
                  {f.why && <div className="mt-0.5 pl-5 text-xs text-muted">{f.why}</div>}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function Supplier({ s }: { s: SupplierCard }) {
  const b = s.brreg;
  const status = !b ? "Ikke hentet fra Brreg" : b.kind === "slettet" ? `Slettet ${b.slettedato ?? ""}` : b.konkurs ? "Konkurs"
    : b.under_avvikling || b.tvangsavvikling ? "Under avvikling" : "Aktiv";
  return (
    <div className="rounded-md border border-line p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link to={`/leverandorer/${s.orgnr}`} className="font-medium text-accent hover:underline">{b?.navn ?? s.orgnr}</Link>
        <a href={s.brreg_url} target="_blank" rel="noreferrer" className="font-mono text-xs text-muted hover:underline">{s.orgnr} ↗</a>
      </div>
      {b && (
        <dl className="mt-2">
          <Row k="Status" v={<span className={cx(status !== "Aktiv" && "font-medium text-hi")}>{status}</span>} />
          <Row k="Selskapsform" v={b.orgform} />
          <Row k="Stiftet" v={date(b.stiftet)} />
          <Row k="Ansatte" v={int(b.ansatte)} />
          <Row k="MVA-registrert" v={b.mva == null ? "–" : b.mva ? "Ja" : <span className="text-hi">Nei</span>} />
          <Row k="Bransje (NACE)" v={b.nace ? `${b.nace} ${b.nace_tekst ?? ""}` : "–"} />
          <Row k="Adresse" v={b.adresse || "–"} />
          {s.regnskap?.driftsinntekter != null && <Row k={`Omsetning ${s.regnskap.aar ?? ""}`} v={nok(s.regnskap.driftsinntekter)} />}
          {s.regnskap?.egenkapital != null && <Row k="Egenkapital" v={<span className={cx(s.regnskap.egenkapital < 0 && "text-hi")}>{nok(s.regnskap.egenkapital)}</span>} />}
          {s.owners.length > 0 && <Row k="Største eier" v={`${s.owners[0].name}${s.owners[0].share_pct != null ? ` (${s.owners[0].share_pct.toLocaleString("nb-NO")} %)` : ""}`} />}
        </dl>
      )}
      {s.roles.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-xs text-muted">Roller</div>
          <ul className="text-sm">
            {s.roles.map((r, i) => <li key={i} className="flex justify-between gap-3"><span>{r.navn}</span><span className="text-xs text-muted">{r.rolle_navn}</span></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

const FIELD: Record<string, string> = { note: "notat", assignee: "ansvarlig", vurdering: "vurdering", vurdering_grunn: "begrunnelse" };

function CasePanel({ d, statuses }: { d: ContractDetail; statuses: string[] }) {
  const c = d.case;
  const [note, setNote] = useState(c?.note ?? "");
  const [assignee, setAssignee] = useState(c?.assignee ?? "");
  const [grunn, setGrunn] = useState(c?.vurdering_grunn ?? "");
  const [fileError, setFileError] = useState("");
  useEffect(() => { setNote(c?.note ?? ""); setAssignee(c?.assignee ?? ""); setGrunn(c?.vurdering_grunn ?? ""); },
    [c?.id, c?.note, c?.assignee, c?.vurdering_grunn]);
  const done = () => queryClient.invalidateQueries();
  const create = useMutation({ mutationFn: () => api("/api/cases", { method: "POST", body: JSON.stringify({ contractId: d.id }) }), onSuccess: done });
  const patch = useMutation({
    mutationFn: (body: Record<string, string>) => api(`/api/cases/${c!.id}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  if (!c) {
    return (
      <div className="rounded-md border border-dashed border-line p-4 text-sm">
        <p className="mb-3 text-muted">Ingen sak ennå. Opprett en sak for å sette status, notere og få logg og arbeidspapir.</p>
        <Button onClick={() => create.mutate()} disabled={create.isPending}>Opprett sak</Button>
      </div>
    );
  }
  const dirty = note !== (c.note ?? "") || assignee !== (c.assignee ?? "");
  return (
    <div className="space-y-3 rounded-md border border-line p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs text-muted">Status
          <select value={c.status} onChange={(e) => patch.mutate({ status: e.target.value })}
            className="mt-1 h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-fg">
            {statuses.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">Ansvarlig
          <input value={assignee} onChange={(e) => setAssignee(e.target.value)}
            className="mt-1 h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-fg" />
        </label>
      </div>
      <div className="rounded-md bg-sunk/60 p-2.5">
        <div className="text-xs text-muted">Var flagget verdt å se på? Vurderingen logges og brukes til å forbedre modellene.</div>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {["Relevant", "Ikke relevant"].map((v) => (
            <Button key={v} variant={c.vurdering === v ? undefined : "outline"} disabled={patch.isPending}
              onClick={() => patch.mutate({ vurdering: c.vurdering === v ? "" : v, vurdering_grunn: grunn })}>{v}</Button>
          ))}
        </div>
        <input value={grunn} onChange={(e) => setGrunn(e.target.value)} placeholder="Begrunnelse (valgfritt)"
          onBlur={() => c.vurdering && grunn !== (c.vurdering_grunn ?? "") && patch.mutate({ vurdering_grunn: grunn })}
          className="mt-2 h-9 w-full rounded-md border border-line bg-surface px-2 text-sm text-fg" />
      </div>
      <label className="block text-xs text-muted">Notat
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4}
          className="mt-1 w-full rounded-md border border-line bg-surface p-2 text-sm text-fg" />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => patch.mutate({ note, assignee })} disabled={!dirty || patch.isPending}>Lagre</Button>
        {STATIC
          ? <Button variant="outline" onClick={() => saveWorkpaper(String(c.id)).catch((e) => setFileError(String(e.message)))}>Last ned arbeidspapir</Button>
          : <a className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm hover:bg-sunk"
            href={`/api/cases/${c.id}/arbeidspapir`} target="_blank" rel="noreferrer">Arbeidspapir (utskrift/PDF)</a>}
      </div>
      {fileError && <p className="text-sm text-hi">{fileError}</p>}
      <details className="text-sm">
        <summary className="cursor-pointer text-xs text-muted">Logg ({c.events.length})</summary>
        <ul className="mt-2 space-y-1 text-xs">
          {c.events.map((e, i) => (
            <li key={i} className="flex gap-2"><span className="num text-muted">{dateTime(e.at)}</span>
              <span>{e.actor}: {FIELD[e.field] ?? e.field}{e.field !== "note" && e.new ? ` → ${e.new}` : ""}</span></li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export function CaseFile({ id, onOpen }: { id: string; onOpen?: (id: string) => void }) {
  const { data: d, isLoading } = useApi<ContractDetail>(`/api/contracts/${encodeURIComponent(id)}`);
  const { data: meta } = useApi<Meta>("/api/meta");
  const assess = useMutation({
    mutationFn: () => api(`/api/contracts/${encodeURIComponent(id)}/assess`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [`/api/contracts/${encodeURIComponent(id)}`] }),
  });
  if (isLoading || !d) return <div className="space-y-3 p-5"><Skeleton className="h-6 w-3/4" /><Skeleton className="h-24" /><Skeleton className="h-48" /></div>;
  const ai = d.assessments[0];
  return (
    <article className="space-y-6 p-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="font-mono">{d.id}</span><span>·</span><span>{date(d.pub_date)}</span><TierBadge tier={d.tier} />
          {d.case_status && <span className="rounded bg-accent-soft px-1.5 py-0.5 text-accent">Sak: {d.case_status}</span>}
        </div>
        <h2 className="text-lg font-semibold leading-snug [overflow-wrap:anywhere]">{d.title}</h2>
        <p className="text-sm text-muted">{d.buyer} · {d.region} · {d.sector}</p>
        <div className="flex flex-wrap gap-2 pt-1">
          <Button variant="outline" onClick={() => assess.mutate()} disabled={assess.isPending}>
            {assess.isPending ? "Vurderer …" : ai ? "Ny AI-vurdering" : "AI-vurdering"}
          </Button>
          {d.url && <a href={d.url} target="_blank" rel="noreferrer" className="inline-flex items-center rounded-md px-3 py-1.5 text-sm hover:bg-sunk">Åpne i TED ↗</a>}
        </div>
        {assess.isError && <p className="text-sm text-hi">{String(assess.error.message)}</p>}
      </header>

      <section className="grid grid-cols-[auto_1fr] items-center gap-4 rounded-md border border-line p-4">
        <div className="num text-4xl font-bold" style={{ color: scoreColor(d.priority) }}>{Math.round(d.priority)}</div>
        <div>
          <div className="text-sm font-medium">Prioritet</div>
          <Meter value={d.priority} className="my-1.5" />
          <p className="text-xs text-muted">Blant de {pct(Math.max(0.1, 100 - d.percentile))} høyeste. Datadekning {d.data_coverage} av 4 nøkkelfelt.</p>
        </div>
        {d.klagerisiko != null && (
          <p className="col-span-2 text-xs text-muted">
            <b className="font-medium">Klagerisiko (eksperimentell): {Math.round(d.klagerisiko)}</b>. Høyere klagerisiko enn {Math.round(d.klagerisiko)} % av kontraktene,
            ut fra verdi, oppdragsgivertype og type anskaffelse. Måler risiko for KOFA-klage, ikke korrupsjon, og teller ikke i prioriteten.
          </p>)}
        <p className="col-span-2 text-xs text-muted">Røde flagg (CRI) {Math.round(d.cri)}. Prioriteten bygger på de røde flaggene og registerflaggene.</p>
      </section>

      {ai && (
        <section className="rounded-md border border-accent/30 bg-accent-soft/60 p-4">
          <div className="mb-2 text-xs font-medium text-accent">AI-vurdering · beslutningsstøtte, ikke konklusjon · {ai.provider}/{ai.model} · {dateTime(ai.at)}</div>
          <Md text={ai.text} />
        </section>
      )}

      <Section title="Røde flagg"><FlagList flags={d.flags} /></Section>

      <Section title="Sak">{meta && <CasePanel d={d} statuses={meta.case_statuses} />}</Section>

      {d.suppliers.length > 0 && (
        <Section title="Leverandør"><div className="space-y-2">{d.suppliers.map((s) => <Supplier key={s.orgnr} s={s} />)}</div></Section>
      )}


      <Section title="Nøkkeldata">
        <dl>
          <Row k="Prosedyre" v={d.method + (d.accelerated ? " (akselerert)" : "")} />
          {d.justification && <Row k="Begrunnelse" v={d.justification} />}
          <Row k="Antall tilbud" v={int(d.num_bidders)} />
          <Row k="Estimert verdi" v={nok(d.est_value)} />
          <Row k="Tildelt verdi" v={nok(d.award_value)} />
          <Row k="Tildelt mot estimat" v={signedPct(d.award_over_estimate_pct)} />
          <Row k="Varighet" v={d.contract_months ? `${Math.round(d.contract_months)} mnd` : "–"} />
          <Row k="Rammeavtale" v={d.framework ? "Ja (verdi er ofte maksramme)" : "Nei"} />
          <Row k="Tidligere tildelinger, samme par" v={int(d.repeat_pair_count)} />
          <Row k="Vinners andel i CPV-gruppen" v={pct(d.winner_market_share_pct)} />
          <Row k="CPV" v={`${d.cpv ?? "–"} · ${d.nature ?? ""}`} />
        </dl>
      </Section>

      {d.einnsyn != null && (
        <Section title="Postjournal hos oppdragsgiveren (eInnsyn)">
          {d.einnsyn.length === 0 ? (
            <p className="text-sm text-muted">Ingen journalposter med leverandøren som avsender eller mottaker. Mange kommuner publiserer ikke i eInnsyn, så det betyr ikke at dokumentene mangler.</p>
          ) : (
            <ul className="divide-y divide-line rounded-md border border-line">
              {d.einnsyn.map((j, i) => (
                <li key={i} className="px-3 py-2 text-sm">
                  <div className="flex justify-between gap-3">
                    {j.url ? <a href={j.url} target="_blank" rel="noreferrer" className="min-w-0 truncate text-accent hover:underline">{j.title}</a>
                      : <span className="min-w-0 truncate">{j.title}</span>}
                    <span className="num shrink-0 text-xs text-muted">{j.date ? date(j.date) : ""}</span>
                  </div>
                  {j.case && <div className="truncate text-xs text-muted">Sak: {j.case}</div>}
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {d.sameSupplier.length > 0 && (
        <Section title="Andre tildelinger, samme oppdragsgiver og leverandør">
          <ul className="divide-y divide-line rounded-md border border-line">
            {d.sameSupplier.map((s) => (
              <li key={s.id}>
                <button className="flex w-full justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-sunk" onClick={() => onOpen?.(s.id)}>
                  <span className="truncate">{s.title}</span><span className="num shrink-0 text-xs text-muted">{date(s.pub_date)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </article>
  );
}
