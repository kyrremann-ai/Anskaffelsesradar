import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Flag, Tier } from "../lib/api";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// Ikoner tegnet som SVG i én strektykkelse (1,75 px på 20-rutenett), i stedet for unicode-tegn
const ICONS: Record<string, ReactNode> = {
  refresh: <><path d="M16.5 10a6.5 6.5 0 1 1-1.9-4.6" /><path d="M16.5 3.5v3.5H13" /></>,
  chevron: <path d="m6 8 4 4 4-4" />,
  sun: <><circle cx="10" cy="10" r="3.2" /><path d="M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M4.7 15.3 6 14M14 6l1.3-1.3" /></>,
  moon: <path d="M15.5 12.4A6.5 6.5 0 0 1 7.6 4.5a6.5 6.5 0 1 0 7.9 7.9Z" />,
  arrow: <path d="M4 10h11m-4-4 4 4-4 4" />,
  back: <path d="M16 10H5m4-4-4 4 4 4" />,
  external: <><path d="M11 4h5v5" /><path d="m16 4-7 7" /><path d="M14 11.5V16H4V6h4.5" /></>,
  close: <path d="m5.5 5.5 9 9m0-9-9 9" />,
  check: <path d="m4.5 10.5 3.5 3.5 7.5-8" />,
  rule: <path d="M10 3.5 16.5 10 10 16.5 3.5 10Z" />,
  flag: <><path d="M5 17V3.5" /><path d="M5 4h9l-2 3.25L14 10.5H5" /></>,
  download: <><path d="M10 3.5v9m-4-4 4 4 4-4" /><path d="M4 16h12" /></>,
  menu: <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />,
};
export function Icon({ name, className }: { name: keyof typeof ICONS | string; className?: string }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden className={cx("inline-block h-4 w-4 shrink-0", className)}>{ICONS[name]}</svg>
  );
}

// Flaggkildene har hver sin farge, så poengstripen viser hvor prioriteten kommer fra
export const SOURCE_LABEL: Record<string, string> = { kunngjøring: "Kunngjøringen", brreg: "Brønnøysundregistrene", andre: "Andre registre" };
export function sourceKey(source: string): "kunngjøring" | "brreg" | "andre" {
  return source === "kunngjøring" ? "kunngjøring" : source === "brreg" ? "brreg" : "andre";
}
export const SOURCE_COLOR = { kunngjøring: "var(--src-kunngjoring)", brreg: "var(--src-brreg)", andre: "var(--src-andre)" } as const;

/** Poengstripen: prioriteten tegnet som summen av flaggene. Lengden er prioriteten (0–100), hvert segment
 *  er ett flaggs bidrag i kildens farge, og avskrevne flagg vises skravert. Appens signatur. */
export function ScoreStrip({ flags, priority, size = "sm", className }: { flags: Flag[]; priority: number; size?: "xs" | "sm" | "lg"; className?: string }) {
  const segs = flags.filter((f) => f.source !== "datakvalitet" && ((f.bidrag ?? 0) > 0 || f.dismissed));
  const h = size === "lg" ? "h-3" : size === "sm" ? "h-1.5" : "h-1";
  const total = segs.reduce((a, f) => a + (f.bidrag ?? 0), 0) || 1;
  const scale = Math.min(1, priority / 100) / total;
  const title = segs.map((f) => `${f.label}: ${f.dismissed ? "avskrevet" : `+${(f.bidrag ?? 0).toLocaleString("nb-NO")}`}`).join("\n");
  return (
    <div className={cx("flex w-full overflow-hidden rounded-full bg-sunk", h, className)} role="img"
      aria-label={`Prioritet ${Math.round(priority)} av 100`} title={title || undefined}>
      {segs.map((f) => (
        <span key={f.key} className={cx("h-full transition-[width] duration-200 first:rounded-l-full [&+span]:ml-px", f.dismissed && "strip-dismissed")}
          style={{ width: f.dismissed ? (size === "lg" ? "6px" : "3px") : `${(f.bidrag ?? 0) * scale * 100}%`,
            background: f.dismissed ? undefined : SOURCE_COLOR[sourceKey(f.source)] }} />
      ))}
    </div>
  );
}

