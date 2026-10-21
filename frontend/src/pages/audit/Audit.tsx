import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, dateFmt, label, money } from "../../api";
import { UploadArea } from "../../components/UploadArea";
import { Badge, Empty, ErrorNotice, Help, PageHead, Pagination, Spinner, TabList } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";
import type { AuditException, Event, LedgerEntry, Paged, RecordBrief, TBImport, Vouching } from "../../types";

interface AuditOverviewData {
  imports: TBImport[]; exceptions_by_status: Record<string, number>; open_by_severity: Record<string, number>;
  awaiting_review: number; vouching: { linked: number; discrepancies: number; pending_review: number }; recent_activity: Event[];
}

const AUDIT_KINDS = [{ value: "trial_balance", label: "Trial balance" }, { value: "ledger", label: "Ledger / day book" }, { value: "auto", label: "Detect from columns" }];

interface TallyResult { ok: boolean; vouchers_read: number; lines_read: number; lines_added: number; rejected: number; months_without_vouchers: string[]; synced_at: string; notes: string[]; status: string }
interface TallyStatus { last_success: string | null; attempts: { at: string; ok: boolean; details: Record<string, unknown> }[] }

/** Pull the Day Book from a running TallyPrime. Every outcome is shown as it is: a success, a partial result or a failure. */
function TallySync({ onDone }: { onDone: () => void }) {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/audit/tally`;
  const today = new Date().toISOString().slice(0, 10);
  const fyStart = (() => { const d = new Date(); return `${d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1}-04-01`; })();
  const [v, setV] = useState({ url: "", tally_company: "", date_from: fyStart, date_to: today });
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TallyResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const status = useLoad(() => api<TallyStatus>(`${base}/status`), [company?.id]);
  const run = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null); setRes(null);
    try { setRes(await api<TallyResult>(`${base}/pull`, { body: { ...v, url: v.url || null, tally_company: v.tally_company || null } })); onDone(); }
    catch (ex) { setErr((ex as Error).message); } finally { setBusy(false); status.reload(); }
  };
  return (
    <section className="card tight stack-sm" aria-label="Sync from Tally">
      <div className="row-between"><h2>Sync from Tally <Help text="Reads the Day Book from TallyPrime running on this computer or your office network. Tally must be open with the company loaded and its HTTP server on (port 9000)." /></h2>
        <span className="muted">{status.data?.last_success ? `Last synced ${dateFmt(status.data.last_success)}` : "Never synced"}</span></div>
      <form className="stack-sm" onSubmit={run}>
        <div className="form-grid">
          <label className="field">Tally address <span className="hint">Leave blank for the default (this computer, port 9000).</span><input value={v.url} placeholder="http://localhost:9000" onChange={(e) => setV({ ...v, url: e.target.value })} /></label>
          <label className="field">Company name in Tally <span className="hint">Leave blank to use this client's name.</span><input value={v.tally_company} onChange={(e) => setV({ ...v, tally_company: e.target.value })} /></label>
          <label className="field">From<input type="date" required value={v.date_from} onChange={(e) => setV({ ...v, date_from: e.target.value })} /></label>
          <label className="field">To<input type="date" required value={v.date_to} onChange={(e) => setV({ ...v, date_to: e.target.value })} /></label>
        </div>
        <div><button className="btn" disabled={busy}>{busy ? "Fetching from Tally…" : "Fetch vouchers from Tally"}</button></div>
      </form>
      <ErrorNotice error={err} />
      {res && (
        <div className={`notice ${res.lines_added > 0 ? "ok" : "info"}`} role="status"><div className="grow">
          {res.vouchers_read} voucher(s) read, {res.lines_added} new ledger line(s) added{res.lines_read - res.lines_added > 0 ? `, ${res.lines_read - res.lines_added} already imported earlier` : ""}; {res.rejected} rejected.
          {res.months_without_vouchers.length > 0 && <div>No vouchers came back for: {res.months_without_vouchers.join(", ")}. Check those months in Tally.</div>}
        </div></div>
      )}
      {!res && !err && status.data && status.data.attempts.length > 0 && !status.data.attempts[0].ok && <p className="muted">The last attempt failed. Check that Tally is open with the company loaded.</p>}
    </section>
  );
}

export function AuditOverview() {
  const { company } = useSession();
  const nav = useNavigate();
  const base = `/api/companies/${company!.id}/audit`;
  const { data, error, loading, reload } = useLoad(() => api<AuditOverviewData>(`${base}/overview`), [company?.id]);
  const [period, setPeriod] = useState({ label: "", start: "", end: "" });
  const stats: [string, number, string][] = data ? [
    ["Items awaiting review", data.awaiting_review, "/audit/exceptions"],
    ["High-severity open", data.open_by_severity.high ?? 0, "/audit/exceptions?severity=high"],
    ["Vouching discrepancies", data.vouching.discrepancies, "/audit/vouching"],
    ["Resolved / accepted", (data.exceptions_by_status.resolved ?? 0) + (data.exceptions_by_status.accepted_with_explanation ?? 0), "/audit/exceptions?status=resolved"],
  ] : [];
  return (
    <div className="stack">
      <PageHead title="Audit" subtitle={`${company!.name} · examine records, evidence and exceptions. Audit review never changes accounting records.`} />
      {can(company!.role, "audit") ? (
        <section className="card tight stack-sm">
          <h2>Import a trial balance or ledger</h2>
          <div className="form-grid">
            <label className="field">Financial period<input placeholder="FY 2025-26" value={period.label} onChange={(e) => setPeriod({ ...period, label: e.target.value })} /></label>
            <label className="field">Period start<input type="date" value={period.start} onChange={(e) => setPeriod({ ...period, start: e.target.value })} /></label>
            <label className="field">Period end<input type="date" value={period.end} onChange={(e) => setPeriod({ ...period, end: e.target.value })} /></label>
          </div>
          <UploadArea companyId={company!.id} workspace="audit" kinds={AUDIT_KINDS} extensions={[".csv", ".xlsx", ".xml"]}
            title="Upload a trial balance or ledger export" subtitle="CSV or XLSX with account and debit/credit columns. FinV checks it and lists anomalies for your review."
            extraFields={() => ({ period_label: period.label, period_start: period.start, period_end: period.end })} onUploaded={() => reload()} />
        </section>
      ) : <div className="notice info">Your role can view audit work but not record audit decisions.</div>}
      {can(company!.role, "audit") && <TallySync onDone={reload} />}
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && (
        <>
          <div className="grid-stats">{stats.map(([l, v, to]) => <button key={l} className="stat" onClick={() => nav(to)}><div className="label">{l}</div><div className="value">{v}</div></button>)}</div>
          <div className="two-col">
            <section className="card tight stack-sm">
              <h2>Imports</h2>
              {data.imports.length === 0 ? <Empty title="No imports yet" icon="scale">Import a trial balance or ledger to start.</Empty> : (
                <div className="table-wrap"><table><thead><tr><th>Type</th><th>Period</th><th className="num">Lines</th><th className="num">Findings</th><th>Imported</th></tr></thead>
                  <tbody>{data.imports.map((i) => (
                    <tr key={i.id} className="clickable" tabIndex={0} onClick={() => nav(i.kind === "trial_balance" ? `/audit/trial-balance/${i.id}` : `/audit/ledger?import_id=${i.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && nav(i.kind === "trial_balance" ? `/audit/trial-balance/${i.id}` : `/audit/ledger?import_id=${i.id}`)}>
                      <td>{label(i.kind)}</td><td>{i.period_label ?? (i.period_start ? `${dateFmt(i.period_start)} – ${dateFmt(i.period_end)}` : "—")}</td>
                      <td className="num">{String(i.summary.lines ?? i.summary.entries ?? "—")}</td><td className="num">{String(i.summary.findings ?? 0)}</td><td className="muted">{dateFmt(i.created_at)}</td></tr>))}</tbody></table></div>
              )}
            </section>
            <section className="card tight stack-sm">
              <h2>Recent audit activity</h2>
              {data.recent_activity.length === 0 ? <p className="muted">No audit activity yet.</p> : (
                <ul className="checks">{data.recent_activity.map((a) => <li key={a.id}><div className="grow">{label(a.action.replace("audit.", ""))}<div className="faint" style={{ fontSize: 12 }}>{a.user_name ?? "FinV"} · {dateFmt(a.created_at)}</div></div></li>)}</ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

interface TBDetail extends TBImport { lines?: { id: number; account_code: string | null; account_name: string | null; account_type: string | null; opening: string | null; debit: string; credit: string; closing: string | null }[]; exceptions: AuditException[] }

const FY_START = (d: string) => { const x = new Date(d); return x.getMonth() >= 3 ? x.getFullYear() : x.getFullYear() - 1; };
function periodText(t: { period_start: string | null; period_end: string | null }) {
  if (!t.period_start || !t.period_end) return "The period this trial balance covers is not stated in the file.";
  const fy = FY_START(t.period_start);
  return `Financial year ${fy}-${String(fy + 1).slice(2)} · ${dateFmt(t.period_start)} to ${dateFmt(t.period_end)}`;
}

interface GeneratedTB {
  financial_year: string; period_start: string; period_end: string; basis: string;
  lines: { account_id: number; code: string; name: string; type: string; debit: string; credit: string; closing_debit: string; closing_credit: string }[];
  total_debit: string; total_credit: string; difference: string; balanced: boolean; not_included: Record<string, number>; limitations: string[];
}

/** Trial balance computed from this client's own posted entries; shows its limits instead of hiding them. */
function BooksTrialBalance() {
  const { company } = useSession();
  const now = new Date();
  const fy0 = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  const [fy, setFy] = useState(`${fy0}-${String(fy0 + 1).slice(2)}`);
  const { data, error, loading } = useLoad(() => api<GeneratedTB>(`/api/companies/${company!.id}/audit/trial-balance/generated?financial_year=${fy}`), [company?.id, fy]);
  return (
    <div className="stack">
      <div className="row">
        <label className="row">Financial year
          <select aria-label="Financial year" value={fy} onChange={(e) => setFy(e.target.value)}>
            {[0, 1, 2].map((k) => { const y = fy0 - k; const v = `${y}-${String(y + 1).slice(2)}`; return <option key={v} value={v}>{v}</option>; })}</select></label>
      </div>
      <ErrorNotice error={error} />
      {loading && !data && <Spinner />}
      {data && (
        <>
          <p className="muted">{data.basis} · {dateFmt(data.period_start)} to {dateFmt(data.period_end)}</p>
          {data.lines.length > 0 && <div className={`notice ${data.balanced ? "ok" : "err"}`}>
            Total debits {money(data.total_debit)} · total credits {money(data.total_credit)}{data.balanced ? " — the books agree." : ` — difference ${money(data.difference)}. FinV has not adjusted anything to hide it.`}</div>}
          {data.limitations.map((l) => <div key={l} className="notice info">{l}</div>)}
          {data.lines.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>Code</th><th>Account</th><th>Type</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Closing debit</th><th className="num">Closing credit</th></tr></thead>
              <tbody>{data.lines.map((l) => <tr key={l.account_id}><td className="mono">{l.code}</td>
                <td><Link to={`/accounting/entries?q=${encodeURIComponent(l.name)}`}>{l.name}</Link></td><td className="muted">{label(l.type)}</td>
                <td className="num">{money(l.debit)}</td><td className="num">{money(l.credit)}</td><td className="num">{money(l.closing_debit)}</td><td className="num">{money(l.closing_credit)}</td></tr>)}</tbody>
            </table></div>
          )}
        </>
      )}
    </div>
  );
}

export function TrialBalance() {
  const [sp, setSp] = useSearchParams();
  const source = sp.get("source") === "books" ? "books" : "imported";
  const pick = (v: string) => setSp(v === "books" ? { source: "books" } : {}, { replace: true });
  return (
    <div className="stack">
      <TabList className="row" items={[["imported", "Imported file"], ["books", "From FinV entries"]]} value={source} label="Trial balance source" onChange={pick} />
      {source === "books" ? <><PageHead title="Trial balance" subtitle="Built from this client's posted entries" /><BooksTrialBalance /></> : <ImportedTrialBalance />}
    </div>
  );
}

function ImportedTrialBalance() {
  const { id } = useParams();
  const { company } = useSession();
  const nav = useNavigate();
  const base = `/api/companies/${company!.id}/audit`;
  const ov = useLoad(() => api<AuditOverviewData>(`${base}/overview`), [company?.id]);
  const tbs = ov.data?.imports.filter((i) => i.kind === "trial_balance") ?? [];
  const current = id ? Number(id) : tbs[0]?.id;
  const det = useLoad(() => (current ? api<TBDetail>(`${base}/imports/${current}`) : Promise.resolve(null)), [current]);
  const [err, setErr] = useState<string | null>(null);
  const rerun = async () => { setErr(null); try { await api(`${base}/imports/${current}/run-checks`, { method: "POST" }); det.reload(); } catch (e) { setErr((e as Error).message); } };
  const exFor = (lineId: number) => det.data?.exceptions.filter((x) => x.object_type === "tb_line" && x.object_id === lineId) ?? [];
  if (ov.loading && !ov.data) return <Spinner />;
  if (!current) return <div><PageHead title="Trial balance" /><div className="card"><Empty title="No trial balance imported" icon="scale">Import one from the <Link to="/audit">Audit overview</Link>.</Empty></div></div>;
  const s = det.data?.summary ?? {};
  return (
    <div className="stack">
      <PageHead title="Trial balance" subtitle={det.data?.period_label ?? "Imported trial balance"}>
        {tbs.length > 1 && <select aria-label="Trial balance import" value={current} onChange={(e) => nav(`/audit/trial-balance/${e.target.value}`)}>{tbs.map((t) => <option key={t.id} value={t.id}>#{t.id} {t.period_label ?? dateFmt(t.created_at)}</option>)}</select>}
        {can(company!.role, "audit") && <button className="btn" onClick={rerun}>Re-run checks</button>}
      </PageHead>
      <ErrorNotice error={err ?? det.error} />
      {det.data && (
        <>
          <div className={`notice ${s.difference === "0.00" ? "ok" : "err"}`}>
            Total debits {money(String(s.total_debit))} · total credits {money(String(s.total_credit))} ({String(s.basis)}){s.difference === "0.00" ? " — the trial balance agrees." : ` — difference ${money(String(s.difference))}.`}
          </div>
          <p className="muted">{periodText(det.data)}</p>
          {det.data.lines && det.data.lines.length > 0 && det.data.lines.every((l) => l.opening === null) &&
            <div className="notice info">This file has no opening balances, so opening-balance checks were not performed.</div>}
          {det.data.lines && det.data.lines.length > 0 && det.data.lines.every((l) => l.closing === null) &&
            <div className="notice info">This file has no closing balances; closing figures are not shown and were not calculated.</div>}
          {det.data.exceptions.filter((x) => x.object_type !== "tb_line").map((x) => <ExceptionRow key={x.id} x={x} />)}
          <div className="table-wrap"><table>
            <thead><tr><th>Code</th><th>Account</th><th>Type</th><th className="num">Opening</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Closing</th><th>Findings</th></tr></thead>
            <tbody>{det.data.lines?.map((l) => { const xs = exFor(l.id); return (
              <tr key={l.id}><td className="mono">{l.account_code}</td><td><Link to={`/audit/ledger?account=${encodeURIComponent(l.account_code ?? l.account_name ?? "")}`}>{l.account_name}</Link></td>
                <td className="muted">{label(l.account_type)}</td><td className="num">{money(l.opening)}</td><td className="num">{money(l.debit)}</td><td className="num">{money(l.credit)}</td><td className="num">{money(l.closing)}</td>
                <td>{xs.map((x) => <Link key={x.id} to={`/audit/exceptions/${x.id}`} style={{ display: "block" }}><Badge status={x.severity}>{x.rule_id}</Badge></Link>)}</td></tr>); })}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}

function ExceptionRow({ x }: { x: AuditException }) {
  return (
    <Link to={`/audit/exceptions/${x.id}`} className="notice" style={{ color: "inherit", textDecoration: "none" }}>
      <Badge status={x.severity}>{label(x.severity)}</Badge><div className="grow"><strong>{x.title}. </strong>{x.explanation}</div><Badge status={x.status} />
    </Link>
  );
}

interface LedgerList extends Paged<LedgerEntry> {
  accounts: { code: string | null; name: string | null; entries: number }[]; totals: { debit: string; credit: string };
  balances: { opening: string | null; closing: string | null; from_trial_balance: number | null; note: string | null } | null;
}

export function LedgerReview({ vouchingOnly = false }: { vouchingOnly?: boolean }) {
  const { company } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const base = `/api/companies/${company!.id}/audit`;
  const qs = new URLSearchParams(params);
  const { data, error, loading, reload } = useLoad(() => api<LedgerList>(`${base}/ledger?${qs.toString()}`), [company?.id, params.toString()]);
  const page = Number(params.get("page") || 1);
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); if (k !== "page") p.delete("page"); setParams(p, { replace: true }); };
  return (
    <div className="stack">
      <PageHead title={vouchingOnly ? "Vouching" : "Ledger review"} subtitle={vouchingOnly ? "Link entries to their supporting documents. FinV compares amount, date, counterparty and reference." :
        "Drill into imported ledger entries and their findings."} />
      <div className="panel row">
        <select aria-label="Account" value={params.get("account") ?? ""} onChange={(e) => set("account", e.target.value)}>
          <option value="">All accounts</option>{data?.accounts.map((a) => <option key={`${a.code}-${a.name}`} value={a.code ?? a.name ?? ""}>{a.code} {a.name} ({a.entries})</option>)}
        </select>
        <form className="row grow" onSubmit={(e) => { e.preventDefault(); set("q", (e.currentTarget.elements.namedItem("q") as HTMLInputElement).value); }}>
          <input name="q" aria-label="Search ledger" className="grow" placeholder="Search narration, voucher, party…" defaultValue={params.get("q") ?? ""} /><button className="btn">Search</button>
        </form>
        <label className="row">From<input type="date" aria-label="From date" value={params.get("from") ?? ""} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="row">To<input type="date" aria-label="To date" value={params.get("to") ?? ""} onChange={(e) => set("to", e.target.value)} /></label>
        <select aria-label="Sort by" value={`${params.get("sort") ?? "date"}:${params.get("dir") ?? "asc"}`} onChange={(e) => { const [k, d] = e.target.value.split(":"); const p = new URLSearchParams(params); p.set("sort", k); p.set("dir", d); p.delete("page"); setParams(p, { replace: true }); }}>
          <option value="date:asc">Oldest first</option><option value="date:desc">Newest first</option>
          <option value="amount:desc">Largest amount</option><option value="amount:asc">Smallest amount</option>
          <option value="voucher:asc">Voucher number</option><option value="account:asc">Account name</option></select>
        <label className="checkbox"><input type="checkbox" checked={params.get("unvouched") === "true"} onChange={(e) => set("unvouched", e.target.checked ? "true" : "")} />Without evidence</label>
      </div>
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && data.items.length === 0 ? <div className="card"><Empty title="No ledger entries" icon="book">Import a ledger from the <Link to="/audit">Audit overview</Link>.</Empty></div> : data && (
        <>
          {params.get("account") && data.balances && (
            <div className="notice info" role="status">
              <div className="grow">
                <strong>{params.get("account")}</strong>: {data.balances.opening !== null ? <>opening {money(data.balances.opening)} · </> : null}
                debits {money(data.totals.debit)} · credits {money(data.totals.credit)}
                {data.balances.closing !== null ? <> · closing {money(data.balances.closing)} (from trial balance #{data.balances.from_trial_balance})</> : null}
                {data.balances.note && <div className="muted">{data.balances.note}</div>}
              </div>
            </div>)}
          {!params.get("account") && <p className="muted">All {data.total} matching entries: debits {money(data.totals.debit)} · credits {money(data.totals.credit)}.</p>}
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Account</th><th>Voucher</th><th>Party / narration</th><th className="num">Debit</th><th className="num">Credit</th><th>Evidence</th></tr></thead>
            <tbody>{data.items.map((l) => (
              <tr key={l.id} className="clickable" tabIndex={0} onClick={() => nav(`/audit/ledger/${l.id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/audit/ledger/${l.id}`)}>
                <td className="muted">{dateFmt(l.entry_date)}</td><td>{l.account_name ?? l.account_code}</td><td className="mono">{l.voucher_no}</td>
                <td>{l.party}<div className="muted" style={{ fontSize: 13 }}>{l.narration}</div></td>
                <td className="num">{Number(l.debit) ? money(l.debit) : ""}</td><td className="num">{Number(l.credit) ? money(l.credit) : ""}</td>
                <td>{l.vouching?.length ? l.vouching.map((v) => <Badge key={v.id} status={v.outcome} />) : <span className="faint">None</span>}</td></tr>))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPage={(p) => set("page", String(p))} />
        </>
      )}
    </div>
  );
}

interface LedgerDetail extends LedgerEntry { vouching: (Vouching & { record: RecordBrief | null })[]; exceptions: AuditException[] }

export function LedgerEntryDetail() {
  const { id } = useParams();
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/audit`;
  const { data, error, loading, reload } = useLoad(() => api<LedgerDetail>(`${base}/ledger/${id}`), [id]);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RecordBrief[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const search = async (e?: FormEvent) => { e?.preventDefault(); setErr(null); try { setResults(await api<RecordBrief[]>(`${base}/evidence-search?q=${encodeURIComponent(q)}&amount=${data ? (Number(data.debit) ? data.debit : data.credit) : ""}`)); } catch (ex) { setErr((ex as Error).message); } };
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); reload(); setResults(null); } catch (ex) { setErr((ex as Error).message); } };
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNotice error={error} />;
  const amount = Number(data.debit) ? data.debit : data.credit;
  return (
    <div className="stack">
      <PageHead title={`${data.account_name ?? data.account_code} · ${money(amount)}`} subtitle={<><Link to="/audit/ledger">Ledger</Link> · voucher {data.voucher_no ?? "—"} · {dateFmt(data.entry_date)}</>} />
      <ErrorNotice error={err} />
      <div className="two-col">
        <div className="stack">
          <section className="card tight stack-sm">
            <h2>Supporting evidence</h2>
            {data.vouching.length === 0 ? <p className="muted">No supporting document is linked. Attaching a document does not make an entry correct: FinV compares it with the entry.</p> : data.vouching.map((v) => (
              <div key={v.id} className="panel stack-sm">
                <div className="row-between"><strong>{v.record ? `${label(v.record.record_type)} ${v.record.doc_number ?? v.record.reference ?? ""}` : `Record #${v.record_id}`}</strong><Badge status={v.outcome} /></div>
                <p>{v.result.summary}</p>
                <ul className="checks">{v.result.checks.map((c) => <li key={c.check} className={c.ok === false ? "error" : ""}><Badge status={c.ok === null ? "not_applicable" : c.ok ? "pass" : "error"}>{c.ok === null ? "Not checked" : c.ok ? "Agrees" : "Differs"}</Badge><div className="grow"><strong>{label(c.check)}: </strong>{c.detail}</div></li>)}</ul>
                <div className="row"><span className="muted">Auditor review:</span><Badge status={v.review_status} />{v.comment && <span className="muted">“{v.comment}”</span>}</div>
                {can(company!.role, "audit") && <ReviewControls onSave={(status, comment) => run(() => api(`${base}/vouching/${v.id}/review`, { body: { review_status: status, comment } }))} />}
              </div>
            ))}
          </section>
          {can(company!.role, "audit") && (
            <section className="card tight stack-sm">
              <h2>Link evidence</h2>
              <form className="row" onSubmit={search}><input className="grow" aria-label="Search documents" placeholder="Party, invoice number, reference…" value={q} onChange={(e) => setQ(e.target.value)} /><button className="btn">Search documents</button></form>
              {results && (results.length === 0 ? <p className="muted">No documents found. Upload invoices or bank statements in the Accounting workspace first.</p> : (
                <div className="table-wrap"><table><thead><tr><th>Document</th><th>Party</th><th>Date</th><th className="num">Amount</th><th /></tr></thead>
                  <tbody>{results.map((r) => <tr key={r.id}><td>{label(r.record_type)} <span className="mono">{r.doc_number ?? r.reference}</span></td><td>{r.party_name ?? r.description}</td><td>{dateFmt(r.doc_date)}</td>
                    <td className="num">{money(r.total_amount ?? r.debit_amount ?? r.credit_amount)}</td><td><button className="btn small" onClick={() => run(() => api(`${base}/ledger/${data.id}/evidence`, { body: { record_id: r.id } }))}>Link</button></td></tr>)}</tbody></table></div>
              ))}
            </section>
          )}
        </div>
        <div className="stack">
          <section className="card tight stack-sm">
            <h2>Entry</h2>
            <dl className="kv"><dt>Date</dt><dd>{dateFmt(data.entry_date)}</dd><dt>Account</dt><dd>{data.account_code} {data.account_name}</dd><dt>Voucher</dt><dd>{data.voucher_no ?? "—"} {data.voucher_type ?? ""}</dd>
              <dt>Party</dt><dd>{data.party ?? "—"}</dd><dt>Narration</dt><dd>{data.narration ?? "—"}</dd><dt>Debit</dt><dd>{money(data.debit)}</dd><dt>Credit</dt><dd>{money(data.credit)}</dd></dl>
          </section>
          <section className="card tight stack-sm">
            <h2>Findings</h2>
            {data.exceptions.length === 0 ? <p className="muted">No findings for this entry.</p> : data.exceptions.map((x) => <ExceptionRow key={x.id} x={x} />)}
          </section>
        </div>
      </div>
    </div>
  );
}

function ReviewControls({ onSave }: { onSave: (status: string, comment: string | null) => void }) {
  const [comment, setComment] = useState("");
  return (
    <div className="row">
      <input className="grow" aria-label="Review comment" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
      <button className="btn small" onClick={() => onSave("agreed", comment || null)}>Agree</button>
      <button className="btn small" onClick={() => onSave("queried", comment || null)}>Query</button>
    </div>
  );
}

const EX_STATUSES = ["open", "needs_information", "under_review", "resolved", "accepted_with_explanation"];

export function Exceptions() {
  const { company } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "unresolved";
  const severity = params.get("severity") ?? "";
  const base = `/api/companies/${company!.id}/audit`;
  const { data, error, loading, reload } = useLoad(() => api<AuditException[]>(`${base}/exceptions?status=${status}${severity ? `&severity=${severity}` : ""}`), [company?.id, status, severity]);
  return (
    <div className="stack">
      <PageHead title="Exceptions" subtitle="Anomalies detected by FinV's checks. A finding is not a confirmed error until you decide." />
      <div className="panel row">
        <select aria-label="Status" value={status} onChange={(e) => setParams({ status: e.target.value, ...(severity ? { severity } : {}) }, { replace: true })}>
          <option value="unresolved">Unresolved</option>{EX_STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}<option value="">All</option>
        </select>
        <select aria-label="Severity" value={severity} onChange={(e) => setParams({ status, ...(e.target.value ? { severity: e.target.value } : {}) }, { replace: true })}>
          <option value="">All severities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
        </select>
      </div>
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data ? <Spinner /> : data && data.length === 0 ? <div className="card"><Empty title="No exceptions" icon="flag">Nothing matches these filters.</Empty></div> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Severity</th><th>Finding</th><th>Rule</th><th>Status</th><th>Raised</th></tr></thead>
          <tbody>{data?.map((x) => (
            <tr key={x.id} className="clickable" tabIndex={0} onClick={() => nav(`/audit/exceptions/${x.id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/audit/exceptions/${x.id}`)}>
              <td><Badge status={x.severity}>{label(x.severity)}</Badge></td><td><strong>{x.title}</strong><div className="muted" style={{ fontSize: 13 }}>{x.explanation}</div></td>
              <td className="mono faint">{x.rule_id}</td><td><Badge status={x.status} /></td><td className="muted">{dateFmt(x.created_at)}</td></tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export function ExceptionDetail() {
  const { id } = useParams();
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/audit`;
  const { data, error, loading, reload, setData } = useLoad(() => api<AuditException>(`${base}/exceptions/${id}`), [id]);
  const [status, setStatus] = useState("");
  const [comment, setComment] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try { setData(await api<AuditException>(`${base}/exceptions/${id}`, { body: { status: status || null, comment: comment || null } })); setComment(""); setStatus(""); }
    catch (ex) { setErr((ex as Error).message); }
  };
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNotice error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <PageHead title={data.title} subtitle={<><Link to="/audit/exceptions">Exceptions</Link> · rule {data.rule_id}</>}><Badge status={data.severity}>{label(data.severity)} severity</Badge><Badge status={data.status} /></PageHead>
      <div className="two-col">
        <div className="stack">
          <section className="card tight stack-sm">
            <h2>Why this was flagged</h2>
            <p>{data.explanation}</p>
            {data.suggested_action && <p><strong>Suggested next step: </strong>{data.suggested_action}</p>}
            {data.ledger_entry && <p><Link to={`/audit/ledger/${data.ledger_entry.id}`}>Open the ledger entry ({data.ledger_entry.voucher_no ?? "no voucher"}, {money(Number(data.ledger_entry.debit) ? data.ledger_entry.debit : data.ledger_entry.credit)})</Link></p>}
            {data.tb_line && <dl className="kv">{Object.entries(data.tb_line).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{label(k)}</dt><dd>{v ?? "—"}</dd></div>)}</dl>}
            <details><summary>Evidence</summary><pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(data.evidence, null, 2)}</pre></details>
          </section>
          {can(company!.role, "audit") && (
            <form className="card tight stack-sm" onSubmit={save}>
              <h2>Record a decision</h2>
              <ErrorNotice error={err} />
              <label className="field">Change status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Keep {label(data.status).toLowerCase()}</option>
                {EX_STATUSES.filter((s) => s !== data.status).map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></label>
              <label className="field">Comment{["resolved", "accepted_with_explanation"].includes(status) && <span className="hint">Required: explain the resolution.</span>}<textarea value={comment} onChange={(e) => setComment(e.target.value)} /></label>
              <div><button className="btn primary" disabled={!status && !comment.trim()}>Save</button></div>
              <p className="faint" style={{ fontSize: 12 }}>Corrections to the books go through the Accounting approval workflow; audit decisions never change accounting records.</p>
            </form>
          )}
        </div>
        <section className="card tight stack-sm">
          <h2>Review history</h2>
          {data.history?.length === 0 ? <p className="muted">No decisions yet.</p> : <ul className="checks">{data.history?.map((h) => (
            <li key={h.id}><div className="grow">{h.to_status ? <>Status → <Badge status={h.to_status} /></> : "Comment"}{h.comment && <p style={{ marginTop: 4 }}>{h.comment}</p>}
              <div className="faint" style={{ fontSize: 12 }}>{h.user_name} · {dateFmt(h.created_at)}</div></div></li>))}</ul>}
        </section>
      </div>
    </div>
  );
}

interface ReportVersion {
  id: number; version: number; status: "draft" | "signed_off" | "superseded"; note: string | null; created_by: string; created_at: string;
  signed_by: string | null; signed_at: string | null; sign_note: string | null; open_exceptions: number; open_high: number; content_hash: string;
}
const VERSION_TONE = { draft: "warn", signed_off: "ok", superseded: "" } as const;

/** Frozen snapshots of the audit work. Only the newest draft can be signed, and signing with open exceptions needs a tick. */
function ReportVersions() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/audit/report-versions`;
  const list = useLoad(() => api<ReportVersion[]>(base), [company?.id]);
  const canSign = can(company!.role, "audit");
  const [note, setNote] = useState("");
  const [ack, setAck] = useState<Record<number, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); setNote(""); list.reload(); } catch (e) { setErr((e as Error).message); } };
  return (
    <section className="card stack-sm" aria-label="Report versions">
      <div><h2>Report versions and sign-off</h2>
        <p className="muted">Each version is a frozen copy of the audit work at that moment. Signing off applies to that copy only. If the work changes afterwards, create a new version.</p></div>
      <ErrorNotice error={err} />
      {canSign && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api(base, { body: { note } })); }}>
          <input aria-label="Version note" placeholder="Note for this version (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
          <button className="btn primary">Create new version</button>
        </form>
      )}
      {list.loading && !list.data ? <Spinner /> : (list.data ?? []).length === 0 ? <p className="muted">No version yet.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Version</th><th>Status</th><th>Created</th><th>Open exceptions</th><th>Sign-off</th></tr></thead>
          <tbody>{list.data!.map((v) => (
            <tr key={v.id}>
              <td>v{v.version}{v.note && <div className="muted" style={{ fontSize: 13 }}>{v.note}</div>}</td>
              <td><Badge status={VERSION_TONE[v.status] === "ok" ? "approved" : VERSION_TONE[v.status] === "warn" ? "needs_review" : ""}>{label(v.status)}</Badge></td>
              <td>{dateFmt(v.created_at)}<div className="muted" style={{ fontSize: 13 }}>{v.created_by}</div></td>
              <td>{v.open_exceptions}{v.open_high > 0 && <span className="muted"> ({v.open_high} high)</span>}</td>
              <td>{v.status === "signed_off" ? <span>Signed by {v.signed_by}<div className="muted" style={{ fontSize: 13 }}>{dateFmt(v.signed_at!)}{v.sign_note ? ` · ${v.sign_note}` : ""}</div></span>
                : v.status === "draft" && canSign ? (
                  <span className="stack-sm">
                    {v.open_exceptions > 0 && <label className="row"><input type="checkbox" checked={!!ack[v.id]} onChange={(e) => setAck({ ...ack, [v.id]: e.target.checked })} /> Sign off with {v.open_exceptions} exception(s) still open</label>}
                    <button className="btn small" disabled={v.open_exceptions > 0 && !ack[v.id]} onClick={() => run(() => api(`${base}/${v.id}/sign-off`, { body: { acknowledge_open: !!ack[v.id] } }))}>Sign off v{v.version}</button>
                  </span>) : <span className="muted">—</span>}</td>
            </tr>))}</tbody></table></div>
      )}
    </section>
  );
}

export function AuditReports() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/audit/reports`;
  const items: [string, string, string][] = [
    ["exceptions", "Exceptions", "Every finding with its status, separating unresolved anomalies from resolved and accepted items."],
    ["vouching", "Vouching results", "Each ledger entry linked to evidence, the comparison outcome and the auditor's review."],
    ["missing-evidence", "Supporting-document gaps", "Entries at or above the vouching threshold without evidence."],
    ["review-history", "Audit activity and approvals", "Who recorded which audit decision and when."],
  ];
  return (
    <div className="stack">
      <PageHead title="Audit reports" subtitle="Exports are generated from the stored audit work. They support, and do not replace, the auditor's professional judgement." />
      <ReportVersions />
      <div className="stack-sm">{items.map(([k, t, d]) => (
        <div key={k} className="card tight row-between"><div><h3>{t}</h3><p className="muted">{d}</p></div><a className="btn" href={`${base}/${k}.csv`}>Download CSV</a></div>))}</div>
    </div>
  );
}
