// Statisk modus (VITE_STATIC=1): klienten kjører som artifact på claude.ai uten egen server.
// Kontraktsdata leses fra contracts.json/cards.json/meta.json som publiseres sammen med siden
// (laget av `python -m radar.export_static`). Oversikt, filtre og lister regnes ut her.
// Saker lagres i artifactens database (db-kapabiliteten), så de deles mellom brukerne.
import type { Case, CaseEvent, ContractRow, Group, InvoiceResult, Stats, SupplierCard } from "./api";

type Row = ContractRow & Record<string, unknown> & { winner: string | null };
type Card = SupplierCard & { links: { orgnr: string; name: string; via: string; rolle: string; is_buyer: boolean }[] };
type StoredCase = Omit<Case, "id" | "events"> & { events: (CaseEvent & { actor_id?: string | null })[] };

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global { interface Window { claude?: { use(name: string): Promise<any> } } }

class HttpError extends Error {}

let data: Promise<{ rows: Row[]; cards: Record<string, Card>; meta: any; model: any; llmSystem?: string; invoices: InvoiceResult }> | null = null;
const getJson = (f: string) => fetch(f).then((r) => {
  if (!r.ok) throw new HttpError(`Fant ikke ${f}`);
  return r.json();
});

// Dataene er delt i flere filer (grense per fil i artifacten); meta.json lister dem
function load() {
  data ??= getJson("meta.json").then(async (m) => {
    const parts: { contracts: string[]; cards: string[] } = m.files ?? { contracts: ["contracts.json"], cards: ["cards.json"] };
    const [rows, cards] = await Promise.all([
      Promise.all(parts.contracts.map(getJson)).then((xs) => xs.flat()),
      Promise.all(parts.cards.map(getJson)).then((xs) => Object.assign({}, ...xs)),
    ]);
    return { rows, cards, meta: m.meta, model: m.model, llmSystem: m.llm_system as string | undefined,
      invoices: (m.invoices ?? { rows: [], period: null, radar_from: null }) as InvoiceResult };
  });
  return data;
}

// ------------------------------------------------------------------ AI-vurdering (Claude via artifacten)

// Demo: Claude svarer på brukerens egen Claude-konto. Serverversjonen bruker Mistral (EU) eller Claude via API.
const AI_MODEL = "claude-sonnet-5-5";

// Samme felt som serverens build_input (llm.py). Ingen personnavn.
function aiInput(c: Row) {
  return {
    Tittel: c.title, Oppdragsgiver: c.buyer, Publisert: c.pub_date, Prosedyre: c.method,
    "Begrunnelse for prosedyre": c.justification ?? null, "Antall tilbud": c.num_bidders,
    "Estimert verdi (NOK)": c.est_value, "Tildelt verdi (NOK)": c.award_value,
    "Tildelt mot estimat (%)": c.award_over_estimate_pct, Rammeavtale: !!c.framework,
    "Varighet (mnd)": c.contract_months ?? null, Leverandør: (c.winner_names ?? []).join(", "),
    "Tidligere tildelinger samme par": c.repeat_pair_count ?? null, CPV: c.cpv ?? null,
    Prioritet: c.priority != null ? Math.round(c.priority) : null, Prioritetsnivå: c.tier,
    "Røde flagg": (c.flags ?? []).map((f) => f.label),
  };
}

async function assess(r: Row, system?: string) {
  const sample = await window.claude?.use("sample").catch(() => null);
  if (!sample || !system) throw new HttpError("AI-vurdering er ikke tilgjengelig i denne visningen.");
  let res;
  try {
    res = await sample(`${system}\n\nKontrakt:\n${JSON.stringify(aiInput(r))}`, { model: AI_MODEL, modelTier: "default", cache: false });
  } catch (e: any) {
    if (e?.code === "not_granted") throw new HttpError("Du må tillate at siden bruker Claude for å få en AI-vurdering.");
    if (e?.code === "rate_limited") throw new HttpError("For mange forespørsler akkurat nå. Prøv igjen om litt.");
    throw new HttpError(`AI-vurdering feilet: ${e?.message ?? e}`);
  }
  const a = { at: new Date().toISOString(), provider: "claude", model: res.modelApplied ?? AI_MODEL, text: res.text, ...(await actor()) };
  // Huskes lokalt uansett, i tilfelle databasen ikke tar imot vurderinger (f.eks. Cloudflare-versjonen)
  localAssessments.set(keyOf(r.id), a);
  const d = await db();
  try { await d?.doc(`assessments/${keyOf(r.id)}`).set(a); } catch { /* lokalt holder */ }
  return a;
}

