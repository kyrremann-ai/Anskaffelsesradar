const nb = "nb-NO";

export const int = (v: number | null | undefined) => (v == null ? "–" : Math.round(v).toLocaleString(nb));
export const pct = (v: number | null | undefined, d = 1) =>
  v == null ? "–" : `${v.toLocaleString(nb, { minimumFractionDigits: d, maximumFractionDigits: d })} %`;
export const signedPct = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : ""}${pct(v, 0)}`);

export function nok(v: number | null | undefined): string {
  if (v == null) return "–";
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toLocaleString(nb, { maximumFractionDigits: 1 })} mrd. kr`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString(nb, { maximumFractionDigits: 1 })} mill. kr`;
  return `${Math.round(v).toLocaleString(nb)} kr`;
}

export function date(s: string | null | undefined): string {
  if (!s) return "–";
  const d = new Date(s);
  return isNaN(+d) ? s : d.toLocaleDateString(nb, { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(s: string | null | undefined): string {
  if (!s) return "–";
  const d = new Date(s);
  return isNaN(+d) ? s : d.toLocaleString(nb, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];
