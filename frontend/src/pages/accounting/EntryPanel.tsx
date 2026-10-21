import { useEffect, useRef, useState } from "react";
import { useUnsavedChanges } from "../../components/Unsaved";
import { api, ApiError, label, money } from "../../api";
import { Badge, CheckList, Dialog, ErrorNotice, Icon } from "../../components/ui";
import { can, useLoad } from "../../session";
import type { Account, Entry, Line } from "../../types";

const OPEN = new Set(["needs_review", "validated"]);

export function useAccounts(companyId: number) {
  return useLoad(() => api<Account[]>(`/api/companies/${companyId}/accounts`), [companyId]);
}

/** Proposed entry: explanation, lines, validation and the review actions. */
export function EntryPanel({ entry, companyId, role, onChange }: { entry: Entry; companyId: number; role: string | null; onChange: () => void }) {
  const base = `/api/companies/${companyId}`;
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errList, setErrList] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [dialog, setDialog] = useState<null | "approve" | "reject">(null);
  const ex = entry.explanation || {};
  const errors = (entry.validation ?? []).filter((c) => c.status === "error").length;
  const reviews = (entry.validation ?? []).filter((c) => c.status === "needs_review").length;

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setErr(null); setErrList([]);
    try { await fn(); onChange(); }
    catch (e) {
      setErr((e as Error).message);
      const d = (e as ApiError).details;
      if (d) setErrList([...((d.errors as string[]) ?? []), ...((d.review_points as string[]) ?? [])]);
    } finally { setBusy(null); }
  };

  return (
    <section className="card stack" aria-label="Proposed entry">
      <div className="row-between">
        <div className="row"><h2>{entry.status === "posted" ? "Posted entry" : entry.status === "approved" ? "Approved entry" : "Proposed entry"}</h2>
          <Badge status={entry.status} /><span className="faint" style={{ fontSize: 12 }}>#{entry.id} · v{entry.version}{entry.origin !== "engine" ? ` · ${label(entry.origin)}` : ""}</span></div>
      </div>

      {ex.summary && <p>{ex.summary}</p>}
      {ex.question && OPEN.has(entry.status) && <div className="notice warn"><Icon name="alert" /><div>{ex.question}</div></div>}
      {entry.rejection_reason && <div className="notice err">Rejected: {entry.rejection_reason}</div>}

      {editing ? (
        <LineEditor entry={entry} companyId={companyId} onCancel={() => setEditing(false)}
          onSaved={() => { setEditing(false); onChange(); }} />
      ) : entry.lines.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Ledger</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead>
            <tbody>
              {entry.lines.map((l, i) => (
                <tr key={l.id ?? i}>
                  <td>{l.account_code ? <><span className="mono faint">{l.account_code}</span> {l.account_name}</> : <span style={{ color: "var(--err)" }}>No ledger selected</span>}
                    {l.description && <div className="faint" style={{ fontSize: 12 }}>{l.description}</div>}</td>
                  <td className="num">{Number(l.debit) ? money(l.debit) : ""}</td>
                  <td className="num">{Number(l.credit) ? money(l.credit) : ""}</td>
                </tr>
              ))}
              <tr><td className="muted">Total</td><td className="num"><strong>{money(entry.total_debit)}</strong></td><td className="num"><strong>{money(entry.total_credit)}</strong></td></tr>
            </tbody>
          </table>
        </div>
      ) : <div className="panel muted">No lines yet. {can(role, "prepare") && OPEN.has(entry.status) ? "Choose the ledgers to continue." : ""}</div>}

      <div className="stack-sm">
        <div className="row-between"><h3>Checks</h3>
          <span className="muted" style={{ fontSize: 13 }}>{errors} error{errors === 1 ? "" : "s"} · {reviews} to review</span></div>
        <CheckList checks={entry.validation ?? []} />
        {entry.status === "validated" && <p className="faint" style={{ fontSize: 12 }}>Validated means FinV's checks found no errors or open questions against the recorded evidence. It is not a guarantee; review before approving.</p>}
      </div>

      {(ex.accounts?.length || ex.uncertainties?.length) ? (
        <details>
          <summary>Why FinV proposed this</summary>
          <div className="stack-sm">
            {ex.accounts?.map((a) => <p key={a.code}><span className="mono faint">{a.code}</span> <strong>{a.name}</strong>: {a.reason}</p>)}
            {ex.policy && <p className="muted">Policy applied: {ex.policy.key} (version {ex.policy.version})</p>}
            {ex.uncertainties && ex.uncertainties.length > 0 && <><h3>Still uncertain</h3><ul>{ex.uncertainties.map((u) => <li key={u}>{u}</li>)}</ul></>}
            {ex.next_action && <p><strong>Next step: </strong>{ex.next_action}</p>}
            {ex.correction_note && <p className="muted">Correction note: {ex.correction_note}</p>}
          </div>
        </details>
      ) : null}
      {ex.evidence && ex.evidence.length > 0 && (
        <details><summary>Source fields used</summary>
          <dl className="kv">{ex.evidence.filter((e) => e.value).map((e) => <div key={e.field} style={{ display: "contents" }}><dt>{label(e.field)}</dt><dd>{e.value} <span className="faint">({e.source})</span></dd></div>)}</dl>
        </details>
      )}

      <ErrorNotice error={err} />
      {errList.length > 0 && <ul className="muted">{errList.map((x) => <li key={x}>{x}</li>)}</ul>}

      {!editing && (
        <div className="row">
          {OPEN.has(entry.status) && can(role, "prepare") && <button className="btn" onClick={() => setEditing(true)}>{entry.lines.length ? "Correct entry" : "Choose ledgers"}</button>}
          {OPEN.has(entry.status) && can(role, "approve") && <button className="btn primary" disabled={!!busy || errors > 0} title={errors ? "Fix errors before approving" : undefined} onClick={() => setDialog("approve")}>Approve</button>}
          {entry.status === "approved" && can(role, "post") && <button className="btn primary" disabled={!!busy} onClick={() => act("post", () => api(`${base}/entries/${entry.id}/post`, { method: "POST" }))}>{busy === "post" ? "Posting…" : "Post to ledger"}</button>}
          {(OPEN.has(entry.status) || entry.status === "approved") && can(role, "approve") && <button className="btn danger" onClick={() => setDialog("reject")}>Reject</button>}
          {OPEN.has(entry.status) && can(role, "prepare") && <button className="btn ghost" disabled={!!busy} onClick={() => act("revalidate", () => api(`${base}/entries/${entry.id}/revalidate`, { method: "POST" }))}>{busy === "revalidate" ? "Checking…" : "Re-run checks"}</button>}
          {OPEN.has(entry.status) && entry.treatment?.startsWith("bank") && entry.source_record_id && can(role, "prepare") && entry.lines.length === 0 &&
            <button className="btn ghost" disabled={!!busy} onClick={() => act("ai", () => api(`${base}/records/${entry.source_record_id}/ai-suggest`, { method: "POST" }))}>{busy === "ai" ? "Asking…" : "Suggest with AI"}</button>}
          {entry.status === "rejected" && entry.source_record_id && can(role, "prepare") && <button className="btn" onClick={() => act("repropose", () => api(`${base}/records/${entry.source_record_id}/repropose`, { method: "POST" }))}>Propose again</button>}
        </div>
      )}

      {dialog === "approve" && <ApproveDialog entry={entry} reviews={reviews} onClose={() => setDialog(null)}
        onConfirm={(note) => { setDialog(null); act("approve", () => api(`${base}/entries/${entry.id}/approve`, { body: { acknowledge_review: reviews > 0, note } })); }} />}
      {dialog === "reject" && <RejectDialog onClose={() => setDialog(null)}
        onConfirm={(reason) => { setDialog(null); act("reject", () => api(`${base}/entries/${entry.id}/reject`, { body: { reason } })); }} />}
    </section>
  );
}

