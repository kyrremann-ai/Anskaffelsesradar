import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import logo from "../assets/logo.svg";
import { STATIC, api, queryClient, useApi, type Meta } from "../lib/api";
import { dateTime } from "../lib/format";
import { useScope } from "../lib/scope";
import { Button, Icon, cx } from "./ui";

const NAV = [
  { to: "/", label: "Oversikt" },
  { to: "/arbeid", label: "Arbeidsflate" },
  { to: "/oppdragsgivere", label: "Oppdragsgivere" },
  { to: "/leverandorer", label: "Leverandører" },
  { to: "/fakturaer", label: "Fakturakontroll" },
  { to: "/doffin", label: "Doffin live" },
  { to: "/metode", label: "Modell og metode" },
].filter((n) => !(STATIC && n.to === "/doffin"));

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
      <button onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog"
        className="flex h-9 max-w-[62vw] items-center gap-2 rounded-md border border-shell-line bg-white/[0.06] px-3 text-sm transition-colors duration-150 hover:bg-white/[0.12] sm:max-w-sm">
        <span className="whitespace-nowrap text-xs text-shell-muted">Mine enheter</span>
        <span className="truncate font-medium">{label}</span>
        <Icon name="chevron" className={cx("text-shell-muted transition-transform duration-150", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute left-0 z-40 mt-2 w-[min(92vw,380px)] rounded-lg border border-line bg-surface p-3 text-fg shadow-[0_12px_32px_-8px_rgba(16,28,22,0.28)]">
          <p className="mb-2 text-xs text-muted">Velg kommunene og foretakene du reviderer. Hele appen filtreres på dem.</p>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Søk oppdragsgiver"
            className="mb-2 h-9 w-full rounded-md border border-line bg-surface px-2.5 text-sm" />
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
    <button onClick={() => m.mutate()} disabled={running || m.isPending} title={meta?.refresh.step ?? undefined}
      className="inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium text-shell-fg transition-colors duration-150 hover:bg-white/[0.1] disabled:opacity-60">
      <Icon name="refresh" className={cx(running && "animate-spin")} />
      <span className="hidden sm:inline">{running ? "Henter …" : "Hent nye data"}</span>
    </button>
  );
}

function ThemeToggle() {
  // Uten eget valg følger appen systemet, så ikonet må starte fra det
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && window.matchMedia?.("(prefers-color-scheme: dark)").matches));
  return (
    <button className="grid h-9 w-9 place-items-center rounded-md text-shell-muted transition-colors duration-150 hover:bg-white/[0.1] hover:text-shell-fg" aria-label={dark ? "Bytt til lys modus" : "Bytt til mørk modus"}
      onClick={() => { const d = !dark; setDark(d); document.documentElement.dataset.theme = d ? "dark" : "light"; }}>
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { data: meta } = useApi<Meta>("/api/meta", {}, { refetchInterval: STATIC ? false : 4000 });
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 bg-shell text-shell-fg">
        <div className="mx-auto flex max-w-[1480px] flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-3 md:px-8">
          <NavLink to="/" className="flex items-center gap-2.5 rounded-md pr-1">
            <img src={logo} alt="" className="h-7 w-7" />
            <span className="hidden font-semibold tracking-[-0.01em] sm:inline">Anskaffelsesradar</span>
          </NavLink>
          <ScopePicker />
          {meta?.mode === "demo" && (
            <span className="rounded border border-[color-mix(in_srgb,#e8b44a_55%,transparent)] px-1.5 py-px text-xs font-medium text-[#f1cd7d]" title="Fiktive oppdragsgivere og leverandører">Demodata</span>
          )}
          <span className="hidden text-xs text-shell-muted md:inline" title="Se Modell og metode">Pilot: regelbaserte flagg</span>
          <div className="ml-auto flex items-center gap-1">
            <span className="mr-2 hidden text-xs text-shell-muted lg:inline">Oppdatert {dateTime(meta?.updated_at)}</span>
            {!STATIC && <RefreshButton meta={meta} />}
            <ThemeToggle />
          </div>
        </div>
        <nav aria-label="Hovedmeny" className="mx-auto max-w-[1480px] overflow-x-auto px-2 md:px-6">
          <ul className="flex min-w-max gap-1 pt-2">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink to={n.to} end={n.to === "/"}
                  className={({ isActive }) => cx("relative block whitespace-nowrap px-2.5 pb-2.5 pt-1.5 text-sm transition-colors duration-150",
                    "after:absolute after:inset-x-2.5 after:bottom-0 after:h-[3px] after:rounded-t-sm after:transition-colors",
                    isActive ? "font-medium text-shell-fg after:bg-shell-fg" : "text-shell-muted hover:text-shell-fg after:bg-transparent")}>
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      {meta?.refresh.error && (
        <div className="border-b border-line bg-hi-soft px-4 py-2 text-sm text-hi md:px-8">Siste oppdatering feilet: {meta.refresh.error}</div>
      )}
      <main className="mx-auto w-full max-w-[1480px] flex-1 px-4 py-7 md:px-8">{children}</main>
      <footer className="mx-auto w-full max-w-[1480px] px-4 pb-8 text-xs text-muted md:px-8">
        Kilder: TED, Doffin og Brønnøysundregistrene. Prioriteten sier hva som bør sjekkes først. Den er ikke bevis for misligheter.
      </footer>
    </div>
  );
}