const localAssessments = new Map<string, any>();
async function assessments(key: string) {
  const d = await db();
  let a = null;
  try { a = d ? await d.doc(`assessments/${key}`).get().then((s: any) => (s.exists ? s.data() : null)) : null; } catch { /* ingen get() */ }
  a ??= localAssessments.get(key);
  return a ? [{ id: 1, ...a }] : [];
}

// ------------------------------------------------------------------ saker (db)

const cases = new Map<string, StoredCase>();
let dbReady: Promise<any | null> | null = null;
let onChange: () => void = () => {};
export function onCasesChange(fn: () => void) { onChange = fn; }

const keyOf = (cid: string) => cid.replace(/[^A-Za-z0-9_-]/g, "_");

function db(): Promise<any | null> {
  dbReady ??= (async () => {
    const d = await window.claude?.use("db").catch(() => null);
    if (!d) return null;
    await new Promise<void>((resolve) => {
      let first = true;
      d.collection("cases").onSnapshot((snap: any) => {
        cases.clear();
        for (const doc of snap.docs) cases.set(doc.id, doc.data());
        if (first) { first = false; resolve(); } else onChange();
      }, () => { if (first) { first = false; resolve(); } });
    });
    return d;
  })();
  return dbReady;
}

let userNs: Promise<any | null> | null = null;
const user = () => (userNs ??= window.claude?.use("user").catch(() => null) ?? Promise.resolve(null));

async function caseOut(key: string): Promise<Case | null> {
  const c = cases.get(key);
  if (!c) return null;
  const u = await user();
  const ids = [...new Set(c.events.map((e) => e.actor_id).filter(Boolean))] as string[];
  const names: Record<string, { name: string }> = u && ids.length ? await u.profiles(ids).catch(() => ({})) : {};
  const events = [...c.events].reverse().map((e) => ({ ...e, actor: (e.actor_id && names[e.actor_id]?.name) || e.actor || "Ukjent" }));
  return { ...c, id: key as unknown as number, events };
}

async function actor() {
  const u = await user();
  const me = u ? await u.me().catch(() => null) : null;
  return { actor_id: (u && (await u.id().catch(() => null))) ?? null, actor: me?.name || "Ukjent" };
}

async function saveCase(key: string, c: StoredCase) {
  cases.set(key, c);
  const d = await db();
  if (d) await d.doc(`cases/${key}`).set(c as unknown as Record<string, unknown>);
}

// ------------------------------------------------------------------ beregninger (som api.py)

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 1000) / 10 : null);
const sum = (xs: (number | null | undefined)[]) => xs.reduce<number>((s, x) => s + (x ?? 0), 0);

function stats(d: Row[]): Stats {
  const known = d.filter((r) => r.num_bidders != null);
  const high = d.filter((r) => r.tier === "Høy").length;
  return {
    n: d.length, n_high: high, n_elevated: d.filter((r) => r.tier === "Forhøyet").length,
    single_bid_pct: pct(known.filter((r) => r.num_bidders === 1).length, known.length),
    direct_pct: pct(d.filter((r) => r.method === "Direkte tildeling").length, d.length),
    total_value: sum(d.map((r) => r.award_value)),
    avg_priority: d.length ? Math.round((sum(d.map((r) => r.priority)) / d.length) * 10) / 10 : null,
    coverage_pct: pct(d.filter((r) => r.est_value != null && r.award_value != null).length, d.length),
  };
}

function groupBy<T>(xs: T[], key: (x: T) => string) {
  const m = new Map<string, T[]>();
  for (const x of xs) { const k = key(x); m.set(k, [...(m.get(k) ?? []), x]); }
  return m;
}