function ApproveDialog({ entry, reviews, onClose, onConfirm }: { entry: Entry; reviews: number; onClose: () => void; onConfirm: (note: string | null) => void }) {
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const points = (entry.validation ?? []).filter((c) => c.status === "needs_review");
  return (
    <Dialog title="Approve entry" onClose={onClose}>
      <div className="stack">
        {reviews > 0 ? (
          <>
            <p>This entry has {reviews} open review point{reviews === 1 ? "" : "s"}:</p>
            <ul>{points.map((p) => <li key={p.rule_id}>{p.message}</li>)}</ul>
            <label className="checkbox"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />I have reviewed these points and the entry is supported by the evidence.</label>
            <label className="field">Explanation (required)<textarea value={note} onChange={(e) => setNote(e.target.value)} /></label>
          </>
        ) : <p>The entry passed all applicable checks. Approving records your decision in the audit trail.</p>}
        <div className="row"><button className="btn primary" disabled={reviews > 0 && (!ack || !note.trim())} onClick={() => onConfirm(note.trim() || null)}>Approve</button>
          <button className="btn ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </Dialog>
  );
}

function RejectDialog({ onClose, onConfirm }: { onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Dialog title="Reject entry" onClose={onClose}>
      <div className="stack">
        <label className="field">Reason<textarea value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <div className="row"><button className="btn danger" disabled={!reason.trim()} onClick={() => onConfirm(reason.trim())}>Reject</button>
          <button className="btn ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </Dialog>
  );
}