export function SourceLegend({ className }: { className?: string }) {
  return (
    <span className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted", className)}>
      {(Object.keys(SOURCE_LABEL) as (keyof typeof SOURCE_COLOR)[]).map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: SOURCE_COLOR[k] }} />{SOURCE_LABEL[k]}</span>
      ))}
    </span>
  );
}

export function Card({ title, desc, right, children, className }: {
  title?: ReactNode; desc?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("min-w-0 rounded-lg border border-line bg-surface", className)}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 px-5 pb-1 pt-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold leading-snug">{title}</h2>
            {desc && <p className="mt-0.5 max-w-prose text-xs text-muted">{desc}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="px-5 pb-5 pt-3">{children}</div>
    </section>
  );
}

export function PageHead({ title, desc, right }: { title: string; desc?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.01em]">{title}</h1>
        {desc && <p className="mt-1.5 max-w-[68ch] text-sm text-muted">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

/** Én celle i nøkkeltallsraden: tallet først, så hva det er. Ingen merkelapp over. */
export function Kpi({ label, value, sub, compare, to }: { label: string; value: ReactNode; sub?: ReactNode; compare?: ReactNode; to?: string }) {
  const cls = "group block min-w-0 px-4 py-3.5";
  const body = (
    <>
      <div className="num text-2xl font-semibold leading-none tracking-[-0.01em]">{value}</div>
      <div className="mt-1.5 text-sm font-medium leading-tight group-hover:underline">{label}</div>
      {sub && <div className="mt-0.5 text-xs leading-snug text-muted">{sub}</div>}
      {compare && <div className="mt-0.5 text-xs leading-snug text-muted">{compare}</div>}
    </>
  );
  // Med lenke: hele cellen åpner arbeidsflaten med tilsvarende filter
  return to ? <Link to={to} className={cx(cls, "transition-colors duration-150 hover:bg-sunk")}>{body}</Link>
    : <div className={cls}>{body}</div>;
}

/** Nøkkeltallene som én rad med skillelinjer, ikke som et rutenett av like kort */
export function KpiRow({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 [&>*]:border-b [&>*]:border-r [&>*]:border-line">{children}</div>
    </div>
  );
}

const TIER = {
  "Høy": "bg-hi-soft text-hi", "Forhøyet": "bg-mid-soft text-mid", "Lav": "bg-sunk text-muted", "Intensjon": "border border-line text-muted",
} as const;

// Prioritet er rangering, ikke sannsynlighet: også et datasett uten problemer har en topp 5 %
export const TIER_HELP = "Relativ rangering, ikke sannsynlighet for regelbrudd. Høy = topp 5 % nasjonalt etter summen av observasjonene, forhøyet = persentil 80–95.";

export function TierBadge({ tier }: { tier: Tier }) {
  if (tier === "Intensjon")
    return <span title="Intensjonskunngjøring: planlagt direkte tildeling. Ikke en kontrakt, og rangeres ikke." className={cx("inline-block whitespace-nowrap rounded px-1.5 py-px text-xs font-medium", TIER[tier])}>Intensjonskunngjøring</span>;
  return <span title={TIER_HELP} className={cx("inline-block whitespace-nowrap rounded px-1.5 py-px text-xs font-medium", TIER[tier])}>{tier} prioritet</span>;
}

// Faste kontrollpunkter vises ved siden av rangeringen, aldri som en del av den
export function CheckBadge({ label, why }: { label: string; why?: string }) {
  return <span title={why ? `${label}: ${why}` : label} className="inline-flex items-center gap-1 whitespace-nowrap rounded border border-[color-mix(in_srgb,var(--hi)_38%,transparent)] px-1.5 py-px text-xs text-hi"><Icon name="rule" className="h-3 w-3" />{label}</span>;
}

// Tallet får samme farge som nivået, så «17» ikke blir grønn ved siden av «Forhøyet prioritet»
const TIER_COLOR: Record<string, string> = { "Høy": "var(--hi)", "Forhøyet": "var(--mid)", "Lav": "var(--muted)" };
export function tierColor(tier: Tier | string) {
  return TIER_COLOR[tier] ?? "var(--muted)";
}

export function Meter({ value, max = 100, color, className }: { value: number; max?: number; color?: string; className?: string }) {
  return (
    <div className={cx("h-1.5 rounded-full bg-sunk", className)} aria-hidden>
      <div className="h-1.5 rounded-full transition-[width] duration-200" style={{ width: `${Math.min(100, (value / max) * 100)}%`, background: color ?? "var(--accent)" }} />
    </div>
  );
}

export function Button({ variant = "primary", className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" | "ghost" }) {
  const v = {
    primary: "bg-accent text-surface hover:bg-[color-mix(in_srgb,var(--accent)_88%,black)] active:translate-y-px",
    outline: "border border-line bg-surface hover:border-field hover:bg-sunk active:translate-y-px",
    ghost: "hover:bg-sunk",
  }[variant];
  return <button className={cx("inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50", v, className)} {...p} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-sunk", className)} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-md px-6 py-12 text-center text-sm text-muted">{children}</div>;
}

export function Select({ value, onChange, items, placeholder, label }: {
  value: string; onChange: (v: string) => void; items: { v: string; l: string }[]; placeholder: string; label: string;
}) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}
      className="h-9 w-full min-w-0 rounded-md border border-line bg-surface px-2.5 text-sm transition-colors duration-150 hover:bg-sunk sm:w-auto sm:max-w-[18rem]">
      <option value="">{placeholder}</option>
      {items.map((i) => <option key={i.v} value={i.v}>{i.l}</option>)}
    </select>
  );
}

