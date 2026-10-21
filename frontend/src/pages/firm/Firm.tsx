import { Fragment, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, dateFmt, label } from "../../api";
import { Badge, Dialog, Empty, ErrorNotice, Help, Icon, PageHead, Pagination, SeverityChip, Spinner } from "../../components/ui";
import { useLoad, useSession } from "../../session";
import { Assistant } from "../../components/Assistant";
import { CreateCompany } from "../Home";
import type { Company, Doc } from "../../types";

interface Task {
  key: string; client_id: number; client_name: string; gstin: string | null; category: string; task: string; period: string | null;
  due_date: string | null; status: string; urgency: "overdue" | "due_soon" | "upcoming" | "done"; severity: string;
  issue: string | null; next_action: string; link: string;
}
interface Issue {
  key: string; client_id: number; client_name: string; category: string; severity: string; title: string; description: string;
  detection: string; nature: "rule_detected" | "potential" | "missing_data"; review_status: string; next_action: string; link: string;
  count?: number; created_at: string | null;
  period?: string | null; financial_year?: string | null; expected?: string | null; actual?: string | null; source?: { import_id: number | null; document_id: number | null };
}
interface ClientRow extends Company {
  overdue: number; due_soon: number; pending_tasks: number; awaiting_review: number; issues: number; high_issues: number;
  missing_data: boolean; financial_year: string; work_status: string; last_import_at: string | null;
}
interface Module { label: string; icon: string; to: string; on: boolean; tone: string; help: string }
interface Paged<T> { items: T[]; total: number; page: number; page_size: number }
interface Overview {
  as_of: string; financial_year: string; attention: Task[]; top_issues: Issue[];
  summary: Record<"clients" | "gst_pending" | "gst_overdue" | "awaiting_review" | "due_soon" | "issues_open" | "issues_high" | "audit_findings" | "missing_data", number>;
  not_tracked: { area: string; reason: string }[];
}
type ImportRow = Doc & { client_id: number; client_name: string };

const NATURE: Record<Issue["nature"], string> = { rule_detected: "Rule-detected", potential: "Potential", missing_data: "Missing data" };
const URGENCY_TEXT: Record<Task["urgency"], string> = { overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming", done: "Done" };

const useOverview = () => useLoad(() => api<Overview>("/api/firm/overview"), []);

/** Query string from the non-empty values, so every filter and page is applied by the server. */
const qs = (o: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== "") p.set(k, String(v)); });
  const t = p.toString();
  return t ? `?${t}` : "";
};

/** Waits for typing to pause before the server is asked again. */
function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
const WORK_TEXT: Record<string, string> = { overdue: "Overdue", needs_attention: "Needs attention", missing_data: "Missing data", on_track: "On track" };
const WORK_TONE: Record<string, string> = { overdue: "err", needs_attention: "warn", missing_data: "warn", on_track: "ok" };

/** Opens the client's workspace at the task or finding, never mixing it with the previously selected client. */
function useOpen() {
  const { selectCompany } = useSession();
  const nav = useNavigate();
  return (clientId: number, link: string) => { selectCompany(clientId); nav(link); };
}

const canAddClient = (orgs: { role: string }[] | undefined) => (orgs ?? []).some((o) => o.role === "owner" || o.role === "admin");

