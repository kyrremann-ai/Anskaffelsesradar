import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/** «Mine enheter»: oppdragsgiverne revisoren har ansvar for. Lagres lokalt i nettleseren. */
type Scope = { buyers: string[]; setBuyers: (b: string[]) => void; param: string | undefined };

const Ctx = createContext<Scope>({ buyers: [], setBuyers: () => {}, param: undefined });
const KEY = "radar.scope";

function load(): string[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [buyers, setBuyers] = useState<string[]>(load);
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(buyers)); } catch { /* privat modus */ }
  }, [buyers]);
  return <Ctx.Provider value={{ buyers, setBuyers, param: buyers.length ? buyers.join(",") : undefined }}>{children}</Ctx.Provider>;
}

export const useScope = () => useContext(Ctx);
