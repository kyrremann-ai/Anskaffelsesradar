import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { STATIC, api, queryClient, useApi, type ContractDetail, type DismissalReason, type Flag, type FlagCheck, type Meta, type SupplierCard } from "../lib/api";
import { canAssess, saveWorkpaper } from "../lib/static";
import { date, dateTime, int, isoDatesInText, nok, pct, signedPct } from "../lib/format";
import { Button, Icon, KonkursCheck, ScoreStrip, Skeleton, SOURCE_COLOR, SOURCE_LABEL, TierBadge, cx, sourceKey, tierColor } from "./ui";

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
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

// Revisor avskriver et flagg som falsk positiv, med årsakskode. Logges i saken og teller ikke i prioriteten.
// Ikke i den statiske versjonen (ingen server å lagre på).
function Dismiss({ d, f }: { d: ContractDetail; f: Flag }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [scope, setScope] = useState<"kontrakt" | "leverandor">("kontrakt");
  const reasons = useApi<DismissalReason[]>("/api/dismissal-reasons", {}, { enabled: open });
  const done = () => queryClient.invalidateQueries();
  const save = useMutation({
    mutationFn: () => api(`/api/contracts/${encodeURIComponent(d.id)}/flags/${encodeURIComponent(f.key)}/dismiss`,
      { method: "POST", body: JSON.stringify({ reason, note, scope }) }),
    onSuccess: () => { setOpen(false); done(); },
  });
  const undo = useMutation({ mutationFn: (id: number) => api(`/api/dismissals/${id}`, { method: "DELETE" }), onSuccess: done });
  const r = reasons.data?.find((x) => x.code === reason);
  const label = Object.fromEntries((reasons.data ?? []).map((x) => [x.code, x.label]));

  if (f.dismissed) {
    const x = f.dismissed;
    return (
      <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-2 text-xs text-muted">
        <span>Avskrevet {date(x.at)} av {x.actor}{x.scope === "leverandor" ? " for leverandøren" : ""}{x.note ? `: ${x.note}` : ""}</span>
        <button className="underline underline-offset-2 hover:text-fg" disabled={undo.isPending} onClick={() => undo.mutate(x.id)}>Angre</button>
      </div>
    );
  }
  if (!open) return <button className="mt-1 text-xs text-muted underline decoration-line underline-offset-2 transition-colors duration-150 hover:text-fg hover:decoration-current" onClick={() => setOpen(true)}>Avskriv flagg</button>;
  return (
    <div className="mt-2 space-y-2 rounded-md bg-sunk p-3 text-xs">
      <label className="block">
        <span className="text-muted">Årsak</span>
        <select className="mt-0.5 block w-full rounded-md border border-line bg-surface px-2 py-1 text-sm" value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">Velg årsak</option>
          {reasons.data?.map((x) => <option key={x.code} value={x.code}>{x.label}</option>)}
        </select>
      </label>
      {r?.supplier_scope && d.winner_ids.length === 1 && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={scope === "leverandor"} onChange={(e) => setScope(e.target.checked ? "leverandor" : "kontrakt")} />
          Gjelder leverandøren i alle kontrakter
        </label>
      )}
      <label className="block">
        <span className="text-muted">Begrunnelse{reason === "annet" ? " (påkrevd)" : ""}</span>
        <textarea className="mt-0.5 block w-full rounded-md border border-line bg-surface px-2 py-1 text-sm" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {save.error && <p className="text-hi">{String((save.error as Error).message)}</p>}
      <div className="flex gap-2">
        <Button disabled={!reason || (reason === "annet" && !note.trim()) || save.isPending} onClick={() => save.mutate()}>Avskriv</Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>Avbryt</Button>
      </div>
      {label[reason] && <p className="text-muted">Flagget trekkes fra prioriteten og føres i saksloggen.</p>}
    </div>
  );
}