function AddClient({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  return <Dialog title="Add new client" onClose={onClose}><CreateCompany onCancel={onClose} onDone={() => { onDone(); onClose(); }} /></Dialog>;
}

function Tile({ label: l, value, tone, onClick }: { label: string; value: number | undefined; tone?: "err" | "warn"; onClick: () => void }) {
  return (
    <button className="stat" onClick={onClick}>
      <div className="label">{l}</div>
      <div className="value" style={value && tone ? { color: `var(--${tone})` } : undefined}>{value ?? "–"}</div>
    </button>
  );
}

function TaskTable({ tasks, open, groupByClient = false }: { tasks: Task[]; open: (id: number, link: string) => void; groupByClient?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Client</th><th>Task</th><th>Period</th><th>Due</th><th>Status</th><th>Issue</th><th>Next action</th></tr></thead>
        <tbody>
          {tasks.map((t, n) => (
            <Fragment key={t.key}>{groupByClient && (n === 0 || tasks[n - 1].client_id !== t.client_id) && <tr><th colSpan={7} scope="colgroup">{t.client_name}</th></tr>}
            <tr className={`clickable ${t.urgency === "overdue" || t.status === "error" ? "row-err" : t.urgency === "due_soon" || t.status === "awaiting_review" || t.status === "missing_documents" ? "row-warn" : ""}`} tabIndex={0} onClick={() => open(t.client_id, t.link)} onKeyDown={(e) => e.key === "Enter" && open(t.client_id, t.link)}>
              <td><div>{t.client_name}</div>{t.gstin && <div className="faint mono" style={{ fontSize: 12 }}>{t.gstin}</div>}</td>
              <td>{t.task}</td><td>{t.period ?? "—"}</td>
              <td><div>{dateFmt(t.due_date)}</div>
                {t.urgency !== "upcoming" && t.urgency !== "done" && <span className={`badge ${t.urgency === "overdue" ? "err" : "warn"}`}>{URGENCY_TEXT[t.urgency]}</span>}</td>
              <td><Badge status={t.status}>{label(t.status)}</Badge></td>
              <td className="muted">{t.issue ?? "—"}</td><td>{t.next_action}</td>
            </tr></Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IssueTable({ issues, open, groupByClient = false }: { issues: Issue[]; open: (id: number, link: string) => void; groupByClient?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Client</th><th>Category</th><th>Severity <Help text="How much attention it needs. High means fix before filing or sign-off." /></th><th>Finding</th><th>Detected by <Help text="Rule-detected: a fixed check found it. Potential: worth a look, may be fine. Missing data: something has not been imported yet." /></th><th>Review <Help text="Whether someone on your team has looked at this finding yet." /></th><th>Next step</th></tr></thead>
        <tbody>
          {issues.map((i, n) => (
            <Fragment key={i.key}>{groupByClient && (n === 0 || issues[n - 1].client_id !== i.client_id) && <tr><th colSpan={7} scope="colgroup">{i.client_name}</th></tr>}
            <tr className={`clickable ${i.severity === "high" ? "row-err" : i.severity === "medium" ? "row-warn" : ""}`} tabIndex={0} onClick={() => open(i.client_id, i.link)} onKeyDown={(e) => e.key === "Enter" && open(i.client_id, i.link)}>
              <td>{i.client_name}</td><td>{label(i.category)}</td><td><SeverityChip severity={i.severity} /></td>
              <td><div>{i.title}</div><div className="muted" style={{ fontSize: 13 }}>{i.description}</div>
                {i.expected != null && i.actual != null && <div style={{ fontSize: 13 }}><strong>Expected</strong> {i.expected} · <strong>Actual</strong> {i.actual}</div>}
                {(i.period || i.financial_year) && <div className="faint" style={{ fontSize: 12 }}>{[i.financial_year && `FY ${i.financial_year}`, i.period].filter(Boolean).join(" · ")}</div>}</td>
              <td><div>{i.detection}</div><span className="badge">{NATURE[i.nature]}</span></td>
              <td><Badge status={i.review_status} /></td><td>{i.next_action}</td>
            </tr></Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FirmDashboard() {
  const { me } = useSession();
  const { data, error, loading, reload } = useOverview();
  const nav = useNavigate();
  const open = useOpen();
  const [adding, setAdding] = useState(false);
  const canAdd = canAddClient(me?.organizations);
  const s = data?.summary;
  const attention = data?.attention ?? [];
  return (
    <div className="stack">
      <PageHead title="Firm dashboard" subtitle={data ? `All clients · FY ${data.financial_year} · as of ${dateFmt(data.as_of)}` : "All clients"}>
        {canAdd && <button className="btn primary" onClick={() => setAdding(true)}>Add new client</button>}
        <button className="btn" onClick={() => nav("/imports")}>Import data</button>
      </PageHead>
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && data.summary.clients === 0 && (
        <Empty title="No clients yet" icon="list">
          {canAdd ? <button className="btn primary" onClick={() => setAdding(true)}>Add new client</button>
            : "Ask your firm's owner or admin to add a client or give you access."}
        </Empty>
      )}
      {data && data.summary.clients > 0 && (
        <>
          <div className="grid-stats">
            <Tile label="GST returns pending" value={s?.gst_pending} onClick={() => nav("/compliance")} />
            <Tile label="GST returns overdue" value={s?.gst_overdue} tone="err" onClick={() => nav("/compliance?filter=overdue")} />
            <Tile label="Awaiting review" value={s?.awaiting_review} tone="warn" onClick={() => nav("/compliance?filter=awaiting_review")} />
            <Tile label="Due within 7 days" value={s?.due_soon} tone="warn" onClick={() => nav("/compliance?filter=due_soon")} />
            <Tile label="Open issues" value={s?.issues_open} tone="err" onClick={() => nav("/errors")} />
            <Tile label="Audit findings to review" value={s?.audit_findings} tone="warn" onClick={() => nav("/errors?category=audit_exception")} />
            <Tile label="Clients missing data" value={s?.missing_data} tone="warn" onClick={() => nav("/errors?category=missing_data")} />
          </div>
          <section className="card tight stack-sm">
            <div className="row-between"><h2>Needs attention <Help text="GST returns and reviews that are overdue or due soon, most urgent first." /></h2><button className="btn small" onClick={() => nav("/compliance")}>All tasks</button></div>
            {attention.length === 0 ? <p className="muted">Nothing overdue, due within 7 days or waiting for review.</p> : <TaskTable tasks={attention} open={open} />}
          </section>
          <section className="card tight stack-sm">
            <div className="row-between"><h2>Errors and findings across clients <Help text="Problems found in imported data: mismatches, duplicates, unmatched bank lines and missing records." /></h2><button className="btn small" onClick={() => nav("/errors")}>Error centre</button></div>
            {data.top_issues.length === 0 ? <p className="muted">No open issues were found in the data imported so far.</p> : <IssueTable issues={data.top_issues} open={open} />}
          </section>
          <section className="notice info" aria-label="Not covered yet">
            <div>
              <strong>Not covered by this view yet</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{data.not_tracked.map((n) => <li key={n.area}><strong>{n.area}:</strong> {n.reason}</li>)}</ul>
            </div>
          </section>
        </>
      )}
      {adding && <AddClient onClose={() => setAdding(false)} onDone={reload} />}
    </div>
  );
}

export function Clients() {
  const { me, refresh } = useSession();
  const nav = useNavigate();
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("name");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, filter, sort]);
  const { data, error, loading, reload } = useLoad(() => api<Paged<ClientRow>>(`/api/firm/clients${qs({ q: dq, filter, sort, page })}`), [dq, filter, sort, page]);
  return (
    <div className="stack">
      <PageHead title="Clients" subtitle={data ? `${data.total} client${data.total === 1 ? "" : "s"}` : undefined}>
        {canAddClient(me?.organizations) && <button className="btn primary" onClick={() => setAdding(true)}>Add new client</button>}
      </PageHead>
      <ErrorNotice error={error} onRetry={reload} />
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input aria-label="Search clients" placeholder="Search by name, GSTIN or client ID" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
        <select aria-label="Filter clients" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All clients</option><option value="overdue">Overdue work</option><option value="high_issues">High-priority errors</option>
          <option value="pending_review">Pending reviews</option><option value="missing_data">Missing data</option><option value="due_soon">Deadlines within 7 days</option>
        </select>
        <select aria-label="Sort clients" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="name">Sort: name</option><option value="overdue">Sort: most overdue</option><option value="issues">Sort: most issues</option><option value="last_import">Sort: least recent import</option>
        </select>
      </div>
      {loading && !data ? <Spinner /> : !data || data.items.length === 0 ? <Empty title={q || filter ? "No client matches" : "No clients yet"} icon="list" /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Client</th><th>GSTIN</th><th>FY</th><th className="num">Pending tasks</th><th className="num">Overdue</th><th className="num">Open issues</th><th>Last import</th><th>Status</th></tr></thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id} className="clickable" tabIndex={0} onClick={() => nav(`/clients/${c.id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/clients/${c.id}`)}>
                  <td><div>{c.name}</div><div className="faint mono" style={{ fontSize: 12 }}>ID {c.id}</div></td>
                  <td className="mono">{c.gstin ?? "—"}</td><td>{c.financial_year}</td>
                  <td className="num">{c.pending_tasks}</td><td className="num">{c.overdue}</td>
                  <td className="num">{c.issues}{c.high_issues > 0 && <span className="badge err" style={{ marginLeft: 6 }}>{c.high_issues} high</span>}</td>
                  <td className="muted">{c.last_import_at ? dateFmt(c.last_import_at) : "Never"}</td>
                  <td><span className={`badge ${WORK_TONE[c.work_status] ?? ""}`}>{WORK_TEXT[c.work_status] ?? label(c.work_status)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      {adding && <AddClient onClose={() => setAdding(false)} onDone={() => { reload(); refresh(); }} />}
    </div>
  );
}

/** A client's own workspace: its modules, deadlines and findings, and nothing from any other client. */
export function ClientWorkspace() {
  const { id } = useParams();
  const cid = Number(id);
  const { selectCompany, companies, loading: sessionLoading } = useSession();
  const nav = useNavigate();
  const open = useOpen();
  const known = companies.some((c) => c.id === cid);
  useEffect(() => { if (known) selectCompany(cid); }, [cid, known]);       // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api<{ client: ClientRow; tasks: Task[]; issues: Issue[]; tds: { applicable: boolean; tan: string | null; gst_filing: string; payable: string } }>(`/api/firm/clients/${cid}`), [cid]);
  if (!sessionLoading && !known) return <Empty title="Client not found" icon="list"><button className="btn" onClick={() => nav("/clients")}>Back to clients</button></Empty>;
  const c = data?.client;
  const has = (s: string) => (c?.services ?? ["accounting", "audit"]).includes(s as "audit");
  const modules: Module[] = [
    { label: "Vouching", icon: "search", to: "/audit/vouching", on: has("audit"), tone: "audit", help: "Check each entry against its source document." },
    { label: "Ledger", icon: "book", to: "/audit/ledger", on: has("audit"), tone: "audit", help: "Every account's entries in one place." },
    { label: "Trial Balance", icon: "scale", to: "/audit/trial-balance", on: has("audit"), tone: "audit", help: "Debits and credits per account, to confirm they agree." },
    { label: "Audit Report", icon: "chart", to: "/audit/reports", on: has("audit"), tone: "audit", help: "Findings and the draft report for sign-off." },
    { label: "GST", icon: "flag", to: "/accounting/gst", on: has("accounting"), tone: "gst", help: "Prepare returns and track due dates. Filing is done by you on the portal." },
    { label: "Bank Reconciliation", icon: "bank", to: "/accounting/bank", on: has("accounting"), tone: "bank", help: "Match bank statement lines with entries." },
    { label: "Documents and imports", icon: "upload", to: "/accounting/transactions", on: has("accounting"), tone: "docs", help: "Upload bills, statements and files for this client." },
    { label: "Entries", icon: "list", to: "/accounting/entries", on: has("accounting"), tone: "docs", help: "Journal entries waiting for review or already posted." },
  ];
  const flagged = (to: string) => (data?.issues ?? []).filter((i) => i.link === to || i.link.startsWith(`${to}/`)).length
    + (data?.tasks ?? []).filter((t) => t.urgency !== "done" && t.status !== "completed" && (t.link === to || t.link.startsWith(`${to}/`))).length;
  const noData = !!c?.missing_data;
  const gstOpen = (data?.tasks ?? []).filter((t) => t.category === "gst" && t.status === "not_started").length;
  const steps: { done: boolean; text: string; to: string }[] = [
    { done: !noData, text: "Upload a bank statement or import from Tally", to: "/accounting/transactions" },
    { done: !noData && (data?.issues.length ?? 0) === 0, text: noData ? "Review findings (after the first import)" : `Review findings${data?.issues.length ? ` (${data.issues.length} open)` : ""}`, to: "/errors" },
    ...(c?.gstin && has("accounting") ? [{ done: !noData && gstOpen === 0, text: gstOpen ? `Prepare GST returns (${gstOpen} not started)` : "Prepare GST returns", to: "/accounting/gst" }] : []),
  ];
  return (
    <div className="stack">
      <PageHead title={c?.name ?? "Client"} subtitle={c ? <span>Client ID {c.id}{c.gstin ? ` · GSTIN ${c.gstin}` : ""} · FY {c.financial_year} · Last import {c.last_import_at ? dateFmt(c.last_import_at) : "never"}</span> : undefined}>
        {c && <span className={`badge ${WORK_TONE[c.work_status] ?? ""}`}>{WORK_TEXT[c.work_status]}</span>}
        <button className="btn" onClick={() => nav("/")}>Firm dashboard</button>
      </PageHead>
      <ErrorNotice error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && (
        <>
          <section className="next-steps" aria-label="Next steps">
            <h2>Next steps <Help text="What to do for this client, in order. A tick means that step is done." /></h2>
            <ol className="steps">{steps.map((st) => (
              <li key={st.text} className={st.done ? "done" : ""}>
                <span className="step-mark" aria-hidden="true">{st.done ? "✓" : "○"}</span>
                <button className="link" onClick={() => st.to === "/errors" ? nav(`/errors?client_id=${cid}`) : nav(st.to)}>{st.text}</button>
                <span className="sr-only">{st.done ? " (done)" : " (to do)"}</span>
              </li>))}</ol>
          </section>
          <nav aria-label="Client modules" className="module-grid">
            {modules.filter((m) => m.on).map((m) => {
              const n = flagged(m.to);
              return (
                <button key={m.label} className={`module-card tone-${m.tone}`} onClick={() => nav(m.to)}>
                  <span className="module-icon"><Icon name={m.icon} size={20} /></span>
                  <span className="module-title">{m.label}</span>
                  <span className="module-help">{m.help}</span>
                  <span className={`badge ${n ? "warn" : noData && m.tone !== "gst" ? "" : "ok"}`}>{n ? `${n} open` : noData && m.tone !== "gst" ? "Not started" : "Up to date"}</span>
                </button>
              );
            })}
            <button className="module-card tone-tds" onClick={() => nav("/settings")}>
              <span className="module-icon"><Icon name="scale" size={20} /></span>
              <span className="module-title">TDS</span>
              <span className="module-help">{data.tds.applicable
                ? `Tracked from posted FinV entries. ${data.tds.tan ? `TAN ${data.tds.tan}.` : "Add the TAN in Settings."} Challans and returns are not tracked.`
                : "Tax deducted at source. Turn it on in Settings if this client deducts TDS."}</span>
              <span className={`badge ${data.tds.applicable && Number(data.tds.payable) > 0 ? "warn" : ""}`}>
                {data.tds.applicable ? (Number(data.tds.payable) > 0 ? `₹${data.tds.payable} payable` : "Nothing payable") : "Not set up"}</span>
            </button>
          </nav>
          <section className="card tight stack-sm"><h2>Deadlines and tasks</h2>
            {data.tasks.length === 0 ? <p className="muted">No tasks apply to this client. GST tasks need a GSTIN and accounting work.</p> : <TaskTable tasks={data.tasks} open={open} />}</section>
          <section className="card tight stack-sm"><h2>Errors and findings</h2>
            {data.issues.length === 0 ? <p className="muted">No open issues were found in this client's imported data.</p> : <IssueTable issues={data.issues} open={open} />}</section>
        </>
      )}
      {known && <Assistant workspace="client" />}
    </div>
  );
}

const TASK_FILTERS: [string, string][] = [
  ["", "All tasks"], ["overdue", "Overdue"], ["due_soon", "Due soon"], ["pending", "Pending"], ["error", "Errors"],
  ["awaiting_review", "Awaiting review"], ["missing_documents", "Missing documents"], ["completed", "Completed"],
];

function ClientSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { companies } = useSession();
  return (
    <select aria-label="Filter by client" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">All clients</option>{companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select>
  );
}

export function Compliance() {
  const open = useOpen();
  const [init] = useState(() => new URLSearchParams(window.location.search));
  const [status, setStatus] = useState(init.get("filter") ?? "");
  const [client, setClient] = useState("");
  const [fy, setFy] = useState("");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState(false);
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, status, client, fy]);
  const { data, error, loading, reload } = useLoad(() => api<Paged<Task> & { facets: { financial_years: string[] } }>(
    `/api/firm/tasks${qs({ q: dq, status, client_id: client, financial_year: fy, page })}`), [dq, status, client, fy, page]);
  const rows = group ? [...(data?.items ?? [])].sort((a, b) => a.client_name.localeCompare(b.client_name)) : data?.items ?? [];
  return (
    <div className="stack">
      <PageHead title="Compliance and deadlines" subtitle="Only obligations that apply to each client are listed." />
      <ErrorNotice error={error} onRetry={reload} />
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input aria-label="Search tasks" placeholder="Search client, GSTIN or task" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 300 }} />
        <select aria-label="Filter tasks" value={status} onChange={(e) => setStatus(e.target.value)}>
          {TASK_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <ClientSelect value={client} onChange={setClient} />
        <select aria-label="Filter by financial year" value={fy} onChange={(e) => setFy(e.target.value)}>
          <option value="">All financial years</option>{(data?.facets.financial_years ?? []).map((y) => <option key={y} value={y}>FY {y}</option>)}
        </select>
        <label className="checkbox"><input type="checkbox" checked={group} onChange={(e) => setGroup(e.target.checked)} /> Group by client</label>
      </div>
      {loading && !data ? <Spinner /> : rows.length === 0 ? <Empty title="No tasks match" icon="flag">GST tasks appear for clients that have a GSTIN and receive accounting work.</Empty> : <TaskTable tasks={rows} open={open} groupByClient={group} />}
      {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      <div className="notice info"><div>TDS payments and returns are not tracked yet.</div></div>
    </div>
  );
}

export function ErrorCentre() {
  const open = useOpen();
  const [init] = useState(() => new URLSearchParams(window.location.search));
  const [client, setClient] = useState(init.get("client_id") ?? "");
  const [category, setCategory] = useState(init.get("category") ?? "");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [period, setPeriod] = useState("");
  const [group, setGroup] = useState(false);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [client, category, severity, status, period]);
  const { data, error, loading, reload } = useLoad(() => api<Paged<Issue> & { facets: { categories: string[]; statuses: string[] } }>(
    `/api/firm/issues${qs({ client_id: client, category, severity, status, period, page })}`), [client, category, severity, status, period, page]);
  const rows = group ? [...(data?.items ?? [])].sort((a, b) => a.client_name.localeCompare(b.client_name)) : data?.items ?? [];
  return (
    <div className="stack">
      <PageHead title="Error centre" subtitle="Findings from every client. Rule-detected and potential items still need a person's review." />
      <ErrorNotice error={error} onRetry={reload} />
      <div className="row" style={{ flexWrap: "wrap" }}>
        <ClientSelect value={client} onChange={setClient} />
        <select aria-label="Filter by category" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>{(data?.facets.categories ?? []).map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <input aria-label="Filter by period" placeholder="Period, e.g. FY 2025-26" value={period} onChange={(e) => setPeriod(e.target.value)} />
        <select aria-label="Filter by severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">All severities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
        </select>
        <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>{(data?.facets.statuses ?? []).map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <label className="checkbox"><input type="checkbox" checked={group} onChange={(e) => setGroup(e.target.checked)} /> Group by client</label>
      </div>
      {loading && !data ? <Spinner /> : rows.length === 0 ? <Empty title="No issues match" icon="check">No open issues were found in the data imported so far.</Empty> : <IssueTable issues={rows} open={open} groupByClient={group} />}
      {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </div>
  );
}

export function Imports() {
  const { companies } = useSession();
  const open = useOpen();
  const [client, setClient] = useState("");
  const [filterClient, setFilterClient] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [filterClient]);
  const { data, error, loading, reload } = useLoad(() => api<Paged<ImportRow>>(`/api/firm/imports${qs({ client_id: filterClient, page })}`), [filterClient, page]);
  const target = companies.find((c) => String(c.id) === client);
  const services = (target?.services ?? ["accounting", "audit"]) as string[];
  return (
    <div className="stack">
      <PageHead title="Integrations and imports" subtitle="Imported files, with client, type, status and record counts." />
      <ErrorNotice error={error} onRetry={reload} />
      <section className="card tight stack-sm">
        <h2>Import data for a client</h2>
        <p className="muted">Bank statements (CSV, XLSX, PDF) and registers are uploaded in the client's accounting workspace; trial balances and ledgers (including Tally exports in CSV, XLSX or voucher XML) in its audit workspace. Pushing posted entries to a running Tally is under Accounting → Reports. Every upload belongs to the client whose workspace it is made in.</p>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <select aria-label="Client to import for" value={client} onChange={(e) => setClient(e.target.value)}>
            <option value="">Choose a client…</option>{companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" disabled={!target || !services.includes("accounting")} onClick={() => target && open(target.id, "/accounting")}>Upload bank statement or register</button>
          <button className="btn" disabled={!target || !services.includes("audit")} onClick={() => target && open(target.id, "/audit")}>Upload trial balance or ledger</button>
        </div>
      </section>
      <div className="row"><ClientSelect value={filterClient} onChange={setFilterClient} /></div>
      {loading && !data ? <Spinner /> : !data || data.items.length === 0 ? <Empty title="No imports yet" icon="upload" /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Client</th><th>Source file</th><th>Type</th><th>Status</th><th className="num">Records</th><th>Notes</th><th>Imported</th></tr></thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.id} className="clickable" tabIndex={0} onClick={() => open(d.client_id, d.workspace === "audit" ? "/audit" : `/accounting/transactions?document_id=${d.id}`)}>
                  <td>{d.client_name}</td><td>{d.filename}</td><td>{label(d.kind)}</td><td><Badge status={d.status} /></td>
                  <td className="num">{d.record_count}</td>
                  <td className="muted">{d.status === "failed" ? d.error : d.status === "repeated_upload" ? "Duplicate of an earlier upload" : d.notes[0] ?? "—"}</td>
                  <td className="muted">{dateFmt(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </div>
  );
}