function group(d: Row[], col: "sector" | "region" | "buyer_id", minN = 1, nameCol?: "buyer"): Group[] {
  const out: (Group & { high_share: number })[] = [];
  for (const [k, g] of groupBy(d, (r) => String(r[col] ?? ""))) {
    if (g.length < minN) continue;
    const st = stats(g);
    out.push({ name: nameCol ? g[0][nameCol] : k, buyer_id: nameCol ? k : null, n: g.length, avg_score: st.avg_priority ?? 0,
      single_bid_pct: st.single_bid_pct, direct_pct: st.direct_pct, n_flagged: st.n_high + st.n_elevated, n_high: st.n_high,
      total_value: st.total_value, high_share: st.n_high / g.length });
  }
  return out.sort((a, b) => b.n - a.n);
}

const caseStatus = (r: Row) => cases.get(keyOf(r.id))?.status ?? null;
const scope = (rows: Row[], buyers: string | null) => (buyers ? rows.filter((r) => buyers.split(",").includes(r.buyer_id)) : rows);

function summary(rows: Row[], buyers: string | null) {
  // Tellingene gjelder kontrakter; intensjonskunngjøringer telles for seg
  const all = rows.filter((r) => r.notice_type !== "intensjon");
  const intents = scope(rows, buyers).filter((r) => r.notice_type === "intensjon");
  const d = scope(all, buyers);
  const months = groupBy(d, (r) => (r.pub_date ?? "").slice(0, 7));
  const counts = new Map<string, { key: string; label: string; n: number }>();
  for (const r of d) for (const f of r.flags) {
    const c = counts.get(f.key) ?? { key: f.key, label: f.label.split(" (")[0], n: 0 };
    c.n++; counts.set(f.key, c);
  }
  const caseCounts: Record<string, number> = {};
  for (const r of d) { const s = caseStatus(r); if (s) caseCounts[s] = (caseCounts[s] ?? 0) + 1; }
  const dates = d.map((r) => r.pub_date).filter(Boolean).sort();
  return {
    ...stats(d), national: buyers ? stats(all) : null, n_buyers: new Set(d.map((r) => r.buyer_id)).size,
    period: [dates[0] ?? null, dates[dates.length - 1] ?? null],
    monthly: [...months.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([month, g]) => ({
      month, n: g.length, flagged: g.filter((r) => r.tier !== "Lav").length, direct: g.filter((r) => r.method === "Direkte tildeling").length })),
    flag_counts: [...counts.values()].sort((a, b) => b.n - a.n),
    by_sector: group(d, "sector"), by_region: group(d, "region"), by_buyer: group(d, "buyer_id", buyers ? 1 : 8, "buyer"),
    cases: caseCounts, n_intents: intents.length,
    regler: [...[...d, ...intents].flatMap((r) => r.regler ?? []).reduce((m, x) => {
      const c = m.get(x.key) ?? { key: x.key, label: x.label, n: 0 }; c.n++; return m.set(x.key, c);
    }, new Map<string, { key: string; label: string; n: number }>()).values()],
  };
}

function filter(all: Row[], p: URLSearchParams) {
  let d = scope(all, p.get("buyers"));
  const q = (p.get("q") ?? "").toLowerCase();
  if (q) d = d.filter((r) => `${r.title} ${r.buyer} ${r.winner_names.join(" ")}`.toLowerCase().includes(q));
  // rank=gruppe: prioritetsnivå innen sammenligningsgruppen i stedet for nasjonalt
  const group = p.get("rank") === "gruppe";
  for (const k of ["tier", "method", "sector", "region"] as const) {
    const v = p.get(k), col = k === "tier" && group ? "peer_tier" : k;
    if (v) d = d.filter((r) => r[col] === v);
  }
  // flag=regel:<nøkkel> gir faste kontrollpunkter, flag=type:<kunngjøringstype> gir f.eks. intensjoner
  const flag = p.get("flag") ?? "";
  if (flag.startsWith("regel:")) d = d.filter((r) => (r.regler ?? []).some((x) => x.key === flag.slice(6)));
  else if (flag.startsWith("type:")) d = d.filter((r) => r.notice_type === flag.slice(5));
  else if (flag) d = d.filter((r) => r.flags.some((f) => f.key === flag));
  if (!flag.startsWith("type:") && !flag.startsWith("regel:intensjon") && p.get("tier") !== "Intensjon")
    d = d.filter((r) => r.notice_type !== "intensjon"); // listene viser kontrakter; intensjoner har eget filter
  const sup = p.get("supplier"); if (sup) d = d.filter((r) => r.winner_ids.includes(sup));
  const cs = p.get("case_status");
  if (cs === "uten") d = d.filter((r) => !caseStatus(r));
  else if (cs === "med") d = d.filter((r) => caseStatus(r));
  else if (cs === "apne") d = d.filter((r) => { const s = caseStatus(r); return !!s && !s.startsWith("Avsluttet"); });
  else if (cs) d = d.filter((r) => caseStatus(r) === cs);
  const asc = p.get("dir") === "asc";
  let sort = p.get("sort") || "priority";
  if (sort === "priority") sort = group ? "peer_percentile" : "percentile"; // ved lik poengsum går større kontrakter først
  return [...d].sort((a, b) => {
    const x = a[sort] as number | string | null, y = b[sort] as number | string | null;
    if (x == null) return 1; if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
  });
}

