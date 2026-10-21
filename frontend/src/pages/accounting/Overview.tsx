import { useNavigate } from "react-router-dom";
import { api, dateFmt, label } from "../../api";
import { UploadArea } from "../../components/UploadArea";
import { Badge, Empty, ErrorNotice, Spinner } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";
import type { Doc } from "../../types";

interface OverviewData {
  pending_review: number; validated: number; approved: number; posted: number; issues_found: number;
  possible_duplicates: number; recent_documents: Doc[];
}

export const ACCOUNTING_KINDS = [
  { value: "auto", label: "Detect from columns (CSV/XLSX)" },
  { value: "bank_statement", label: "Bank statement" },
  { value: "invoice_register", label: "Invoice / note register" },
  { value: "purchase_invoice", label: "Purchase invoice" },
  { value: "sales_invoice", label: "Sales invoice" },
  { value: "credit_note", label: "Credit note" },
  { value: "debit_note", label: "Debit note" },
];

export function AccountingOverview() {
  const { company, me } = useSession();
  const nav = useNavigate();
  const { data, error, loading, reload } = useLoad(() => api<OverviewData>(`/api/companies/${company!.id}/accounting/overview`), [company?.id]);
  const stats: [string, number | undefined, string][] = [
    ["Pending review", data?.pending_review, "/accounting/entries?status=needs_review"],
    ["Validated entries", data?.validated, "/accounting/entries?status=validated"],
    ["Issues found", data?.issues_found, "/accounting/entries?status=issues"],
    ["Possible duplicates", data?.possible_duplicates, "/accounting/duplicates"],
  ];
  return (
    <div className="stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div><h1>Good to see you, {me?.name.split(" ")[0]}</h1><p className="muted">{company!.name} · Accounting</p></div>
      </div>
      {can(company!.role, "prepare") ? (
        <UploadArea companyId={company!.id} workspace="accounting" kinds={ACCOUNTING_KINDS}
          title="Upload financial documents"
          subtitle="FinV will extract the data, prepare proposed entries and check them for errors."
          onUploaded={() => reload()} />
      ) : <div className="notice info">Your role can view records but not upload documents.</div>}
      <ErrorNotice error={error} onRetry={reload} />
      <div className="grid-stats">
        {stats.map(([l, v, to]) => (
          <button key={l} className="stat" onClick={() => nav(to)}>
            <div className="label">{l}</div>
            <div className="value">{loading && v === undefined ? "–" : v ?? 0}</div>
          </button>
        ))}
      </div>
      <section className="card tight stack-sm">
        <div className="row-between"><h2>Recent processing activity</h2>
          {data && data.posted + data.approved > 0 && <span className="muted">{data.approved} approved · {data.posted} posted</span>}</div>
        {loading && !data ? <Spinner /> : data && data.recent_documents.length === 0 ? (
          <Empty title="No documents yet" icon="upload">Upload a bank statement or an invoice register to get started.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>File</th><th>Type</th><th>Status</th><th className="num">Records</th><th>Uploaded</th></tr></thead>
              <tbody>
                {data?.recent_documents.map((d) => (
                  <tr key={d.id} className="clickable" tabIndex={0} onClick={() => nav(`/accounting/transactions?document_id=${d.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && nav(`/accounting/transactions?document_id=${d.id}`)}>
                    <td><div>{d.filename}</div>{d.status === "failed" && <div style={{ color: "var(--err)", fontSize: 13 }}>{d.error}</div>}
                      {d.status === "repeated_upload" && <div className="muted" style={{ fontSize: 13 }}>{d.notes[0]}</div>}</td>
                    <td>{label(d.kind)}</td><td><Badge status={d.status} /></td>
                    <td className="num">{d.record_count}</td><td className="muted">{dateFmt(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
