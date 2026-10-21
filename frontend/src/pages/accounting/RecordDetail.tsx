import { FormEvent, useState } from "react";
import { useUnsavedChanges } from "../../components/Unsaved";
import { Link, useParams } from "react-router-dom";
import { api, dateFmt, label, money } from "../../api";
import { Badge, ErrorNotice, Icon, PageHead, Spinner } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";
import type { Dup, Entry, RecordBrief, RecordFull } from "../../types";
import { EntryPanel } from "./EntryPanel";

interface RecordView { record: RecordFull; entries: Entry[]; duplicates: Dup[]; related: (RecordBrief & { relation: string })[] }

const DOC_FIELDS: [keyof RecordFull, string][] = [
  ["party_name", "Party"], ["party_gstin", "GSTIN"], ["doc_number", "Number"], ["doc_date", "Date"],
  ["original_doc_number", "Original invoice"], ["note_side", "Note relates to"], ["place_of_supply", "Place of supply (state code)"],
  ["taxable_amount", "Taxable value"], ["cgst", "CGST"], ["sgst", "SGST"], ["igst", "IGST"], ["cess", "Cess"],
  ["total_amount", "Total"], ["category_hint", "Category"],
];
const BANK_FIELDS: [keyof RecordFull, string][] = [
  ["doc_date", "Date"], ["description", "Description"], ["reference", "Reference"], ["debit_amount", "Money out"],
  ["credit_amount", "Money in"], ["balance", "Balance"], ["bank_account_code", "Bank ledger code"],
];
const PROV_ALIAS: Record<string, string> = { total_amount: "total", taxable_amount: "taxable" };
const MONEY = new Set(["taxable_amount", "cgst", "sgst", "igst", "cess", "total_amount", "debit_amount", "credit_amount", "balance"]);