const rowOut = (r: Row): ContractRow => ({ ...r, case_status: caseStatus(r) });

function suppliers(all: Row[], cards: Record<string, Card>, p: URLSearchParams) {
  const d = scope(all, p.get("buyers"));
  const by = new Map<string, Row[]>();
  const names = new Map<string, string>();
  for (const r of d) r.winner_ids.forEach((o, i) => { by.set(o, [...(by.get(o) ?? []), r]); if (r.winner_names[i]) names.set(o, r.winner_names[i]); });
  let rows = [...by.entries()].map(([orgnr, g]) => {
    const e = cards[orgnr]?.brreg;
    return {
      orgnr, name: e?.navn || names.get(orgnr) || orgnr, n: g.length, n_buyers: new Set(g.map((r) => r.buyer_id)).size,
      total_value: sum(g.map((r) => r.award_value)), avg_priority: Math.round((sum(g.map((r) => r.priority)) / g.length) * 10) / 10,
      n_high: g.filter((r) => r.tier === "Høy").length,
      brreg_flags: [...new Set(g.flatMap((r) => r.flags.filter((f) => f.source === "brreg").map((f) => f.label)))].sort(),
      orgform: e?.orgform ?? null, stiftet: e?.stiftet ?? null, ansatte: e?.ansatte ?? null, in_brreg: !!e,
    };
  });
  const q = (p.get("q") ?? "").toLowerCase();
  if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q) || r.orgnr.includes(q));
  if (p.get("only_flagged") === "true") rows = rows.filter((r) => r.brreg_flags.length);
  rows.sort((a, b) => b.brreg_flags.length - a.brreg_flags.length || b.avg_priority - a.avg_priority);
  return { rows: rows.slice(0, +(p.get("limit") || 200)), total: rows.length };
}

// ------------------------------------------------------------------ filer (CSV og arbeidspapir)

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const nb = (v: unknown) => (v == null ? "" : typeof v === "number" ? String(Math.round(v * 100) / 100).replace(".", ",") : String(v));

async function save(filename: string, text: string) {
  const dl = await window.claude?.use("downloads").catch(() => null);
  if (!dl) throw new HttpError("Nedlasting er ikke tilgjengelig her. Åpne siden på claude.ai.");
  await dl.save({ filename, data: new Blob([text], { type: filename.endsWith(".csv") ? "text/csv" : "text/html" }) });
}

export async function exportCsv(params: Record<string, unknown>) {
  const { rows } = await load();
  const p = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));
  const cols = ["id", "pub_date", "title", "buyer", "region", "sector", "method", "num_bidders", "est_value", "award_value",
    "priority", "percentile", "tier", "peer_group", "peer_percentile", "peer_tier", "cri", "klagerisiko"];
  // Kolonnenavnene sier at prioritet og klagerisiko er rangeringer, ikke sannsynligheter
  const head: Record<string, string> = { priority: "prioritet_poeng", percentile: "prioritet_persentil_nasjonalt",
    tier: "prioritetsnivå_relativt", klagerisiko: "klagerisiko_persentil", peer_group: "sammenligningsgruppe",
    peer_percentile: "prioritet_persentil_i_gruppe", peer_tier: "prioritetsnivå_i_gruppe" };
  const lines = [[...cols.map((c) => head[c] ?? c), "leverandører", "observasjoner", "sak"].join(";")];
  for (const r of filter(rows, p)) {
    lines.push([...cols.map((c) => nb(r[c])), r.winner_names.join(", "), r.flags.map((f) => f.label).join(", "), caseStatus(r) ?? ""]
      .map((v) => (/[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)).join(";"));
  }
  await save("anskaffelsesradar.csv", "﻿" + lines.join("\n"));
}

