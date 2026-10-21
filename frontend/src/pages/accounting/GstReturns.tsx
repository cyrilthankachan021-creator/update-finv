import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, dateFmt, label, money } from "../../api";
import { Badge, Empty, ErrorNotice, PageHead, Spinner } from "../../components/ui";
import { can, useLoad, useSession } from "../../session";

interface Warning { code: string; severity: "blocking" | "info"; message: string; count?: number; record_ids?: number[] }
interface Snapshot {
  id: number; version: number; gstin: string; return_type: string; period: string; rules_version: string;
  totals: Record<string, string | number>; breakdown: Record<string, Record<string, unknown>>; warnings: Warning[];
  reconciliation: Record<string, string | number> | null; payload_sha256: string; documents: number; tax_total: string; created_at: string;
}
interface Job { id: number; kind: string; state: string; steps: string[]; attempts: number; error: string | null;
  differences: { field: string; finv: string; portal: string | null }[] | null; command: string }
interface GstReturn {
  id: number; return_type: string; period: string; state: string; reason: string | null; snapshot: Snapshot | null;
  approved_snapshot_id: number | null; approved_by: string | null; approved_at: string | null;
  override: { reason: string; warnings: string[] } | null; arn: string | null; portal_status: string | null;
  filed_at: string | null; verified_at: string | null; reported_arn: string | null;
  prepare_job: Job | null; verify_job: Job | null; telegram_configured: boolean; can_open_window: boolean; window_opened?: boolean;
  history: { at: string; action: string; by: string; details: Record<string, unknown> }[];
}
interface Row { id: number; return_type: string; period: string; state: string; version: number | null; arn: string | null; tax_total: string | null; blocking: number }

const RT: Record<string, string> = { GSTR1: "GSTR-1", GSTR3B: "GSTR-3B" };
const STATE_TEXT: Record<string, string> = {
  prepared: "Prepared", awaiting_approval: "Awaiting approval", approved: "Approved", portal_in_progress: "Portal preparation running",
  portal_failed: "Portal preparation stopped", portal_ready_for_review: "Ready for your final review on the portal",
  filing_reported: "Filed — waiting for portal confirmation", verified: "Filed and verified", invalidated: "Data changed — prepare again",
  cancelled: "Cancelled",
};
const TONE: Record<string, string> = { approved: "approved", verified: "approved", portal_ready_for_review: "validated",
  portal_failed: "failed", invalidated: "needs_review", cancelled: "rejected", awaiting_approval: "needs_review", filing_reported: "proposed" };
const STEPS = ["prepared", "awaiting_approval", "approved", "portal_in_progress", "portal_ready_for_review", "filing_reported", "verified"];

