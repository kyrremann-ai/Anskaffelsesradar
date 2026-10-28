import { QueryClient, useQuery } from "@tanstack/react-query";
import { onCasesChange, staticApi } from "./static";

// Bygget som artifact (uten server): data fra JSON-filer, saker i artifactens database
export const STATIC = import.meta.env.VITE_STATIC === "1";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false, retry: 1 } },
});
// Andre brukeres endringer i saker kommer live fra databasen
if (STATIC) onCasesChange(() => queryClient.invalidateQueries());

export type Tier = "Høy" | "Forhøyet" | "Lav";
// checks: alternative forklaringer som er sjekket (leverandørflagg). data_note: hva som mangler i dataene.
export type FlagCheck = { q: string; status: "ok" | "justert" | "forklarer" | "svekker" | "ukjent"; a: string };
export type Flag = { key: string; label: string; weight: number; source: string; why?: string; checks?: FlagCheck[]; data_note?: string };

export type ContractRow = {
  id: string; title: string; pub_date: string; buyer_id: string; buyer: string; region: string; sector: string;
  method: string; num_bidders: number | null; est_value: number | null; award_value: number | null;
  award_over_estimate_pct: number | null; priority: number; tier: Tier; percentile: number;
  cri: number; klagerisiko: number | null; winner_names: string[]; winner_ids: string[]; flags: Flag[]; data_coverage: number;
  framework: number; case_status: string | null; source: string;
};

export type BrregEntity = {
  orgnr: string; kind: string; navn: string; orgform: string; stiftet: string | null; nace: string | null;
  nace_tekst: string | null; ansatte: number | null; mva: boolean | null; konkurs: boolean; under_avvikling: boolean;
  tvangsavvikling: boolean; slettedato: string | null; adresse: string;
};
export type Role = { rolle: string; rolle_navn: string; type: string; navn: string; person_key: string };
export type Regnskap = {
  aar: number | null; driftsinntekter: number | null; driftsresultat: number | null; aarsresultat: number | null;
  egenkapital: number | null; gjeld: number | null; eiendeler: number | null;
};
export type Owner = { name: string; orgnr: string | null; share_pct: number | null; source: string; year: number | null; person: boolean };
export type SupplierCard = {
  orgnr: string; brreg: BrregEntity | null; roles: Role[]; brreg_url: string;
  regnskap: Regnskap | null; owners: Owner[]; registers: Record<string, string>;
  sanctions: { name: string; datasets: string; is_sanction: number } | null;
};
export type SourceStatus = { status: "ok" | "feil" | "av" | "demo"; rows?: number | null; at: string; error?: string; note?: string };
export type CaseEvent = { at: string; actor: string; field: string; old: string | null; new: string | null };
export type Case = {
  id: number; contract_id: string; title: string; buyer: string; status: string; assignee: string; note: string;
  vurdering?: string; vurdering_grunn?: string; created_at: string; updated_at: string; events: CaseEvent[];
};
export type Assessment = { id: number; at: string; provider: string; model: string; text: string };

export type ContractDetail = ContractRow & {
  url: string | null; cpv: string; nature: string; accelerated: number; justification: string | null;
  tender_duration_days: number | null; contract_months: number | null; winner_size: string | null;
  repeat_pair_count: number | null; prev_contract: number | null; winner_market_share_pct: number | null;
  sameSupplier: { id: string; title: string; pub_date: string; tier: Tier }[];
  suppliers: SupplierCard[]; case: Case | null; assessments: Assessment[];
  // null = ikke slått opp (bare høy/forhøyet prioritet slås opp)
  einnsyn?: JournalPost[] | null;
};
export type JournalPost = { title: string | null; date: string | null; type: string | null; case: string | null; url: string | null };

export type Stats = {
  n: number; n_high: number; n_elevated: number; single_bid_pct: number | null; direct_pct: number | null;
  total_value: number; avg_priority: number | null; coverage_pct: number | null;
};
export type Group = {
  name: string; buyer_id: string | null; n: number; avg_score: number; single_bid_pct: number | null;
  direct_pct: number | null; n_flagged: number; n_high: number; total_value: number;
};
export type Summary = Stats & {
  loading?: boolean; national: Stats | null; n_buyers: number; period: [string, string];
  monthly: { month: string; n: number; flagged: number; direct: number }[];
  flag_counts: { key: string; label: string; n: number }[]; by_sector: Group[]; by_region: Group[];
  by_buyer: Group[]; cases: Record<string, number>;
};
export type Meta = {
  mode: "demo" | "live"; updated_at: string | null; llm: string; doffin: boolean; case_statuses: string[];
  refresh: { running: boolean; step: string | null; error: string | null };
  flag_labels: Record<string, string>; sources: Record<string, SourceStatus>;
};

export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC) return staticApi(path, init) as Promise<T>;
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* tom body */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export function useApi<T>(path: string, params: Record<string, unknown> = {}, opts: { enabled?: boolean; refetchInterval?: number | false | ((q: { state: { data?: T } }) => number | false) } = {}) {
  const url = path + qs(params);
  return useQuery<T>({ queryKey: [url], queryFn: () => api<T>(url), ...opts });
}
