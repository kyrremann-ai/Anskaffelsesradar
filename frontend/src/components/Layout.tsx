import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api, queryClient, useApi, type Meta } from "../lib/api";
import { dateTime } from "../lib/format";
import { useScope } from "../lib/scope";
import { Button, cx } from "./ui";

const NAV = [
  { to: "/", label: "Oversikt" },
  { to: "/arbeid", label: "Arbeidsflate" },
  { to: "/oppdragsgivere", label: "Oppdragsgivere" },
  { to: "/leverandorer", label: "Leverandører" },
  { to: "/doffin", label: "Doffin live" },
  { to: "/metode", label: "Modell og metode" },
];

function ScopePicker() {
  const { buyers, setBuyers } = useScope();
  const { data = [] } = useApi<{ id: string; name: string; n: number }[]>("/api/buyers");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string[]>(buyers);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => setSel(buyers), [buyers, open]);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const names = Object.fromEntries(data.map((b) => [b.id, b.name]));
  const label = buyers.length === 0 ? `Alle oppdragsgivere (${data.length})`
    : buyers.length === 1 ? names[buyers[0]] ?? "1 enhet" : `${names[buyers[0]] ?? ""} + ${buyers.length - 1}`;
  const shown = data.filter((b) => b.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex max-w-[60vw] items-center gap-2 rounded-md border border-line bg-surface px-3 py-1.5 text-sm hover:bg-sunk sm:max-w-xs">
        <span className="whitespace-nowrap text-xs text-muted">Mine enheter:</span>
        <span className="truncate font-medium">{label}</span>
        <span aria-hidden className="text-muted">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1 w-[min(92vw,360px)] rounded-md border border-line bg-surface p-3 shadow-lg">
          <p className="mb-2 text-xs text-muted">Velg kommunene og foretakene du reviderer. Hele appen filtreres på dem.</p>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Søk oppdragsgiver"
            className="mb-2 h-8 w-full rounded border border-line bg-sunk px-2 text-sm" />
          <ul className="max-h-72 overflow-y-auto">
            {shown.map((b) => (
              <li key={b.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-sunk">
                  <input type="checkbox" checked={sel.includes(b.id)}
                    onChange={(e) => setSel(e.target.checked ? [...sel, b.id] : sel.filter((x) => x !== b.id))} />
                  <span className="flex-1 truncate">{b.name}</span>
                  <span className="num text-xs text-muted">{b.n}</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex justify-between gap-2">
            <Button variant="ghost" onClick={() => { setBuyers([]); setOpen(false); }}>Vis alle</Button>
            <Button onClick={() => { setBuyers(sel); setOpen(false); }}>Bruk ({sel.length})</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function RefreshButton({ meta }: { meta?: Meta }) {
  const running = meta?.refresh.running;
  const m = useMutation({
    mutationFn: () => api("/api/refresh", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/meta"] }),
  });
  useEffect(() => {
    if (!running) queryClient.invalidateQueries();
  }, [running]);
  return (
    <Button variant="outline" onClick={() => m.mutate()} disabled={running || m.isPending} title={meta?.refresh.step ?? undefined}>
      <span className={cx(running && "animate-spin")} aria-hidden>↻</span>
      {running ? "Henter …" : "Hent nye data"}
    </Button>
  );
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  return (
    <button className="rounded p-1.5 text-muted hover:bg-sunk" aria-label="Bytt lys/mørk modus"
      onClick={() => { const d = !dark; setDark(d); document.documentElement.dataset.theme = d ? "dark" : "light"; }}>
      {dark ? "☀" : "☾"}
    </button>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { data: meta } = useApi<Meta>("/api/meta", {}, { refetchInterval: 4000 });
  const [menu, setMenu] = useState(false);
  return (
    <div className="flex min-h-screen">
      <aside className={cx("fixed inset-y-0 left-0 z-40 w-60 flex-col bg-ink text-ink-fg md:flex", menu ? "flex" : "hidden")}>
        <div className="flex items-center gap-2.5 px-5 py-5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <div>
            <div className="font-bold">Anskaffelsesradar</div>
            <div className="text-xs opacity-70">Prioritering for revisjon</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} onClick={() => setMenu(false)}
              className={({ isActive }) => cx("block rounded-md px-3 py-2 text-sm", isActive ? "bg-white/10 font-medium" : "opacity-80 hover:bg-white/5")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <p className="border-t border-white/10 px-4 py-4 text-xs leading-relaxed opacity-70">
          Kilder: TED, Doffin og Brønnøysundregistrene. Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.
        </p>
      </aside>
      {menu && <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setMenu(false)} />}
      <div className="flex min-w-0 flex-1 flex-col md:pl-60">
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:px-8">
          <button className="rounded p-1.5 md:hidden" onClick={() => setMenu(true)} aria-label="Meny">☰</button>
          <ScopePicker />
          {meta?.mode === "demo" && (
            <span className="rounded bg-mid-soft px-2 py-0.5 font-mono text-xs text-mid" title="Fiktive oppdragsgivere og leverandører">DEMODATA</span>
          )}
          <span className="rounded bg-accent-soft px-2 py-0.5 text-xs text-accent" title="Se Modell og metode">
            Pilot: modell trent på syntetiske data
          </span>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-xs text-muted lg:inline">Oppdatert {dateTime(meta?.updated_at)}</span>
            <RefreshButton meta={meta} />
            <ThemeToggle />
          </div>
        </header>
        {meta?.refresh.error && (
          <div className="border-b border-line bg-hi-soft px-4 py-2 text-sm text-hi md:px-8">Siste oppdatering feilet: {meta.refresh.error}</div>
        )}
        <main className="w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