export function Search({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      aria-label={placeholder} className="h-9 w-full rounded-md border border-line bg-surface px-3 text-sm sm:w-80" />
  );
}

// Konkurser hos rolleinnehavere (åpne data) + manuell sjekk av konkurskarantene i Konkursregisteret.
// Radaren slår aldri opp karantene selv og lagrer ikke resultatet.
export function KonkursCheck({ links, url, compact }: {
  links?: { orgnr: string; navn: string; konkursdato: string | null; rolle: string; via: string | null }[];
  url?: string; compact?: boolean;
}) {
  return (
    <div className={compact ? "mt-2 text-sm" : "space-y-2 text-sm"}>
      {links && links.length > 0 && (
        <div>
          <div className="mb-1 text-xs text-muted">Rolle i annet selskap under konkursbehandling</div>
          <ul className="space-y-0.5">
            {links.map((k, i) => (
              <li key={i} className="flex flex-wrap justify-between gap-x-3">
                <span>{k.via ?? "Rolleinnehaver"} <span className="text-xs text-muted">({k.rolle.toLowerCase()})</span></span>
                <a href={`https://virksomhet.brreg.no/nb/oppslag/enheter/${k.orgnr}`} target="_blank" rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-accent hover:underline">{k.navn}{k.konkursdato ? `, konkurs ${k.konkursdato}` : ""}<Icon name="external" className="h-3 w-3" /></a>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-muted">Koblet på navn og fødselsdato i Enhetsregisteret. Er en observasjon, ikke en konklusjon, og sier ikke noe om konkurskarantene.</p>
        </div>
      )}
      {url && (
        <p className="text-xs text-muted">
          <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-accent hover:underline">Sjekk konkurskarantene i Brreg<Icon name="external" className="h-3.5 w-3.5" /></a>
          {" "}Krever innlogging med ID-porten. Søk på etternavn og ett fornavn gir fødselsår og kommune, så sammenlign med rollelisten. Radaren lagrer ikke svaret. Noter resultatet i saken om det er relevant.
        </p>
      )}
    </div>
  );
}
