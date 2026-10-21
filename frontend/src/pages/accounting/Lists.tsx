import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, dateFmt, label, money } from "../../api";
import { Badge, Dialog, Empty, ErrorNotice, Icon, PageHead, Pagination, Spinner, TabList } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";
import type { Dup, Entry, Paged, RecordBrief } from "../../types";
import { amountOf } from "./Transactions";

const FIELD_NAMES: Record<string, string> = { cgst: "CGST", sgst: "SGST", igst: "IGST", cess: "Cess", taxable: "Taxable value",
  total: "Total", doc_date: "Date", party_gstin: "GSTIN", party_name: "Party name" };

const ENTRY_TABS: [string, string][] = [["needs_review", "Pending review"], ["validated", "Validated"], ["issues", "Issues"], ["approved", "Approved"],
  ["posted", "Posted"], ["rejected", "Rejected"], ["", "All"]];

export function Entries() {
  const { company } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "needs_review";
  const page = Number(params.get("page") || 1);
  const { data, error, loading, reload } = useLoad(
    () => api<Paged<Entry>>(`/api/companies/${company!.id}/entries?status=${status}&page=${page}&page_size=25`), [company?.id, status, page]);
  return (
    <div>
      <PageHead title="Entries" subtitle="Proposed, validated, approved and posted journal entries." />
      <TabList items={ENTRY_TABS} value={status} label="Entry status" onChange={(v) => setParams(v ? { status: v } : { status: "" }, { replace: true })} />
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && data.items.length === 0 ? (
        <div className="card"><Empty title="No entries here">{status === "needs_review" ? "Nothing is waiting for review." : "Entries appear after documents are uploaded."}</Empty></div>
      ) : data && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Narration</th><th>Treatment</th><th className="num">Amount</th><th>Checks</th><th>Status</th></tr></thead>
              <tbody>
                {data.items.map((e) => {
                  const c = e.validation_counts ?? {};
                  const go = () => e.source_record_id ? nav(`/accounting/transactions/${e.source_record_id}`) : undefined;
                  return (
                    <tr key={e.id} className="clickable" tabIndex={0} onClick={go} onKeyDown={(ev) => ev.key === "Enter" && go()}>
                      <td className="muted">{dateFmt(e.entry_date)}</td>
                      <td>{e.narration}{e.explanation?.question && ["needs_review"].includes(e.status) && <div className="muted" style={{ fontSize: 13 }}>{e.explanation.question}</div>}</td>
                      <td className="muted">{label(e.treatment)}</td>
                      <td className="num">{money(e.total_debit)}</td>
                      <td>{c.error ? <Badge status="error">{c.error} error{c.error > 1 ? "s" : ""}</Badge> : c.needs_review ? <Badge status="needs_review">{c.needs_review} to review</Badge> : <Badge status="pass">Passed</Badge>}</td>
                      <td><Badge status={e.status} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageSize={25} total={data.total} onPage={(p) => setParams({ status, page: String(p) }, { replace: true })} />
        </>
      )}
    </div>
  );
}

export function Duplicates() {
  const { company } = useSession();
  const [status, setStatus] = useState("open");
  const { data, error, loading, reload } = useLoad(() => api<Dup[]>(`/api/companies/${company!.id}/duplicates?status=${status}`), [company?.id, status]);
  const [deciding, setDeciding] = useState<{ dup: Dup; decision: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const decide = async (note: string) => {
    if (!deciding) return;
    try {
      await api(`/api/companies/${company!.id}/duplicates/${deciding.dup.id}/decision`, { body: { decision: deciding.decision, note: note || null } });
      setDeciding(null); reload();
    } catch (e) { setErr((e as Error).message); setDeciding(null); }
  };
  return (
    <div>
      <PageHead title="Duplicates" subtitle="Possible duplicate and inconsistent documents. FinV never deletes records; you decide." />
      <TabList items={([["open", "To review"], ["confirmed_duplicate", "Confirmed duplicates"], ["not_duplicate", "Not duplicates"], ["all", "All"]] as [string, string][])} value={status} label="Duplicate status" onChange={setStatus} />
      <ErrorNotice error={error ?? err} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && data.length === 0 ? (
        <div className="card"><Empty title="No possible duplicates" icon="copy">FinV checks every new document against earlier records.</Empty></div>
      ) : (
        <div className="stack">
          {data?.map((d) => (
            <section key={d.id} className="card tight stack-sm">
              <div className="row-between">
                <div className="row">
                  <Badge status={d.kind === "possible_alteration" ? "error" : d.strength === "possible" ? "proposed" : "warning"}>
                    {d.kind === "possible_alteration" ? "Possible alteration" : `${label(d.strength)} duplicate`}</Badge>
                  <span className="faint" style={{ fontSize: 12 }}>match strength {d.score}/100</span>
                </div>
                {d.status !== "open" && <Badge status={d.status} />}
              </div>
              <p>{d.explanation}</p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th></th><th>Record</th><th>Party</th><th>Number / reference</th><th>Date</th><th className="num">Amount</th><th>Status</th></tr></thead>
                  <tbody>
                    {([["New", d.record], ["Earlier", d.matched_record]] as [string, RecordBrief | null][]).map(([l, r]) => r && (
                      <tr key={l}><td className="muted">{l}</td><td><Link to={`/accounting/transactions/${r.id}`}>#{r.id}</Link></td>
                        <td>{r.party_name ?? r.description ?? "—"}</td><td className="mono">{r.doc_number ?? r.reference ?? "—"}</td>
                        <td>{dateFmt(r.doc_date)}</td><td className="num">{money(amountOf(r))}</td><td><Badge status={r.status} /></td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {Array.isArray(d.signals.differences) && (
                <div className="notice warn"><Icon name="alert" /><div>Fields that differ: {(d.signals.differences as { field: string; new: string; existing: string }[]).map((x) => `${FIELD_NAMES[x.field] ?? label(x.field)} (${x.new} vs ${x.existing})`).join("; ")}</div></div>
              )}
              {typeof d.signals.recommended_action === "string" && <p className="muted">Recommended: {d.signals.recommended_action}</p>}
              {typeof d.signals.decision_warning === "string" && <div className="notice warn">{d.signals.decision_warning}</div>}
              {d.decision_note && <p className="muted">Decision note: {d.decision_note}</p>}
              {d.status === "open" && (
                <div className="row">
                  {can(company!.role, "prepare") && <button className="btn" onClick={() => setDeciding({ dup: d, decision: "confirmed_duplicate" })}>Confirm duplicate</button>}
                  {can(company!.role, "approve") && <button className="btn" onClick={() => setDeciding({ dup: d, decision: "not_duplicate" })}>Separate transactions</button>}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
      {deciding && <NoteDialog title={deciding.decision === "confirmed_duplicate" ? "Confirm duplicate" : "Mark as separate transactions"}
        text={deciding.decision === "confirmed_duplicate" ? "The newer record will be excluded and its proposed entry rejected. Nothing is deleted." :
          "Both records stay active and the duplicate warning is cleared."}
        onClose={() => setDeciding(null)} onConfirm={decide} />}
    </div>
  );
}

function NoteDialog({ title, text, onClose, onConfirm }: { title: string; text: string; onClose: () => void; onConfirm: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <Dialog title={title} onClose={onClose}>
      <div className="stack"><p>{text}</p>
        <label className="field">Note (optional)<textarea value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="row"><button className="btn primary" onClick={() => onConfirm(note)}>Confirm</button><button className="btn ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </Dialog>
  );
}

interface Recon {
  matched: { bank_record_id: number; entry_id: number; method: string; score: number | null }[];
  ambiguous: { bank_record_id: number; candidate_entry_ids: number[]; reason: string }[];
  unmatched_bank: number[]; unmatched_book: number[]; near_misses: { bank_record_id: number; entry_id: number; difference: string }[];
  possible_transfers: { bank_record_ids: number[]; amount: string }[];
  confirmed_matches: { id: number; bank_record_id: number; entry_id: number; method: string }[];
  records: Record<string, RecordBrief>; entries: Record<string, { id: number; entry_date: string | null; narration: string | null; status: string; total_debit: string }>;
  summary: Record<string, number>; rules_version: string;
}

export function Bank() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const { data, error, loading, reload } = useLoad(() => api<Recon>(`${base}/bank/reconciliation`), [company?.id]);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<{ bank?: number; entry?: number }>({});
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); setPick({}); reload(); } catch (e) { setErr((e as Error).message); } };
  const rec = (id: number) => data?.records[String(id)];
  const ent = (id: number) => data?.entries[String(id)];
  const confirmedFor = (b: number) => data?.confirmed_matches.find((m) => m.bank_record_id === b);
  return (
    <div className="stack">
      <PageHead title="Bank reconciliation" subtitle="Statement lines compared with approved and posted entries on the bank ledger. Equal amounts alone never prove a match.">
        {can(company!.role, "prepare") && data && data.matched.some((m) => m.method === "auto") &&
          <button className="btn" onClick={() => run(() => api(`${base}/bank/confirm-auto`, { method: "POST" }))}>Confirm automatic matches</button>}
        <a className="btn" href={`${base}/reports/reconciliation.csv`}>Export CSV</a>
      </PageHead>
      <ErrorNotice error={error ?? err} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && (
        <>
          <div className="grid-stats">
            {[["Matched", data.summary.matched], ["Unmatched bank lines", data.summary.unmatched_bank], ["Unmatched entries", data.summary.unmatched_book], ["Need manual match", data.summary.ambiguous]].map(([l, v]) =>
              <div key={l as string} className="stat" style={{ cursor: "default" }}><div className="label">{l}</div><div className="value">{v}</div></div>)}
          </div>
          {data.summary.matched + data.summary.unmatched_bank + data.summary.unmatched_book === 0 && (
            <div className="card"><Empty title="Nothing to reconcile yet" icon="bank">Upload a bank statement and approve its entries to reconcile.</Empty></div>
          )}
          {(data.unmatched_bank.length > 0 || data.ambiguous.length > 0) && (
            <section className="card tight stack-sm">
              <h2>Unmatched bank lines</h2>
              <p className="muted">An unmatched line is not necessarily an error: its entry may not be approved yet. Select a line and an entry to match them manually.</p>
              <div className="table-wrap"><table>
                <thead><tr><th></th><th>Date</th><th>Description</th><th className="num">Amount</th><th>Note</th></tr></thead>
                <tbody>{[...data.unmatched_bank, ...data.ambiguous.map((a) => a.bank_record_id)].map((id) => {
                  const r = rec(id); const amb = data.ambiguous.find((a) => a.bank_record_id === id);
                  const near = data.near_misses.find((n) => n.bank_record_id === id);
                  return r && (
                    <tr key={id}><td><input type="radio" name="bank" aria-label={`Select bank line ${id}`} checked={pick.bank === id} onChange={() => setPick({ ...pick, bank: id })} /></td>
                      <td>{dateFmt(r.doc_date)}</td><td><Link to={`/accounting/transactions/${id}`}>{r.description}</Link><div className="faint mono">{r.reference}</div></td>
                      <td className="num">{money(amountOf(r))}</td>
                      <td className="muted" style={{ fontSize: 13 }}>{amb ? `${amb.reason} Candidates: ${amb.candidate_entry_ids.map((e) => "#" + e).join(", ")}` :
                        near ? `Entry #${near.entry_id} differs by ${money(near.difference)}` : r.status === "needs_review" ? "Entry not yet approved" : ""}</td></tr>);
                })}</tbody>
              </table></div>
            </section>
          )}
          {data.unmatched_book.length > 0 && (
            <section className="card tight stack-sm">
              <h2>Entries without a bank line</h2>
              <div className="table-wrap"><table>
                <thead><tr><th></th><th>Date</th><th>Narration</th><th className="num">Amount</th><th>Status</th></tr></thead>
                <tbody>{data.unmatched_book.map((id) => { const e = ent(id); return e && (
                  <tr key={id}><td><input type="radio" name="entry" aria-label={`Select entry ${id}`} checked={pick.entry === id} onChange={() => setPick({ ...pick, entry: id })} /></td>
                    <td>{dateFmt(e.entry_date)}</td><td>{e.narration}</td><td className="num">{money(e.total_debit)}</td><td><Badge status={e.status} /></td></tr>); })}</tbody>
              </table></div>
            </section>
          )}
          {pick.bank && pick.entry && can(company!.role, "prepare") && (
            <div className="notice info"><div className="grow">Match bank line #{pick.bank} with entry #{pick.entry}?</div>
              <button className="btn primary small" onClick={() => run(() => api(`${base}/bank/matches`, { body: { bank_record_id: pick.bank, entry_id: pick.entry } }))}>Match</button></div>
          )}
          {data.possible_transfers.length > 0 && <div className="notice info">Possible transfers between your own bank accounts: {data.possible_transfers.map((t) => `lines ${t.bank_record_ids.join(" & ")} (${money(t.amount)})`).join("; ")}</div>}
          {data.matched.length > 0 && (
            <details><summary>Matched ({data.matched.length})</summary>
              <div className="table-wrap"><table>
                <thead><tr><th>Bank line</th><th>Entry</th><th className="num">Amount</th><th>How</th><th></th></tr></thead>
                <tbody>{data.matched.map((m) => { const r = rec(m.bank_record_id); const c = confirmedFor(m.bank_record_id); return (
                  <tr key={m.bank_record_id}><td>{r?.description}</td><td>#{m.entry_id} {ent(m.entry_id)?.narration}</td><td className="num">{r && money(amountOf(r))}</td>
                    <td className="muted">{m.method === "auto" ? `Automatic (score ${m.score})` : "Confirmed"}</td>
                    <td>{c && can(company!.role, "prepare") && <button className="btn ghost small" onClick={() => run(() => api(`${base}/bank/matches/${c.id}`, { method: "DELETE" }))}>Unmatch</button>}</td></tr>); })}</tbody>
              </table></div>
            </details>
          )}
          <p className="faint" style={{ fontSize: 12 }}>Matching rules version {data.rules_version}. Results are recomputed from stored records each time.</p>
        </>
      )}
    </div>
  );
}