function LineEditor({ entry, companyId, onCancel, onSaved }: { entry: Entry; companyId: number; onCancel: () => void; onSaved: () => void }) {
  const accounts = useAccounts(companyId);
  const amountGuess = entry.source_record?.total_amount ?? entry.source_record?.debit_amount ?? entry.source_record?.credit_amount ?? "";
  const [lines, setLines] = useState<Line[]>(entry.lines.length ? entry.lines.map((l) => ({ ...l })) :
    [{ account_id: null, debit: amountGuess || "0", credit: "0" }, { account_id: null, debit: "0", credit: amountGuess || "0" }]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const initial = useRef(JSON.stringify(lines));
  const prompt = useUnsavedChanges(!busy && (JSON.stringify(lines) !== initial.current || !!note));
  useEffect(() => { setErr(null); }, [lines]);
  const toCents = (v: string) => Math.round(Number(v || 0) * 100);
  const dr = lines.reduce((a, l) => a + toCents(l.debit), 0);
  const cr = lines.reduce((a, l) => a + toCents(l.credit), 0);
  const set = (i: number, patch: Partial<Line>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const save = async () => {
    setBusy(true);
    try {
      await api(`/api/companies/${companyId}/entries/${entry.id}`, { method: "PUT", body: {
        lines: lines.map((l) => ({ account_id: l.account_id, debit: l.debit || "0", credit: l.credit || "0", description: l.description ?? null })),
        note: note || null } });
      onSaved();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="stack-sm">
      {prompt}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Ledger</th><th className="num">Debit</th><th className="num">Credit</th><th><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td><select aria-label={`Ledger for line ${i + 1}`} value={l.account_id ?? ""} style={{ width: "100%" }}
                  onChange={(e) => set(i, { account_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Select ledger…</option>
                  {accounts.data?.filter((a) => a.is_active || a.id === l.account_id).map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}
                </select></td>
                <td><input aria-label={`Debit for line ${i + 1}`} className="num" inputMode="decimal" style={{ width: 120 }} value={l.debit} onChange={(e) => set(i, { debit: e.target.value })} /></td>
                <td><input aria-label={`Credit for line ${i + 1}`} className="num" inputMode="decimal" style={{ width: 120 }} value={l.credit} onChange={(e) => set(i, { credit: e.target.value })} /></td>
                <td><button className="btn ghost small" aria-label={`Remove line ${i + 1}`} onClick={() => setLines(lines.filter((_, j) => j !== i))}><Icon name="x" /></button></td>
              </tr>
            ))}
            <tr><td><button className="btn small" onClick={() => setLines([...lines, { account_id: null, debit: "0", credit: "0" }])}>Add line</button></td>
              <td className="num">{money((dr / 100).toFixed(2))}</td><td className="num">{money((cr / 100).toFixed(2))}</td><td /></tr>
          </tbody>
        </table>
      </div>
      {dr !== cr && <div className="notice warn">Debits and credits differ by {money(((dr - cr) / 100).toFixed(2))}. You can still save; FinV will report the difference.</div>}
      <label className="field">Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why this treatment" /></label>
      <ErrorNotice error={err} />
      <div className="row"><button className="btn primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save and re-check"}</button>
        <button className="btn ghost" onClick={onCancel}>Cancel</button></div>
    </div>
  );
}