function FlagList({ flags, d }: { flags: Flag[]; d?: ContractDetail }) {
  if (!flags.length) return <p className="text-sm text-muted">Ingen regelbaserte flagg utløst.</p>;
  // Flaggene er observasjoner i kildene, ikke konklusjoner. Datakvalitet teller ikke i prioriteten.
  const groups: [string, string, (f: Flag) => boolean][] = [
    ["kunngjøring", "Fra kunngjøringen", (f) => f.source === "kunngjøring"],
    ["brreg", "Fra Brønnøysundregistrene", (f) => f.source === "brreg"],
    ["andre", "Fra andre registre", (f) => !["kunngjøring", "brreg", "datakvalitet"].includes(f.source)],
    ["datakvalitet", "Datakvalitet (teller ikke i prioriteten)", (f) => f.source === "datakvalitet"],
  ];
  return (
    <div className="space-y-3">
      {groups.map(([key, label, match]) => {
        const fl = flags.filter(match);
        if (!fl.length) return null;
        const dq = key === "datakvalitet";
        return (
          <div key={key}>
            <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted">
              {!dq && <span className="h-2 w-2 rounded-full" style={{ background: SOURCE_COLOR[sourceKey(key === "andre" ? "andre" : key)] }} />}{label}</div>
            <ul className="divide-y divide-line border-y border-line">
              {fl.map((f) => (
                <li key={f.key} className={cx("py-2.5 text-sm", dq && "text-muted")}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className={cx("font-medium", (f.dismissed || dq) && "font-normal text-muted", f.dismissed && "line-through decoration-[color-mix(in_srgb,var(--muted)_60%,transparent)]")}>{f.label}</span>
                    {!dq && <span className="num shrink-0 text-xs text-muted" title={`Vekt ${f.weight.toLocaleString("nb-NO")}`}>
                      {f.bidrag != null ? `+${f.bidrag.toLocaleString("nb-NO")} poeng` : `vekt ${f.weight.toLocaleString("nb-NO")}`}</span>}
                  </div>
                  {f.why && <div className="mt-0.5 text-xs text-muted">{isoDatesInText(f.why)}</div>}
                  {f.data_note && <div className="mt-0.5 text-xs text-mid">{f.data_note}</div>}
                  {f.checks?.length ? <Checks checks={f.checks} /> : null}
                  {d && !STATIC && !dq && <Dismiss d={d} f={f} />}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

// Svaret på hver alternative forklaring som ord, ikke tegn
const CHECK_MARK: Record<FlagCheck["status"], [string, string]> = {
  ok: ["Sjekket", "bg-lo-soft text-lo"], justert: ["Justert", "bg-mid-soft text-mid"], forklarer: ["Forklart", "bg-sunk text-muted"],
  svekker: ["Halv vekt", "bg-mid-soft text-mid"], ukjent: ["Data mangler", "border border-line text-muted"],
};

// Alternative forklaringer som er sjekket for leverandørflagget, med svaret dataene gir
function Checks({ checks }: { checks: FlagCheck[] }) {
  return (
    <details className="mt-1 text-xs text-muted">
      <summary className="cursor-pointer hover:text-fg">Alternative forklaringer ({checks.length})</summary>
      <ul className="mt-1.5 space-y-1">
        {checks.map((c, i) => (
          <li key={i} className="flex items-baseline gap-2">
            <span className={cx("shrink-0 rounded px-1.5 py-px text-[11px] font-medium", CHECK_MARK[c.status][1])}>{CHECK_MARK[c.status][0]}</span>
            <span><span className="text-fg">{c.q}</span> {c.a}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Supplier({ s }: { s: SupplierCard }) {
  const b = s.brreg;
  const status = !b ? "Ikke hentet fra Brreg" : b.kind === "slettet" ? `Slettet ${b.slettedato ?? ""}` : b.konkurs ? "Konkurs"
    : b.under_avvikling || b.tvangsavvikling ? "Under avvikling" : "Aktiv";
  return (
    <div className="rounded-lg bg-sunk p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link to={`/leverandorer/${s.orgnr}`} className="font-medium text-accent hover:underline">{b?.navn ?? s.orgnr}</Link>
        <a href={s.brreg_url} target="_blank" rel="noreferrer" className="num inline-flex items-center gap-1 text-xs text-muted hover:text-fg hover:underline">{s.orgnr}<Icon name="external" className="h-3 w-3" /></a>
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
      {s.roles.some((r) => r.type === "person") && <KonkursCheck links={s.konkurs_links} url={s.karantene_url} compact />}
    </div>
  );
}

const FIELD: Record<string, string> = { note: "notat", assignee: "ansvarlig", vurdering: "vurdering", vurdering_grunn: "begrunnelse" };

// Ulagret notat og ansvarlig per kontrakt. Overlever bytte av sak, og en lagring fra en kollega
// overskriver ikke det revisor holder på å skrive.
const drafts = new Map<string, { note: string; assignee: string }>();

function CasePanel({ d, statuses }: { d: ContractDetail; statuses: string[] }) {
  const c = d.case;
  const server = { note: c?.note ?? "", assignee: c?.assignee ?? "" };
  const [note, setNoteState] = useState(drafts.get(d.id)?.note ?? server.note);
  const [assignee, setAssigneeState] = useState(drafts.get(d.id)?.assignee ?? server.assignee);
  const [grunn, setGrunn] = useState(c?.vurdering_grunn ?? "");
  const [fileError, setFileError] = useState("");
  const draft = (patch: Partial<{ note: string; assignee: string }>) => {
    const next = { note, assignee, ...patch };
    if (next.note === server.note && next.assignee === server.assignee) drafts.delete(d.id); else drafts.set(d.id, next);
  };
  const setNote = (v: string) => { setNoteState(v); draft({ note: v }); };
  const setAssignee = (v: string) => { setAssigneeState(v); draft({ assignee: v }); };
  // Ny sak eller ny versjon fra serveren: vis kladden hvis det finnes en, ellers det som er lagret
  useEffect(() => {
    const k = drafts.get(d.id);
    setNoteState(k?.note ?? server.note);
    setAssigneeState(k?.assignee ?? server.assignee);
    setGrunn(c?.vurdering_grunn ?? "");
  }, [d.id, c?.id, c?.note, c?.assignee, c?.vurdering_grunn]); // eslint-disable-line react-hooks/exhaustive-deps
  const unsaved = !!c && (note !== server.note || assignee !== server.assignee);
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  const done = () => queryClient.invalidateQueries();
  const create = useMutation({ mutationFn: () => api("/api/cases", { method: "POST", body: JSON.stringify({ contractId: d.id }) }), onSuccess: done });
  const patch = useMutation({
    mutationFn: (body: Record<string, string>) => api(`/api/cases/${c!.id}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  if (!c) {
    return (
      <div className="rounded-lg bg-sunk p-4 text-sm">
        <p className="mb-3 text-muted">Ingen sak ennå. Opprett en sak for å sette status, notere og få logg og arbeidspapir.</p>
        <Button onClick={() => create.mutate()} disabled={create.isPending}>Opprett sak</Button>
      </div>
    );
  }
  const dirty = note !== (c.note ?? "") || assignee !== (c.assignee ?? "");
  return (
    <div className="space-y-3">
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
      <div className="rounded-lg bg-sunk p-3">
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
        <Button onClick={() => patch.mutate({ note, assignee }, { onSuccess: () => drafts.delete(d.id) })} disabled={!dirty || patch.isPending}>Lagre</Button>
        {dirty && <span className="self-center text-xs text-hi">Ulagrede endringer</span>}
        {STATIC
          ? <Button variant="outline" onClick={() => saveWorkpaper(String(c.id)).catch((e) => setFileError(String(e.message)))}>Last ned arbeidspapir</Button>
          : <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-sm font-medium transition-colors duration-150 hover:border-field hover:bg-sunk"
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

// Summen per kilde under poengstripen: hvor mye av prioriteten kommer fra kunngjøringen, Brreg og andre registre
function SourceSums({ flags }: { flags: Flag[] }) {
  const sums: Record<string, number> = {};
  for (const f of flags) if (f.source !== "datakvalitet" && !f.dismissed && f.bidrag) sums[sourceKey(f.source)] = (sums[sourceKey(f.source)] ?? 0) + f.bidrag;
  const n = flags.filter((f) => f.dismissed).length;
  if (!Object.keys(sums).length && !n) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {(Object.keys(SOURCE_LABEL) as (keyof typeof SOURCE_COLOR)[]).filter((k) => sums[k]).map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: SOURCE_COLOR[k] }} />
          {SOURCE_LABEL[k]} <span className="num font-medium text-fg">+{sums[k].toLocaleString("nb-NO", { maximumFractionDigits: 1 })}</span></span>
      ))}
      {n > 0 && <span className="inline-flex items-center gap-1.5"><span className="strip-dismissed h-2 w-3 rounded-sm" />{n} avskrevet</span>}
    </div>
  );
}

export function CaseFile({ id, onOpen }: { id: string; onOpen?: (id: string) => void }) {
  const { data: d, isLoading } = useApi<ContractDetail>(`/api/contracts/${encodeURIComponent(id)}`);
  const { data: meta } = useApi<Meta>("/api/meta");
  // Cloudflare-versjonen kan ikke be Claude om nye vurderinger; der vises bare nattens vurderinger
  const [live, setLive] = useState(!STATIC);
  useEffect(() => { if (STATIC) canAssess.then(setLive); }, []);
  const assess = useMutation({
    mutationFn: () => api(`/api/contracts/${encodeURIComponent(id)}/assess`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [`/api/contracts/${encodeURIComponent(id)}`] }),
  });
  if (isLoading || !d) return <div className="space-y-3 p-5"><Skeleton className="h-6 w-3/4" /><Skeleton className="h-24" /><Skeleton className="h-48" /></div>;
  const ai = d.assessments[0];
  const nightly = ai?.kind === "nattlig";
  return (
    <article className="space-y-7 p-5 sm:p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="num">{d.id}</span><span aria-hidden>·</span><span>{date(d.pub_date)}</span><TierBadge tier={d.tier} />
          {d.case_status && <span className="inline-flex items-center gap-1 rounded bg-accent-soft px-1.5 py-px font-medium text-accent"><Icon name="flag" className="h-3 w-3" />Sak: {d.case_status}</span>}
        </div>
        <h2 className="text-xl font-semibold leading-snug tracking-[-0.01em] [overflow-wrap:anywhere] [text-wrap:balance]">{d.title}</h2>
        <p className="text-sm text-muted">{d.buyer} · {d.region} · {d.sector}</p>
        <div className="flex flex-wrap gap-2 pt-1">
          {live && <Button variant="outline" onClick={() => assess.mutate()} disabled={assess.isPending}>
            {assess.isPending ? "Vurderer …" : ai ? "Ny AI-vurdering" : "AI-vurdering"}
          </Button>}
          {d.url && <a href={d.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium text-accent transition-colors duration-150 hover:bg-sunk">Åpne i {d.source === "doffin" ? "Doffin" : "TED"}<Icon name="external" className="h-3.5 w-3.5" /></a>}
        </div>
        {assess.isError && <p className="text-sm text-hi">{String(assess.error.message)}</p>}
      </header>

      {(d.regler ?? []).length > 0 && (
        <section className="rounded-lg bg-hi-soft p-4">
          <h3 className="text-sm font-semibold text-hi">Faste kontrollpunkter</h3>
          <p className="text-xs text-[color-mix(in_srgb,var(--fg)_75%,transparent)]">Slår ut uansett hvordan resten av datasettet ser ut. Må kontrolleres mot originalkunngjøringen.</p>
          <ul className="mt-2.5 space-y-1.5 text-sm">{d.regler.map((x) => (
            <li key={x.key} className="flex gap-2"><Icon name="rule" className="mt-0.5 h-3.5 w-3.5 text-hi" />
              <span><span className="font-medium">{x.label}</span>{x.why && <span className="block text-xs text-[color-mix(in_srgb,var(--fg)_75%,transparent)]">{isoDatesInText(x.why)}</span>}</span></li>))}
          </ul>
        </section>)}

      <section className="rounded-lg bg-sunk p-4">
        <div className="flex items-end gap-3">
          <div className="num text-[44px] font-semibold leading-none tracking-[-0.02em]" style={{ color: tierColor(d.tier) }}>{Math.round(d.priority)}</div>
          <div className="pb-1 text-sm"><span className="font-semibold">Prioritet</span> <span className="text-muted">av 100</span></div>
        </div>
        <ScoreStrip flags={d.flags} priority={d.priority} size="lg" className="mt-3 bg-surface" />
        <SourceSums flags={d.flags} />
        <div className="mt-3 grid gap-1.5">
          <p className="text-xs text-muted">{d.tier === "Intensjon"
            ? "Intensjonskunngjøringer er ikke kontrakter og rangeres ikke. Poengsummen viser bare hvilke observasjoner som slår ut."
            : <>Relativ rangering: blant de {pct(Math.max(0.1, 100 - d.percentile))} høyeste nasjonalt. Ikke en sannsynlighet for regelbrudd.</>} Datadekning {d.data_coverage} av 4 nøkkelfelt
            {d.data_coverage < 4 && <> (mangler {[d.num_bidders == null && "tilbudstall", d.est_value == null && "estimert verdi",
              d.award_value == null && "tildelt verdi", d.contract_months == null && "varighet"].filter(Boolean).join(", ")})</>}.
            {" "}Flagg som ikke kan vurderes uten disse feltene, slår ikke ut.</p>
          {d.peer_group && d.peer_percentile != null && (
            <p className="text-xs text-muted" title="Samme poengsum rangert bare mot kontrakter fra samme type oppdragsgiver og sektor">
              Innen sammenligningsgruppen <b className="font-medium">{d.peer_group}</b>: {d.peer_tier.toLowerCase()} prioritet,
              blant de {pct(Math.max(0.1, 100 - d.peer_percentile))} høyeste.</p>)}
        {d.klagerisiko != null && (
          <p className="text-xs text-muted">
            <b className="font-medium">Klagerisiko (eksperimentell): persentil {Math.round(d.klagerisiko)}</b>. Rangert over {Math.round(d.klagerisiko)} % av kontraktene i radaren
            på trekk som har fulgt KOFA-klager (verdi, oppdragsgivertype, type anskaffelse). En rangering, ikke en sannsynlighet. Måler klagerisiko, ikke korrupsjon, og teller ikke i prioriteten.
          </p>)}
        <p className="text-xs text-muted">Røde flagg (CRI) {Math.round(d.cri)}. Prioriteten bygger på de røde flaggene og registerflaggene.</p>
        </div>
      </section>

      {ai && (
        <section className="rounded-lg bg-accent-soft p-4">
          <div className="mb-2 text-xs font-medium text-accent">AI-vurdering · beslutningsstøtte, ikke konklusjon · {ai.provider}/{ai.model} · {nightly ? `laget ${date(ai.at)}` : dateTime(ai.at)}</div>
          <Md text={ai.text} />
        </section>
      )}
      {!ai && !live && (
        <p className="text-xs text-muted">AI-vurdering lages bare for saker med høy prioritet.</p>
      )}

      <Section title="Røde flagg"><FlagList flags={d.flags} d={d} /></Section>

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
          <Row k="Tildelt verdi" v={<>{nok(d.award_value)}{d.value_note && <span className="block text-xs text-mid">{d.value_note}</span>}</>} />
          <Row k="Tildelt mot estimat" v={signedPct(d.award_over_estimate_pct)} />
          <Row k="Tilbudsfrist" v={d.tender_duration_days != null ? `${Math.round(d.tender_duration_days)} dager (fra ${d.cn_ref})` : "–"} />
          <Row k="Varighet" v={d.contract_months ? `${Math.round(d.contract_months)} mnd` : "–"} />
          <Row k="Rammeavtale" v={d.framework ? "Ja (verdi er ofte maksramme)" : "Nei"} />
          <Row k="Tidligere tildelinger, samme par" v={int(d.repeat_pair_count)} />
          <Row k="Vinners andel i CPV-gruppen" v={pct(d.winner_market_share_pct)} />
          <Row k="CPV" v={`${d.cpv ?? "–"} · ${d.nature ?? ""}`} />
          <Row k="Kunngjøring" v={`${d.source === "doffin" ? "Doffin" : "TED"} ${d.id.replace(/^doffin-/, "")}`} />
          {d.procedure_id && d.procedure_id !== d.id && <Row k="Anskaffelsesforløp (prosedyre-ID)" v={<span className="font-mono text-xs">{d.procedure_id}</span>} />}
          {(d.also ?? []).length > 0 && <Row k="Samme tildeling også kunngjort som" v={<>{d.also!.map((a) => (
            <span key={a.id} className="block" title={a.rule}>{a.url ? <a href={a.url} target="_blank" rel="noreferrer" className="underline">
              {a.source === "doffin" ? "Doffin" : "TED"} {a.id.replace(/^doffin-/, "")}</a> : a.id}</span>))}
            <span className="block text-xs text-muted">Telles som én kontrakt</span></>} />}
        </dl>
      </Section>

      {d.einnsyn != null && (
        <Section title="Postjournal hos oppdragsgiveren (eInnsyn)">
          {d.einnsyn.length === 0 ? (
            <p className="text-sm text-muted">Ingen journalposter med leverandøren som avsender eller mottaker. Mange kommuner publiserer ikke i eInnsyn, så det betyr ikke at dokumentene mangler.</p>
          ) : (
            <ul className="divide-y divide-line border-y border-line">
              {d.einnsyn.map((j, i) => (
                <li key={i} className="py-2 text-sm">
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
          <ul className="divide-y divide-line border-y border-line">
            {d.sameSupplier.map((s) => (
              <li key={s.id}>
                <button className="-mx-2 flex w-[calc(100%+1rem)] justify-between gap-3 rounded-md px-2 py-2 text-left text-sm transition-colors duration-150 hover:bg-sunk" onClick={() => onOpen?.(s.id)}>
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