export function RecordDetail() {
  const { id } = useParams();
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const { data, error, loading, reload, setData } = useLoad(() => api<RecordView>(`${base}/records/${id}`), [id, company?.id]);
  const [editing, setEditing] = useState(false);

  if (loading && !data) return <Spinner />;
  if (error && !data) return <ErrorNotice error={error} onRetry={reload} />;
  if (!data) return null;
  const r = data.record;
  const isBank = r.record_type === "bank_txn";
  const fields = isBank ? BANK_FIELDS : DOC_FIELDS;
  const openEntry = data.entries.find((e) => ["needs_review", "validated", "approved", "posted"].includes(e.status)) ?? data.entries[0];
  const editable = can(company!.role, "prepare") && !["approved", "posted", "duplicate"].includes(r.status);
  const doc = r.document;
  const showPreview = doc && doc.has_file && ["application/pdf", "image/png", "image/jpeg"].includes(doc.content_type);

  return (
    <div className="stack">
      <PageHead title={isBank ? (r.description ?? "Bank line") : `${label(r.record_type)} ${r.doc_number ?? ""}`}
        subtitle={<><Link to="/accounting/transactions">Transactions</Link> · {isBank ? "Bank statement line" : r.party_name ?? "Party not identified"}</>}>
        <Badge status={r.status} />
      </PageHead>

      {r.review_question && r.status === "needs_review" && (
        <div className="notice warn"><Icon name="alert" /><div><strong>Question: </strong>{r.review_question}</div></div>
      )}

      {data.duplicates.filter((d) => d.status === "open").map((d) => (
        <div key={d.id} className="notice warn">
          <Icon name="copy" />
          <div className="grow"><strong>{d.kind === "possible_alteration" ? "Possible alteration" : `Possible duplicate (${d.strength})`}. </strong>{d.explanation}</div>
          <Link className="btn small" to="/accounting/duplicates">Review</Link>
        </div>
      ))}

      <div className="two-col">
        <div className="stack">
          {openEntry ? <EntryPanel entry={openEntry} companyId={company!.id} role={company!.role} onChange={reload} /> :
            <div className="card"><p className="muted">No entry has been proposed for this record.</p></div>}
          {data.entries.length > 1 && (
            <details><summary>Earlier entries ({data.entries.length - 1})</summary>
              <ul className="checks">{data.entries.filter((e) => e !== openEntry).map((e) => (
                <li key={e.id}><Badge status={e.status} /><div className="grow">Entry #{e.id} v{e.version} {e.rejection_reason && `— ${e.rejection_reason}`}</div></li>))}</ul>
            </details>
          )}
        </div>

        <div className="stack">
          <section className="card tight stack-sm">
            <div className="row-between"><h2>Extracted information</h2>
              {editable && !editing && <button className="btn small" onClick={() => setEditing(true)}>Correct data</button>}</div>
            {editing ? (
              <RecordEditForm record={r} fields={fields} base={base} onCancel={() => setEditing(false)}
                onSaved={(v) => { setData(v); setEditing(false); }} />
            ) : (
              <dl className="kv">
                {fields.map(([k, l]) => {
                  const v = r[k] as string | null;
                  const prov = r.provenance?.[k as string] ?? r.provenance?.[PROV_ALIAS[k as string] ?? ""];
                  return (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{l}</dt>
                      <dd>{v === null || v === undefined || v === "" ? <span className="faint">Not found</span> :
                        MONEY.has(k as string) ? money(v) : k === "doc_date" ? dateFmt(v) : String(v)}
                        {prov && prov.source && prov.source !== "csv" && prov.source !== "xlsx" &&
                          <span className="faint" style={{ fontSize: 12 }}> · {prov.source === "user_correction" ? "corrected by user" : `${prov.source}, confidence ${Math.round((prov.confidence ?? 0) * 100)}%`}</span>}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </section>

          {data.related.length > 0 && (
            <section className="card tight stack-sm">
              <h2>Related documents</h2>
              <ul className="checks">{data.related.map((x) => (
                <li key={x.id}><div className="grow"><Link to={`/accounting/transactions/${x.id}`}>{label(x.record_type)} {x.doc_number}</Link>
                  <div className="muted">{label(x.relation)} · {money(x.total_amount)}</div></div><Badge status={x.status} /></li>))}</ul>
            </section>
          )}

          {doc && (
            <section className="card tight stack-sm">
              <h2>Source document</h2>
              <p><span className="muted">File: </span>{doc.filename}{r.row_index ? <span className="muted"> · row {r.row_index}</span> : null}</p>
              {showPreview ? (doc.content_type === "application/pdf"
                ? <iframe title="Document preview" src={`${base}/documents/${doc.id}/file`} style={{ width: "100%", height: 420, border: "1px solid var(--border)", borderRadius: 6 }} />
                : <img alt="Uploaded document" src={`${base}/documents/${doc.id}/file`} style={{ border: "1px solid var(--border)", borderRadius: 6 }} />)
                : !doc.has_file ? <p className="muted">The file content has been deleted under the retention policy.</p> : null}
              <details><summary>Original values as imported</summary>
                <dl className="kv">{Object.entries(r.raw ?? {}).filter(([k]) => k !== "text_excerpt").map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd className="mono">{v === null ? "—" : String(v)}</dd></div>))}</dl>
                {typeof r.raw?.text_excerpt === "string" && <pre className="mono" style={{ whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>{r.raw.text_excerpt as string}</pre>}
              </details>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function RecordEditForm({ record, fields, base, onCancel, onSaved }: {
  record: RecordFull; fields: [keyof RecordFull, string][]; base: string; onCancel: () => void; onSaved: (v: RecordView) => void;
}) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map(([k]) => [k, (record[k] as string | null) ?? ""])));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prompt = useUnsavedChanges(!busy && fields.some(([k]) => vals[k as string] !== ((record[k] as string | null) ?? "")));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    const changed = Object.fromEntries(Object.entries(vals).filter(([k, v]) => v !== ((record[k as keyof RecordFull] as string | null) ?? "")));
    try { onSaved(await api<RecordView>(`${base}/records/${record.id}`, { method: "PATCH", body: { fields: changed } })); }
    catch (ex) { setErr((ex as Error).message); } finally { setBusy(false); }
  };
  return (
    <form className="stack-sm" onSubmit={submit}>
      {prompt}
      <ErrorNotice error={err} />
      <p className="muted" style={{ fontSize: 13 }}>Enter values exactly as they appear on the original document. FinV re-checks duplicates and re-proposes the entry after saving.</p>
      {fields.map(([k, l]) => (
        <label key={k} className="field">{l}
          {k === "note_side" ? (
            <select value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
              <option value="">Not specified</option><option value="sales">A sale (issued to a customer)</option><option value="purchase">A purchase (received from a supplier)</option>
            </select>
          ) : <input type={k === "doc_date" ? "date" : "text"} inputMode={MONEY.has(k as string) ? "decimal" : undefined}
            value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />}
        </label>
      ))}
      <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save and re-check"}</button>
        <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}