// Revisorenes vurderinger som fasit for ML (samme kolonner som serverens /api/labels.csv)
export async function exportLabels() {
  const { rows } = await load();
  const byKey = new Map(rows.map((r) => [keyOf(r.id), r]));
  const cols = ["kontrakt_id", "tittel", "oppdragsgiver", "vurdering", "relevant", "begrunnelse", "vurdert_av", "vurdert",
    "status", "prioritet", "cri", "klagerisiko", "flagg"];
  const lines = [cols.join(";")];
  for (const key of cases.keys()) {
    const c = await caseOut(key), r = byKey.get(key);
    if (!c?.vurdering) continue;
    const ev = c.events.find((e) => e.field === "vurdering"); // nyeste først
    lines.push([r?.id ?? key, c.title, c.buyer, c.vurdering, c.vurdering === "Relevant" ? "1" : "0", c.vurdering_grunn ?? "",
      ev?.actor ?? "", ev?.at ?? "", c.status, nb(r?.priority), nb(r?.cri), nb(r?.klagerisiko), (r?.flags ?? []).map((f) => f.label).join(", ")]
      .map((v) => String(v ?? "")).map((v) => (/[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)).join(";"));
  }
  await save("revisorvurderinger.csv", "\ufeff" + lines.join("\n"));
}

// Sporbarhet: hvilken kode og hvilke data prioriteten bygde på, nå og da revisor satte vurderingen
type Build = { commit?: string | null; built_at?: string; data_updated_at?: string | null };
const short = (c?: string | null) => (c ? c.slice(0, 7) : "ukjent");
function grunnlag(b?: Build, v?: { at: string; priority: number | null; tier: string; flags: string[]; build?: Build }) {
  const now = b ? `<p><small>Datagrunnlag oppdatert ${esc(b.data_updated_at ?? "–")}. Beregnet med programversjon ${esc(short(b.commit))}, bygget ${esc(b.built_at ?? "–")}.</small></p>` : "";
  const then = v ? `<p><small>Da vurderingen ble satt (${esc(v.at)}): prioritet ${esc(v.priority == null ? "–" : Math.round(v.priority))} (${esc(v.tier)}), `
    + `flagg: ${esc(v.flags.join(", ") || "ingen")}. Programversjon ${esc(short(v.build?.commit))}, data fra ${esc(v.build?.data_updated_at ?? "–")}.</small></p>` : "";
  return now + then;
}

export async function saveWorkpaper(key: string) {
  const { rows, cards, meta } = await load();
  const c = await caseOut(key);
  const d = rows.find((r) => keyOf(r.id) === key);
  if (!c || !d) throw new HttpError("Fant ikke saken");
  const fmt = (v: unknown) => (v == null ? "–" : typeof v === "number" ? Math.round(v).toLocaleString("nb-NO") : esc(v));
  const flags = d.flags.map((f) => `<li>${esc(f.label)}${f.why ? " – " + esc(f.why) : ""}${f.data_note ? " (" + esc(f.data_note) + ")" : ""} <small>(kilde: ${esc(f.source)})</small></li>`).join("") || "<li>Ingen</li>";
  const sup = d.winner_ids.map((o) => `<li>${esc(cards[o]?.brreg?.navn ?? o)} – orgnr ${esc(o)}, stiftet ${esc(cards[o]?.brreg?.stiftet ?? "–")}</li>`).join("");
  const ev = c.events.map((e) => `<tr><td>${esc(e.at)}</td><td>${esc(e.actor)}</td><td>${esc(e.field)}</td><td>${esc(e.old)}</td><td>${esc(e.new)}</td></tr>`).join("");
  const html = `<!doctype html><html lang="nb"><meta charset="utf-8"><title>Arbeidspapir ${esc(d.title)}</title>
<style>body{font:14px/1.5 system-ui,sans-serif;max-width:800px;margin:24px auto;padding:0 16px;color:#111}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left;font-size:13px}
pre{white-space:pre-wrap;background:#f5f5f5;padding:8px}small{color:#666}</style>
<h1>Arbeidspapir: ${esc(d.title)}</h1>
<p><b>Oppdragsgiver:</b> ${esc(d.buyer)} &nbsp; <b>Kunngjøring:</b> ${esc(d.id)} (${esc(d.pub_date)})<br>
<b>Status:</b> ${esc(c.status)} &nbsp; <b>Ansvarlig:</b> ${esc(c.assignee || "–")} &nbsp; <b>Utskrevet:</b> ${new Date().toISOString().slice(0, 19)}</p>
<p><b>Prioritet:</b> ${fmt(d.priority)} (${esc(d.tier)}, persentil ${fmt(d.percentile)} nasjonalt) – røde flagg (CRI) ${fmt(d.cri)}. Klagerisiko-persentil (eksperimentell, teller ikke) ${fmt(d.klagerisiko)}.
Prioriteten er en relativ rangering, ikke en sannsynlighet for regelbrudd. Den sier hva som bør sjekkes først, ikke at noe er galt.</p>
<h2>Nøkkeldata</h2><table>
<tr><th>Prosedyre</th><td>${esc(d.method)}</td></tr><tr><th>Antall tilbud</th><td>${fmt(d.num_bidders)}</td></tr>
<tr><th>Estimert verdi</th><td>${fmt(d.est_value)}</td></tr><tr><th>Tildelt verdi</th><td>${fmt(d.award_value)}</td></tr>
<tr><th>Leverandør(er)</th><td>${esc(d.winner_names.join(", "))}</td></tr><tr><th>Kilde</th><td>${esc(d.url ?? d.source)}</td></tr>
<tr><th>Kunngjøringen hentet</th><td>${esc(d.fetched_at ?? "før hentetidspunkt ble lagret")}</td></tr></table>
${grunnlag(meta?.build, (c as unknown as Record<string, any>).vurdert_grunnlag)}
<h2>Observasjoner (flagg)</h2><ul>${flags}</ul><h2>Leverandør (Brønnøysundregistrene)</h2><ul>${sup || "<li>–</li>"}</ul>
<h2>Notat</h2><pre>${esc(c.note)}</pre>
<h2>Logg</h2><table><tr><th>Tid</th><th>Hvem</th><th>Felt</th><th>Fra</th><th>Til</th></tr>${ev}</table></html>`;
  await save(`arbeidspapir-${key}.html`, html);
}

// ------------------------------------------------------------------ ruting

export async function staticApi(url: string, init?: RequestInit): Promise<unknown> {
  const u = new URL(url, "http://x");
  const p = u.searchParams, path = u.pathname, method = (init?.method ?? "GET").toUpperCase();
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  const { rows, cards, meta, model } = await load();
  await db();
  let m: RegExpMatchArray | null;

  if (path === "/api/meta") return meta;
  if (path === "/api/model") return model;
  if (path === "/api/buyers") {
    return [...groupBy(rows, (r) => r.buyer_id).entries()].map(([id, g]) => ({ id, name: g[0].buyer, n: g.length }))
      .sort((a, b) => a.name.localeCompare(b.name, "nb"));
  }
  if (path === "/api/summary") return summary(rows, p.get("buyers"));
  if (path === "/api/invoices") {
    const inv = (await load()).invoices, b = p.get("buyers");
    return b ? { ...inv, rows: inv.rows.filter((r) => b.split(",").includes(r.buyer_id)) } : inv;
  }
  if (path === "/api/contracts") {
    const d = filter(rows, p), limit = +(p.get("limit") || 50), page = +(p.get("page") || 1);
    return { rows: d.slice((page - 1) * limit, page * limit).map(rowOut), total: d.length };
  }
  if ((m = path.match(/^\/api\/contracts\/(.+)\/assess$/))) {
    const r = rows.find((x) => x.id === decodeURIComponent(m![1]));
    if (!r) throw new HttpError("Fant ikke kontrakten");
    return assess(r, (await load()).llmSystem);
  }
  if ((m = path.match(/^\/api\/contracts\/([^/]+)$/))) {
    const id = decodeURIComponent(m[1]);
    const r = rows.find((x) => x.id === id);
    if (!r) throw new HttpError("Fant ikke kontrakten");
    const same = rows.filter((x) => x.buyer_id === r.buyer_id && x.winner === r.winner && x.id !== id)
      .sort((a, b) => (b.pub_date ?? "").localeCompare(a.pub_date ?? "")).slice(0, 10)
      .map((x) => ({ id: x.id, title: x.title, pub_date: x.pub_date, tier: x.tier }));
    return { ...rowOut(r), sameSupplier: same, suppliers: r.winner_ids.map((o) => cards[o] ?? { orgnr: o, brreg: null, roles: [], owners: [], registers: {}, regnskap: null, sanctions: null, brreg_url: `https://virksomhet.brreg.no/nb/oppslag/enheter/${o}` }),
      case: await caseOut(keyOf(id)), assessments: await assessments(keyOf(id)) };
  }
  if (path === "/api/suppliers") return suppliers(rows, cards, p);
  if ((m = path.match(/^\/api\/suppliers\/(\d+)$/))) {
    const card = cards[m[1]];
    if (!card) throw new HttpError("Fant ikke leverandøren");
    const mine = scope(rows, p.get("buyers")).filter((r) => r.winner_ids.includes(m![1])).sort((a, b) => b.priority - a.priority);
    return { ...card, contracts: mine.map(rowOut) };
  }
  if (path === "/api/cases" && method === "POST") {
    const r = rows.find((x) => x.id === body.contractId);
    if (!r) throw new HttpError("Fant ikke kontrakten");
    const key = keyOf(r.id), now = new Date().toISOString();
    if (!cases.has(key)) {
      await saveCase(key, { contract_id: r.id, title: r.title, buyer: r.buyer, status: body.status ?? "Ny", assignee: body.assignee ?? "",
        note: body.note ?? "", created_at: now, updated_at: now,
        events: [{ at: now, ...(await actor()), field: "opprettet", old: null, new: body.status ?? "Ny" }] });
    }
    return caseOut(key);
  }
  if ((m = path.match(/^\/api\/cases\/([^/]+)$/))) {
    const key = m[1], c = cases.get(key);
    if (!c) throw new HttpError("Fant ikke saken");
    if (method === "DELETE") {
      // Databasen først: Cloudflare-versjonen avviser sletting for å bevare revisjonssporet
      const d = await db();
      if (d) await d.doc(`cases/${key}`).delete().catch((e: any) => { throw new HttpError(e?.message ?? String(e)); });
      cases.delete(key);
      return { ok: true };
    }
    if (method === "PATCH") {
      const now = new Date().toISOString(), who = await actor();
      const next = { ...c, events: [...c.events], updated_at: now } as StoredCase & Record<string, unknown>;
      for (const k of ["status", "assignee", "note", "vurdering", "vurdering_grunn"] as const) {
        if (k in body && (body[k] ?? "") !== (c[k] ?? "")) { next.events.push({ at: now, ...who, field: k, old: c[k] ?? null, new: body[k] ?? null }); next[k] = body[k]; }
      }
      if ("vurdering" in body && body.vurdering !== c.vurdering) {
        const r = rows.find((x) => keyOf(x.id) === key);
        next.vurdert_grunnlag = { at: now, priority: r?.priority ?? null, tier: r?.tier ?? "", flags: (r?.flags ?? []).map((f) => f.label), build: meta?.build };
      }
      await saveCase(key, next);
      return caseOut(key);
    }
  }
  if (path === "/api/cases") return Promise.all([...cases.keys()].map(caseOut));
  if (path === "/api/refresh") throw new HttpError("Dataene oppdateres automatisk hver natt i artifact-versjonen.");
  if (path.startsWith("/api/doffin")) throw new HttpError("Doffin live krever serverversjonen.");
  throw new HttpError("Ikke tilgjengelig i artifact-versjonen");
}
