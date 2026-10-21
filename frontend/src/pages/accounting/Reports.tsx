import { useState } from "react";
import { ApiError } from "../../api";
import { api, dateFmt, label, money } from "../../api";
import { Badge, ErrorNotice, PageHead, Spinner } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";
import type { Event } from "../../types";

interface Summary {
  entries_by_status: Record<string, { count: number; debit: string; credit: string }>;
  open_errors_by_rule: Record<string, number>; processing_failures: number; possible_duplicates: number;
  reconciliation: Record<string, number>; note: string;
}

const EXPORTS: [string, string][] = [
  ["journal", "All entries (journal lines)"], ["posted-journal", "Posted journal"], ["pending-review", "Pending review"],
  ["validation-errors", "Validation findings"], ["duplicates", "Duplicate decisions"], ["reconciliation", "Bank reconciliation"],
  ["activity", "Audit trail"],
];

export function Reports() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const qs = new URLSearchParams({ ...(from ? { date_from: from } : {}), ...(to ? { date_to: to } : {}) }).toString();
  const sum = useLoad(() => api<Summary>(`${base}/reports/summary?${qs}`), [company?.id, qs]);
  const acts = useLoad(() => api<Event[]>(`${base}/activity?limit=50`), [company?.id]);
  const verify = useLoad(() => api<{ valid: boolean; events: number; broken_at_event_id?: number }>(`${base}/activity/verify`), [company?.id]);
  return (
    <div className="stack">
      <PageHead title="Reports" subtitle="Figures are calculated from stored records. Imported data, proposals, validated, approved and posted entries are kept separate.">
        <label className="row" style={{ gap: 6 }}>From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="row" style={{ gap: 6 }}>To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </PageHead>
      <ErrorNotice error={sum.error} onRetry={sum.reload} />
      {sum.loading && !sum.data ? <Spinner /> : sum.data && (
        <div className="two-col">
          <section className="card tight stack-sm">
            <h2>Entries by status</h2>
            <div className="table-wrap"><table>
              <thead><tr><th>Status</th><th className="num">Entries</th><th className="num">Debits</th><th className="num">Credits</th></tr></thead>
              <tbody>{["needs_review", "validated", "approved", "posted", "rejected"].map((s) => { const v = sum.data!.entries_by_status[s]; return (
                <tr key={s}><td><Badge status={s} /></td><td className="num">{v?.count ?? 0}</td><td className="num">{money(v?.debit ?? "0.00")}</td><td className="num">{money(v?.credit ?? "0.00")}</td></tr>); })}</tbody>
            </table></div>
          </section>
          <section className="card tight stack-sm">
            <h2>Exceptions</h2>
            <dl className="kv">
              <dt>Possible duplicates</dt><dd>{sum.data.possible_duplicates}</dd>
              <dt>Processing failures</dt><dd>{sum.data.processing_failures}</dd>
              <dt>Unmatched bank lines</dt><dd>{sum.data.reconciliation.unmatched_bank}</dd>
              <dt>Unmatched entries</dt><dd>{sum.data.reconciliation.unmatched_book}</dd>
              <dt>Open errors by rule</dt><dd>{Object.keys(sum.data.open_errors_by_rule).length === 0 ? "None" :
                Object.entries(sum.data.open_errors_by_rule).map(([r, n]) => `${r}: ${n}`).join(", ")}</dd>
            </dl>
          </section>
        </div>
      )}
      <section className="card tight stack-sm">
        <h2>Exports (CSV)</h2>
        <div className="row">{EXPORTS.map(([n, l]) => <a key={n} className="btn small" href={`${base}/reports/${n}.csv${n !== "activity" && qs ? "?" + qs : ""}`}>{l}</a>)}</div>
      </section>
      <TallySection base={base} canPost={can(company!.role, "post")} />
      <section className="card tight stack-sm">
        <div className="row-between"><h2>Audit trail</h2>
          {verify.data && (verify.data.valid ? <Badge status="pass">Chain verified · {verify.data.events} events</Badge> :
            <Badge status="error">Integrity check failed at event #{verify.data.broken_at_event_id}</Badge>)}</div>
        <p className="muted" style={{ fontSize: 13 }}>Every upload, correction, decision, approval and posting is recorded in an append-only, hash-chained log.</p>
        {acts.loading && !acts.data ? <Spinner /> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Object</th><th>Details</th></tr></thead>
            <tbody>{acts.data?.map((a) => (
              <tr key={a.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{dateFmt(a.created_at)}</td><td>{a.user_name ?? "FinV"}</td>
                <td>{label(a.action.replace(".", " "))}</td><td className="muted">{a.object_type ? `${label(a.object_type)} #${a.object_id}` : "—"}</td>
                <td className="muted" style={{ fontSize: 12, maxWidth: 360 }}>{summariseDetails(a.details)}</td></tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

function summariseDetails(d: Record<string, unknown>): string {
  const keep = ["filename", "kind", "records", "reason", "note", "version", "error", "decision", "result", "count", "key", "outcome", "from", "to"];
  return keep.filter((k) => d[k] !== undefined && d[k] !== null && d[k] !== "").map((k) => `${k}: ${typeof d[k] === "object" ? JSON.stringify(d[k]) : String(d[k])}`).join(" · ");
}

interface TallyStatus { pending: { id: number; entry_date: string | null; narration: string | null; voucher_type: string; amount: string }[];
  exported_count: number; default_url: string; default_company_name: string }

function TallySection({ base, canPost }: { base: string; canPost: boolean }) {
  const st = useLoad(() => api<TallyStatus>(`${base}/tally/status`), [base]);
  const [url, setUrl] = useState("");
  const [tc, setTc] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; lines?: string[] } | null>(null);
  if (!st.data) return st.error ? <ErrorNotice error={st.error} onRetry={st.reload} /> : null;
  const d = st.data;
  const company = tc || d.default_company_name;
  const q = `?tally_company=${encodeURIComponent(company)}`;
  const push = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ message: string }>(`${base}/tally/push`, { body: { url: url || d.default_url, tally_company: company } });
      setMsg({ ok: true, text: r.message }); st.reload();
    } catch (e) {
      const t = (e as ApiError).details?.tally as { vouchers_result?: { messages?: string[] } } | undefined;
      setMsg({ ok: false, text: (e as Error).message, lines: t?.vouchers_result?.messages });
    } finally { setBusy(false); }
  };
  const mark = async () => {
    setBusy(true);
    try { await api(`${base}/tally/mark-exported`, { body: { entry_ids: d.pending.map((p) => p.id) } }); setMsg({ ok: true, text: "Marked as exported." }); st.reload(); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  };
  return (
    <section className="card tight stack-sm">
      <h2>Tally</h2>
      <p className="muted">Posted entries become Tally vouchers (Purchase, Sales, Payment, Receipt, Credit/Debit Note or Journal). Party lines use party ledgers under Sundry Creditors/Debtors. Each entry is sent once.</p>
      <p><strong>{d.pending.length}</strong> posted entr{d.pending.length === 1 ? "y" : "ies"} waiting · {d.exported_count} already exported</p>
      {canPost && d.pending.length > 0 && (
        <>
          <label className="field">Company name in Tally<span className="hint">Must match the company loaded in Tally exactly.</span>
            <input value={tc} placeholder={d.default_company_name} onChange={(e) => setTc(e.target.value)} /></label>
          <h3>Option 1 · Import a file</h3>
          <div className="row">
            <a className="btn small" href={`${base}/tally/masters.xml${q}`}>1. Download ledgers (masters)</a>
            <a className="btn small" href={`${base}/tally/vouchers.xml${q}`}>2. Download vouchers</a>
            <button className="btn small" disabled={busy} onClick={mark}>3. Mark as exported</button>
          </div>
          <p className="faint" style={{ fontSize: 12 }}>In TallyPrime: Import → Masters (ledgers file), then Import → Transactions (vouchers file). Mark as exported only after Tally accepts them.</p>
          <h3>Option 2 · Send directly</h3>
          <div className="row">
            <input aria-label="Tally address" className="grow" placeholder={d.default_url} value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="btn primary small" disabled={busy} onClick={push}>{busy ? "Sending…" : "Send to Tally"}</button>
          </div>
          <p className="faint" style={{ fontSize: 12 }}>TallyPrime must be open with the company loaded and its HTTP server enabled (F1 Help → Settings → Connectivity, port 9000). Only this machine or your local network is allowed.</p>
        </>
      )}
      {msg && <div className={`notice ${msg.ok ? "ok" : "err"}`} role="status"><div>{msg.text}{msg.lines && msg.lines.length > 0 && <ul>{msg.lines.map((l) => <li key={l}>{l}</li>)}</ul>}</div></div>}
    </section>
  );
}
