import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, dateFmt, label, money } from "../../api";
import { Badge, Empty, ErrorNotice, PageHead, Pagination, Spinner } from "../../components/ui";
import { useLoad, useSession } from "../../session";
import type { Paged, RecordBrief } from "../../types";

const TYPES = ["", "bank_txn", "purchase_invoice", "sales_invoice", "credit_note", "debit_note"];
const STATUSES = ["", "needs_review", "validated", "approved", "posted", "rejected", "duplicate"];

export function amountOf(r: RecordBrief): string {
  if (r.record_type === "bank_txn") return r.debit_amount ? `-${r.debit_amount}` : r.credit_amount ?? "";
  return r.total_amount ?? "";
}

export function Transactions() {
  const { company } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const page = Number(params.get("page") || 1);
  const query = new URLSearchParams(params);
  query.set("page_size", "25");
  const { data, error, loading, reload } = useLoad(
    () => api<Paged<RecordBrief>>(`/api/companies/${company!.id}/records?${query.toString()}`), [company?.id, params.toString()]);

  const setParam = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    if (k !== "page") p.delete("page");
    setParams(p, { replace: true });
  };

  return (
    <div>
      <PageHead title="Transactions" subtitle="Every extracted invoice, note and bank line, with its review status." />
      <div className="panel row" style={{ marginBottom: 16 }}>
        <form className="row grow" onSubmit={(e) => { e.preventDefault(); setParam("q", q); }}>
          <label className="sr-only" htmlFor="tx-q">Search</label>
          <input id="tx-q" className="grow" placeholder="Search party, number, GSTIN, description…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn">Search</button>
        </form>
        <select aria-label="Document type" value={params.get("record_type") ?? ""} onChange={(e) => setParam("record_type", e.target.value)}>
          {TYPES.map((t) => <option key={t} value={t}>{t ? label(t.replace("txn", "transaction")) : "All types"}</option>)}
        </select>
        <select aria-label="Status" value={params.get("status") ?? ""} onChange={(e) => setParam("status", e.target.value)}>
          {STATUSES.map((t) => <option key={t} value={t}>{t ? label(t) : "All statuses"}</option>)}
        </select>
        <label className="row" style={{ gap: 6 }}>From<input type="date" aria-label="From date" value={params.get("date_from") ?? ""} onChange={(e) => setParam("date_from", e.target.value)} /></label>
        <label className="row" style={{ gap: 6 }}>To<input type="date" aria-label="To date" value={params.get("date_to") ?? ""} onChange={(e) => setParam("date_to", e.target.value)} /></label>
        <label className="checkbox"><input type="checkbox" checked={params.get("duplicates_only") === "true"} onChange={(e) => setParam("duplicates_only", e.target.checked ? "true" : "")} />Possible duplicates</label>
      </div>
      {params.get("document_id") && <div className="notice info" style={{ marginBottom: 12 }}>Showing records from document #{params.get("document_id")}. <button className="btn small" onClick={() => setParam("document_id", "")}>Show all</button></div>}
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && data.items.length === 0 ? (
        <div className="card"><Empty title="No transactions match">Upload documents from the Overview, or clear the filters.</Empty></div>
      ) : data && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Type</th><th>Party / description</th><th>Number / reference</th><th className="num">Amount</th><th>Status</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} className="clickable" tabIndex={0} onClick={() => nav(`/accounting/transactions/${r.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && nav(`/accounting/transactions/${r.id}`)}>
                    <td className="muted">{dateFmt(r.doc_date)}</td>
                    <td>{label(r.record_type === "bank_txn" ? "bank_line" : r.record_type)}</td>
                    <td><div>{r.party_name ?? r.description ?? "—"}</div>{r.party_gstin && <div className="faint mono">{r.party_gstin}</div>}</td>
                    <td className="mono">{r.doc_number ?? r.reference ?? "—"}</td>
                    <td className="num">{money(amountOf(r))}</td>
                    <td><div className="row" style={{ gap: 6 }}><Badge status={r.status} />{r.has_open_duplicate && <Badge status="warning">Possible duplicate</Badge>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageSize={25} total={data.total} onPage={(p) => setParam("page", String(p))} />
        </>
      )}
    </div>
  );
}
