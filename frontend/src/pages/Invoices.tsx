import { Link } from "react-router-dom";
import { CheckBadge, Empty, PageHead, Skeleton, cx } from "../components/ui";
import { useApi, type InvoiceResult } from "../lib/api";
import { date, int, nok } from "../lib/format";
import { useScope } from "../lib/scope";

export default function Invoices() {
  const { param: buyers } = useScope();
  const { data } = useApi<InvoiceResult>("/api/invoices", { buyers });
  if (!data) return <Skeleton className="h-96" />;
  const flagged = data.rows.filter((r) => r.checks.length).length;
  return (
    <div>
      <PageHead title="Fakturakontroll"
        desc="Kjøp fra kundens egne fakturaer, summert per leverandør og sammenlignet med kontraktene radaren kjenner fra TED og Doffin. Her blir direkte kjøp og kjøp under terskel synlige." />
      {!data.rows.length ? (
        <Empty>Ingen fakturadata lastet inn. Legg en CSV- eller XLSX-fil med leverandørens orgnr, beløp ekskl. mva, fakturadato og konto
          i importmappen (faktura_&lt;orgnr&gt;.csv). Dataene blir i kundens database og sendes aldri til en språkmodell.</Empty>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted">
            {int(data.rows.length)} leverandører, fakturert {date(data.period?.[0])} til {date(data.period?.[1])}. {int(flagged)} har minst ett kontrollpunkt.
            Kontrakter kunngjort før {date(data.radar_from)} er ikke med i radaren. Terskelverdien gjelder samlet verdi for hele kontraktsperioden,
            og kjøpet kan være dekket av en avtale utenfor radaren eller et unntak. Kontrollpunktene er utgangspunkt for kontroll, ikke konklusjoner.
          </p>
          <div className="overflow-x-auto rounded-md border border-line bg-surface">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="bg-sunk text-left text-xs text-muted">
                <tr>{["Leverandør", "Fakturaer", "Sum", "Høyeste 12 mnd", "Kunngjorte kontrakter", "Kontrollpunkter"].map((h, i) =>
                  <th key={h} className={cx("whitespace-nowrap px-3 py-2.5 font-medium", i > 0 && i < 4 && "text-right")}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={`${r.buyer_id}-${r.orgnr}`} className="border-t border-line align-top hover:bg-sunk">
                    <td className="px-3 py-2">
                      <Link to={`/leverandorer/${r.orgnr}`} className="font-medium hover:underline">{r.name ?? r.orgnr}</Link>
                      <span className="block text-xs text-muted">{r.orgnr}{r.public && " · offentlig enhet"} · {r.accounts.map((a) => a.account).join(", ")}</span>
                    </td>
                    <td className="num px-3 py-2 text-right">{int(r.n)}<span className="block text-xs text-muted">{date(r.first)}–{date(r.last)}</span></td>
                    <td className="num px-3 py-2 text-right">{nok(r.total)}</td>
                    <td className="num px-3 py-2 text-right">{nok(r.max_12m)}</td>
                    <td className="px-3 py-2">{r.contracts.length ? r.contracts.map((c) => (
                      <Link key={c.id} to={`/arbeid?id=${encodeURIComponent(c.id)}${c.type === "intensjon" ? "&flag=type:intensjon" : ""}`} className="block truncate hover:underline" title={c.title}>
                        {c.type === "intensjon" ? "Intensjon" : "Kontrakt"} {date(c.pub_date)} · {nok(c.value)}</Link>))
                      : <span className="text-muted">Ingen</span>}</td>
                    <td className="px-3 py-2"><span className="flex flex-wrap gap-1">{r.checks.map((x) => <CheckBadge key={x.key} label={x.label} why={x.why} />)}</span></td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