function StateBadge({ state }: { state: string }) {
  return <Badge status={TONE[state] ?? "proposed"}>{STATE_TEXT[state] ?? label(state)}</Badge>;
}

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function GstReturns() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/gst`;
  const list = useLoad(() => api<Row[]>(`${base}/returns`), [company?.id]);
  const [rt, setRt] = useState("GSTR1");
  const [period, setPeriod] = useState(lastMonth());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const prepare = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const r = await api<GstReturn>(`${base}/returns`, { body: { return_type: rt, period } });
      nav(`/accounting/gst/${r.id}`);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="stack">
      <PageHead title="GST Returns" subtitle="FinV calculates the return from approved entries. An authorised person approves it, FinV puts it on the GST portal, and you file it there yourself." />
      {can(company!.role, "prepare") && (
        <form className="card tight row" onSubmit={prepare} style={{ flexWrap: "wrap" }}>
          <label className="row" style={{ gap: 6 }}>Return
            <select value={rt} onChange={(e) => setRt(e.target.value)}><option value="GSTR1">GSTR-1</option><option value="GSTR3B">GSTR-3B</option></select></label>
          <label className="row" style={{ gap: 6 }}>Tax period<input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} required /></label>
          <button className="btn primary" disabled={busy}>{busy ? "Preparing…" : "Prepare return"}</button>
        </form>
      )}
      <ErrorNotice error={err} />
      <ErrorNotice error={list.error} onRetry={list.reload} />
      {list.loading && !list.data ? <Spinner /> : list.data && list.data.length === 0 ? (
        <Empty title="No GST returns yet" icon="flag">Choose a return and tax period above to prepare one.</Empty>
      ) : list.data && (
        <div className="card tight table-wrap"><table>
          <thead><tr><th>Period</th><th>Return</th><th>Status</th><th className="num">Tax</th><th>Must resolve</th><th>ARN</th></tr></thead>
          <tbody>{list.data.map((r) => (
            <tr key={r.id}><td><Link to={`/accounting/gst/${r.id}`}>{r.period}</Link></td><td>{RT[r.return_type]}</td>
              <td><StateBadge state={r.state} /></td><td className="num">{money(r.tax_total)}</td>
              <td>{r.blocking ? <Badge status="warning">{r.blocking}</Badge> : "—"}</td><td>{r.arn ?? "—"}</td></tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Totals({ s }: { s: Snapshot }) {
  const rows: [string, string][] = s.return_type === "GSTR1"
    ? [["Taxable value", "taxable"], ["IGST", "igst"], ["CGST", "cgst"], ["SGST/UTGST", "sgst"], ["Cess", "cess"]]
    : [["3.1(a) Outward taxable value", "outward_taxable"], ["Output IGST", "output_igst"], ["Output CGST", "output_cgst"],
      ["Output SGST/UTGST", "output_sgst"], ["Output cess", "output_cess"], ["3.1(c) Nil rated / exempt", "nil_exempt"],
      ["ITC IGST — 4(A)(5)", "itc_igst"], ["ITC CGST", "itc_cgst"], ["ITC SGST/UTGST", "itc_sgst"], ["ITC cess", "itc_cess"]];
  return (
    <div className="table-wrap"><table>
      <tbody>{rows.map(([l, k]) => <tr key={k}><td>{l}</td><td className="num">{money(String(s.totals[k]))}</td></tr>)}</tbody>
    </table></div>
  );
}

function Breakdown({ s }: { s: Snapshot }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Section</th>{s.return_type === "GSTR1" && <th className="num">Documents</th>}<th className="num">Taxable</th><th className="num">IGST</th><th className="num">CGST</th><th className="num">SGST</th></tr></thead>
      <tbody>{Object.entries(s.breakdown).filter(([, v]) => v && typeof v === "object" && ("taxable" in v || "igst" in v)).map(([k, v]) => {
        const r = v as Record<string, string | number>;
        return <tr key={k}><td>{k.toUpperCase()}</td>{s.return_type === "GSTR1" && <td className="num">{r.count}</td>}
          <td className="num">{money(r.taxable as string)}</td><td className="num">{money(r.igst as string)}</td>
          <td className="num">{money(r.cgst as string)}</td><td className="num">{money(r.sgst as string)}</td></tr>;
      })}</tbody>
    </table></div>
  );
}

function JobPanel({ job, title, canOpen, onOpen, busy }: { job: Job; title: string; canOpen: boolean; onOpen: () => void; busy: boolean }) {
  return (
    <div className="stack-sm">
      <div className="row-between"><strong>{title}</strong><Badge status={job.state === "done" ? "approved" : job.state === "failed" ? "failed" : "processing"}>{label(job.state)}</Badge></div>
      {job.state !== "done" && canOpen && (
        <div className="row-between">
          <p className="muted" style={{ fontSize: 13 }}>FinV opens the GST login page in a separate browser window on this computer. Sign in there yourself (username, password, captcha, OTP); FinV continues once you are in and never sees them. Closed it by mistake? Open it again; finished steps are not repeated.</p>
          <button className="btn" disabled={busy} onClick={onOpen}>Open the GST portal window</button>
        </div>
      )}
      {job.state !== "done" && !canOpen && (
        <>
          <p className="muted" style={{ fontSize: 13 }}>Run this on this computer. A browser opens; sign in to the GST portal there yourself. FinV never sees your portal password or OTP. Running it again resumes from the last completed step.</p>
          <pre className="code" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 12, padding: 8, background: "var(--surface-2)", borderRadius: 6 }}>{job.command}</pre>
        </>
      )}
      {job.steps.length > 0 && <p className="muted" style={{ fontSize: 13 }}>Completed steps: {job.steps.map(label).join(", ")}</p>}
      {job.error && <div className="notice err">{job.error}</div>}
      {job.differences && job.differences.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Figure</th><th className="num">Approved in FinV</th><th className="num">On the portal</th></tr></thead>
          <tbody>{job.differences.map((d) => <tr key={d.field}><td>{label(d.field)}</td><td className="num">{money(d.finv)}</td><td className="num">{money(d.portal)}</td></tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}

function ApproveForm({ r, url, onDone, canOverride }: { r: GstReturn; url: string; onDone: (r: GstReturn) => void; canOverride: boolean }) {
  const s = r.snapshot!;
  const blocking = s.warnings.filter((w) => w.severity === "blocking");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      onDone(await api<GstReturn>(url, {
        body: { snapshot_id: s.id, version: s.version, payload_sha256: s.payload_sha256, password, override_reason: blocking.length ? reason : null } }));
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); setPassword(""); }
  };
  return (
    <form className="stack-sm" onSubmit={submit}>
      <h2>Approve version {s.version}</h2>
      {blocking.length > 0 && (canOverride ? (
        <label className="stack-sm">Override reason (required: {blocking.length} warning{blocking.length > 1 ? "s" : ""} must be resolved or overridden)
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required minLength={15}
            placeholder="What you checked, e.g. 'HSN summary entered on the portal from the sales register'" /></label>
      ) : <div className="notice warn">Resolve the warnings marked “must resolve”, or ask an owner or admin to approve with an override.</div>)}
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        I reviewed these figures for GSTIN ending {s.gstin.slice(-4)}, {RT[s.return_type]} {s.period}.</label>
      <label className="stack-sm">Your FinV password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      <ErrorNotice error={err} />
      <div><button className="btn primary" disabled={busy || !checked || (blocking.length > 0 && !canOverride)}>{busy ? "Approving…" : "Approve"}</button></div>
    </form>
  );
}

export function GstReturnDetail() {
  const { id } = useParams();
  const { company } = useSession();
  const base = `/api/companies/${company!.id}/gst/returns/${id}`;
  const ret = useLoad(() => api<GstReturn>(base), [company?.id, id]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [arn, setArn] = useState("");
  const nav = useNavigate();
  const running = ret.data && ["portal_in_progress", "filing_reported"].includes(ret.data.state);
  const timer = useRef<number>();
  useEffect(() => {   // keep the job status fresh while the browser job runs
    if (running) timer.current = window.setInterval(ret.reload, 5000);
    return () => window.clearInterval(timer.current);
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (path: string, body: unknown = {}, msg?: string) => {
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await api<GstReturn>(`${base}/${path}`, { body });
      ret.setData(r);
      if (r.window_opened) setNote("A browser window with the GST login page is opening on this computer. Sign in there yourself; FinV takes over after that.");
      else if (msg) setNote(msg);
    } catch (x) { setErr((x as Error).message); ret.reload(); } finally { setBusy(false); }
  };
  const reprepare = async () => {
    setBusy(true); setErr(null);
    try {
      ret.setData(await api<GstReturn>(`/api/companies/${company!.id}/gst/returns`, { body: { return_type: ret.data!.return_type, period: ret.data!.period } }));
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };

  if (ret.loading && !ret.data) return <Spinner />;
  if (ret.error && !ret.data) return <ErrorNotice error={ret.error} onRetry={ret.reload} />;
  const r = ret.data!;
  const s = r.snapshot;
  const role = company!.role;
  const stepAt = STEPS.indexOf(r.state === "portal_failed" ? "portal_in_progress" : r.state);
  return (
    <div className="stack">
      <PageHead title={`${RT[r.return_type]} · ${r.period}`} subtitle={s ? <>GSTIN {s.gstin} · version {s.version} · {s.documents} documents · prepared {dateFmt(s.created_at)}</> : null}>
        <button className="btn small" onClick={() => nav("/accounting/gst")}>All returns</button>
      </PageHead>
      <section className="card tight stack-sm">
        <div className="row-between"><StateBadge state={r.state} /><span className="muted" style={{ fontSize: 13 }}>FinV never submits, files, pays or signs. Those steps stay with you on the portal.</span></div>
        {stepAt >= 0 && <ol className="row muted" style={{ gap: 12, fontSize: 12, flexWrap: "wrap", listStyle: "none", padding: 0, margin: 0 }}>
          {STEPS.map((x, i) => <li key={x} style={{ fontWeight: i === stepAt ? 600 : 400, color: i <= stepAt ? "var(--text)" : undefined }}>{i + 1}. {STATE_TEXT[x]}</li>)}</ol>}
        {r.reason && <div className={`notice ${["invalidated", "portal_failed"].includes(r.state) ? "warn" : "info"}`}>{r.reason}</div>}
      </section>
      <ErrorNotice error={err} />
      {note && <div className="notice ok">{note}</div>}

      {s && <div className="two-col">
        <section className="card tight stack-sm"><h2>Totals</h2><Totals s={s} />
          {s.reconciliation && <p className="muted" style={{ fontSize: 13 }}>Bank reconciliation: {label(String(s.reconciliation.status))}
            {s.reconciliation.unmatched_bank !== undefined && ` · ${s.reconciliation.unmatched_bank} unmatched bank lines`}</p>}</section>
        <section className="card tight stack-sm"><h2>Warnings</h2>
          {s.warnings.length === 0 ? <p className="muted">None.</p> : <ul className="stack-sm" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {s.warnings.map((w) => <li key={w.code} className={`notice ${w.severity === "blocking" ? "warn" : "info"}`}>
              <div><Badge status={w.severity === "blocking" ? "warning" : "proposed"}>{w.severity === "blocking" ? "Must resolve" : "Information"}</Badge> {w.message}
                {w.record_ids && <div className="muted" style={{ fontSize: 12 }}>Records: {w.record_ids.slice(0, 12).map((rid) => <Link key={rid} to={`/accounting/transactions/${rid}`} style={{ marginRight: 6 }}>#{rid}</Link>)}</div>}</div></li>)}
          </ul>}
          {r.override && <div className="notice info">Override by {r.approved_by}: “{r.override.reason}”</div>}</section>
      </div>}
      {s && <section className="card tight stack-sm"><h2>By section</h2><Breakdown s={s} /></section>}

      {["prepared", "awaiting_approval"].includes(r.state) && s && (
        <section className="card tight stack">
          {can(role, "prepare") && <div className="row-between">
            <span className="muted" style={{ fontSize: 13 }}>{r.telegram_configured ? "Sends a short summary and a single-use review link by Telegram. No names or invoice details are sent." : "Telegram is not set up, so approve here in FinV (see the setup notes in README)."}</span>
            <button className="btn" disabled={busy} onClick={() => act("send", {}, r.telegram_configured ? "Sent by Telegram." : "Marked as awaiting approval.")}>Send for approval</button></div>}
          {can(role, "approve") && <ApproveForm r={r} url={`${base}/approve`} canOverride={["owner", "admin"].includes(role ?? "")} onDone={(x) => { ret.setData(x); setNote("Approved. You can now start portal preparation."); }} />}
        </section>
      )}
      {["approved", "portal_failed"].includes(r.state) && can(role, "approve") && (
        <section className="card tight row-between">
          <span>{r.state === "approved" ? `Approved by ${r.approved_by} on ${dateFmt(r.approved_at)}.` : "Fix the cause shown above, then resume. Steps already done are not repeated."}</span>
          <div className="row">
            {r.return_type === "GSTR1" && <a className="btn small" href={`${base}/payload.json`}>Download portal JSON</a>}
            <button className="btn primary" disabled={busy} onClick={() => act("portal-job")}>{r.state === "approved" ? "Start portal preparation" : "Resume portal preparation"}</button></div>
        </section>
      )}
      {r.prepare_job && ["portal_in_progress", "portal_failed", "portal_ready_for_review"].includes(r.state) && (
        <section className="card tight"><JobPanel job={r.prepare_job} title="Portal preparation" canOpen={r.can_open_window} busy={busy} onOpen={() => act("open-window")} /></section>)}
      {r.state === "portal_ready_for_review" && can(role, "approve") && (
        <section className="card tight stack-sm">
          <h2>Your final review and filing</h2>
          <p>The data is on the portal and its totals match version {s?.version}. On the portal: check the summary, then file with EVC or DSC and make any payment yourself. FinV did not and will not do these steps.</p>
          <form className="row" style={{ flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); act("filed", { arn: arn || null }, "Recorded. Now let FinV check the portal to confirm it."); }}>
            <label className="row" style={{ gap: 6 }}>ARN (optional)<input value={arn} onChange={(e) => setArn(e.target.value.toUpperCase())} maxLength={20} /></label>
            <button className="btn primary" disabled={busy}>I have filed it on the portal</button>
          </form>
        </section>
      )}
      {r.state === "filing_reported" && (
        <section className="card tight stack-sm">
          <p>FinV shows “filed” only after it reads the acknowledgement from the portal.{r.reported_arn && ` You reported ARN ${r.reported_arn}.`}</p>
          {r.verify_job && <JobPanel job={r.verify_job} title="Check filing on the portal" canOpen={r.can_open_window} busy={busy} onOpen={() => act("open-window")} />}
          {r.verify_job?.state === "failed" && <div><button className="btn" disabled={busy} onClick={() => act("filed", {})}>Check again</button></div>}
        </section>
      )}
      {r.state === "verified" && (
        <section className="card tight"><dl className="kv">
          <dt>ARN</dt><dd>{r.arn}</dd><dt>Portal status</dt><dd>{r.portal_status}</dd><dt>Filed on (portal)</dt><dd>{r.filed_at ?? "—"}</dd><dt>Verified</dt><dd>{dateFmt(r.verified_at)}</dd>
        </dl></section>
      )}
      {["invalidated", "cancelled", "portal_failed", "portal_ready_for_review", "approved", "awaiting_approval", "prepared"].includes(r.state) && can(role, "prepare") && (
        <div className="row">
          <button className="btn small" disabled={busy} onClick={reprepare}>{r.state === "invalidated" ? "Prepare again with current data" : "Recalculate"}</button>
          {can(role, "approve") && !["cancelled", "invalidated"].includes(r.state) && <button className="btn small ghost" disabled={busy} onClick={() => {
            const why = window.prompt("Why cancel this return?");
            if (why) act("cancel", { reason: why });
          }}>Cancel return</button>}
        </div>
      )}
      <section className="card tight stack-sm"><h2>History</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Time</th><th>By</th><th>Event</th><th>Note</th></tr></thead>
          <tbody>{r.history.map((h, i) => <tr key={i}><td className="muted" style={{ whiteSpace: "nowrap" }}>{dateFmt(h.at)}</td><td>{h.by}</td>
            <td>{label(h.action.replace("gst.", ""))}</td><td className="muted" style={{ fontSize: 12 }}>{String(h.details?.reason ?? h.details?.step ?? h.details?.error ?? "")}</td></tr>)}</tbody>
        </table></div>
      </section>
    </div>
  );
}

/** Target of the Telegram link: needs a signed-in member; opens the return, never approves it. */
export function GstReviewLink() {
  const [params] = useSearchParams();
  const { selectCompany } = useSession();
  const [to, setTo] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    api<{ company_id: number; return_id: number }>("/api/gst/review-links/redeem", { body: { token: params.get("t") ?? "" } })
      .then((r) => { selectCompany(r.company_id); setTo(`/accounting/gst/${r.return_id}`); })
      .catch((x) => setErr(x instanceof ApiError ? x.message : "This review link could not be opened."));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (to) return <Navigate to={to} replace />;
  return <div className="auth-wrap">{err ? <div className="stack"><ErrorNotice error={err} /><Link to="/">Go to FinV</Link></div> : <Spinner text="Opening the return…" />}</div>;
}
