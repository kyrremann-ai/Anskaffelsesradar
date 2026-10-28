import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Tier } from "../lib/api";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export function Card({ title, desc, right, children, className }: {
  title?: ReactNode; desc?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("rounded-md border border-line bg-surface min-w-0", className)}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">{title}</h2>
            {desc && <p className="mt-0.5 text-xs text-muted">{desc}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHead({ title, desc, right }: { title: string; desc?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {desc && <p className="mt-1 max-w-3xl text-sm text-muted">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

export function Kpi({ label, value, sub, compare }: { label: string; value: ReactNode; sub?: ReactNode; compare?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-surface px-3 py-2.5 sm:px-3.5 sm:py-3">
      <div className="truncate text-[11px] font-medium uppercase leading-tight tracking-wide text-muted" title={typeof label === "string" ? label : undefined}>{label}</div>
      <div className="num mt-1 text-xl font-bold leading-none sm:text-2xl">{value}</div>
      {sub && <div className="mt-1 text-xs leading-snug text-muted">{sub}</div>}
      {compare && <div className="mt-1 text-xs leading-snug text-muted">{compare}</div>}
    </div>
  );
}

const TIER = {
  "Høy": "bg-hi-soft text-hi", "Forhøyet": "bg-mid-soft text-mid", "Lav": "bg-lo-soft text-lo",
} as const;

export function TierBadge({ tier }: { tier: Tier }) {
  return <span className={cx("inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium", TIER[tier])}>{tier} prioritet</span>;
}

export function scoreColor(v: number) {
  return v >= 60 ? "var(--hi)" : v >= 40 ? "var(--mid)" : "var(--lo)";
}

export function Meter({ value, max = 100, color, className }: { value: number; max?: number; color?: string; className?: string }) {
  return (
    <div className={cx("h-1.5 rounded bg-sunk", className)} aria-hidden>
      <div className="h-1.5 rounded" style={{ width: `${Math.min(100, (value / max) * 100)}%`, background: color ?? scoreColor(value) }} />
    </div>
  );
}

export function Button({ variant = "primary", className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" | "ghost" }) {
  const v = {
    primary: "bg-accent text-surface hover:opacity-90",
    outline: "border border-line bg-surface hover:bg-sunk",
    ghost: "hover:bg-sunk",
  }[variant];
  return <button className={cx("inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50", v, className)} {...p} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-sunk", className)} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-8 text-center text-sm text-muted">{children}</div>;
}

export function Select({ value, onChange, items, placeholder, label }: {
  value: string; onChange: (v: string) => void; items: { v: string; l: string }[]; placeholder: string; label: string;
}) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-md border border-line bg-surface px-2 text-sm">
      <option value="">{placeholder}</option>
      {items.map((i) => <option key={i.v} value={i.v}>{i.l}</option>)}
    </select>
  );
}

export function Search({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      aria-label={placeholder} className="h-9 w-full rounded-md border border-line bg-surface px-3 text-sm sm:w-72" />
  );
}
