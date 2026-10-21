import { FormEvent, useState } from "react";
import { useUnsavedChanges } from "../components/Unsaved";
import { Link } from "react-router-dom";
import { api, dateFmt, label } from "../api";
import { Assistant } from "../components/Assistant";
import { TopBar } from "../components/Shell";
import { Badge, ErrorNotice, Help, PageHead, Spinner, TabList } from "../components/ui";
import { can, useLoad, useSession } from "../session";
import { servicesOf, type Account, type Policy } from "../types";
import { ServicePicker } from "./Home";

const TABS: [string, string][] = [["general", "Company & periods"], ["accounts", "Chart of accounts"], ["policies", "Accounting policies"],
  ["privacy", "Privacy & AI"], ["retention", "Retention"], ["team", "Team"]] as const;

export function Settings() {
  const { company } = useSession();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("general");
  if (!company) return <><TopBar /><div className="main"><p>Select a company first. <Link to="/">Go home</Link></p></div></>;
  return (
    <>
      <TopBar workspace="Settings" />
      <main className="main" style={{ margin: "0 auto" }}>
        <PageHead title="Settings" subtitle={`${company.name} · your role: ${label(company.role)}`} />
        <TabList items={TABS} value={tab} label="Settings sections" onChange={(v) => setTab(v as typeof tab)} />
        {tab === "general" && <General />}
        {tab === "accounts" && <Accounts />}
        {tab === "policies" && <Policies />}
        {tab === "privacy" && <Privacy />}
        {tab === "retention" && <Retention />}
        {tab === "team" && <Team />}
      </main>
      <Assistant workspace="accounting" />
    </>
  );
}

interface Period { id: number; name: string; start_date: string; end_date: string; locked: boolean }
interface CSettings { privacy_mode: string; retention_days: number | null; materiality_amount: string; bank_match_window_days: number; vouching_threshold: string; gst_filing: string; tds_applicable: boolean; tan: string | null }

function useSettings() {
  const { company } = useSession();
  return useLoad(() => api<CSettings>(`/api/companies/${company!.id}/settings`), [company?.id]);
}

function General() {
  const { company, refresh } = useSession();
  const base = `/api/companies/${company!.id}`;
  const periods = useLoad(() => api<Period[]>(`${base}/periods`), [company?.id]);
  const st = useSettings();
  const admin = can(company!.role, "admin");
  const [p, setP] = useState({ name: "", start_date: "", end_date: "" });
  const prompt = useUnsavedChanges(!!(p.name || p.start_date || p.end_date));
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, ok?: string) => { setErr(null); setMsg(null); try { await fn(); periods.reload(); st.reload(); if (ok) setMsg(ok); } catch (e) { setErr((e as Error).message); } };
  return (
    <div className="stack">
      <section className="card stack-sm">
        <h2>Company</h2>
        <dl className="kv"><dt>Name</dt><dd>{company!.name}</dd><dt>GSTIN</dt><dd className="mono">{company!.gstin ?? "—"}</dd><dt>State code</dt><dd>{company!.state_code ?? "—"}</dd></dl>
      </section>
      <section className="card stack-sm">
        <h2>Services</h2>
        <p className="muted" style={{ fontSize: 13 }}>Turning a service off hides its workspace for this company. No data is deleted.</p>
        <ServicePicker value={servicesOf(company)} disabled={!admin}
          onChange={(v) => run(async () => { await api(`${base}/services`, { method: "PUT", body: { services: v } }); await refresh(); }, "Services updated.")} />
      </section>
      <ErrorNotice error={err} />
      {msg && <div className="notice ok">{msg}</div>}
      <section className="card stack-sm">
        <h2>Accounting periods</h2>
        <p className="muted">Entries dated outside a defined period need review; entries in a locked period cannot be posted.</p>
        {periods.loading && !periods.data ? <Spinner /> : (
          <div className="table-wrap"><table><thead><tr><th>Period</th><th>From</th><th>To</th><th>Status</th><th /></tr></thead>
            <tbody>{periods.data?.map((x) => (
              <tr key={x.id}><td>{x.name}</td><td>{dateFmt(x.start_date)}</td><td>{dateFmt(x.end_date)}</td><td><Badge status={x.locked ? "rejected" : "validated"}>{x.locked ? "Locked" : "Open"}</Badge></td>
                <td>{admin && <button className="btn small" onClick={() => run(() => api(`${base}/periods/${x.id}/lock`, { body: { locked: !x.locked } }))}>{x.locked ? "Unlock" : "Lock"}</button>}</td></tr>))}</tbody></table></div>
        )}
        {admin && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api(`${base}/periods`, { body: p }), "Period added."); }}>{prompt}
            <input aria-label="Period name" placeholder="FY 2025-26" required value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} />
            <input aria-label="Start date" type="date" required value={p.start_date} onChange={(e) => setP({ ...p, start_date: e.target.value })} />
            <input aria-label="End date" type="date" required value={p.end_date} onChange={(e) => setP({ ...p, end_date: e.target.value })} />
            <button className="btn">Add period</button>
          </form>
        )}
      </section>
      {st.data && (
        <section className="card stack-sm">
          <h2>Thresholds</h2>
          <ThresholdForm s={st.data} admin={admin} onSave={(body) => run(() => api(`${base}/settings`, { method: "PATCH", body }), "Saved.")} />
        </section>
      )}
      {admin && <Assignees companyId={company!.id} orgId={company!.organization_id} />}
      {st.data && (
        <section className="card stack-sm">
          <h2>GST and TDS setup</h2>
          <p className="muted">This decides which due dates FinV shows for this client on the firm dashboard.</p>
          <TaxSetupForm s={st.data} admin={admin} onSave={(body) => run(() => api(`${base}/settings`, { method: "PATCH", body }), "Saved.")} />
        </section>
      )}
    </div>
  );
}

function Assignees({ companyId, orgId }: { companyId: number; orgId: number }) {
  const members = useLoad(() => api<{ user_id: number; name: string; email: string; role: string }[]>(`/api/organizations/${orgId}/members`), [orgId]);
  const cur = useLoad(() => api<{ user_ids: number[]; restricted: boolean }>(`/api/companies/${companyId}/assignees`), [companyId]);
  const [picked, setPicked] = useState<number[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sel = picked ?? cur.data?.user_ids ?? [];
  const save = async (ids: number[]) => { setErr(null); setMsg(null); try { await api(`/api/companies/${companyId}/assignees`, { method: "PUT", body: { user_ids: ids } }); setPicked(null); cur.reload(); setMsg(ids.length ? "Saved. Only these people, and the firm's owners and admins, can open this client." : "Saved. Everyone in the firm can open this client."); } catch (e) { setErr((e as Error).message); } };
  return (
    <section className="card stack-sm">
      <h2>Who works on this client <Help text="Leave everyone unticked and the whole firm can open this client. Tick people and only they, plus the firm's owners and admins, can." /></h2>
      <ErrorNotice error={err} />
      {msg && <div className="notice ok">{msg}</div>}
      {members.data?.map((m) => (
        <label key={m.user_id} className="row"><input type="checkbox" checked={sel.includes(m.user_id)}
          onChange={(e) => setPicked(e.target.checked ? [...sel, m.user_id] : sel.filter((x) => x !== m.user_id))} />{m.name} <span className="muted">{label(m.role)}</span></label>))}
      <div className="row"><button className="btn" disabled={picked === null} onClick={() => save(sel)}>Save</button>
        {sel.length > 0 && <button className="btn ghost" onClick={() => save([])}>Open to everyone</button>}</div>
    </section>
  );
}

function TaxSetupForm({ s, admin, onSave }: { s: CSettings; admin: boolean; onSave: (b: Record<string, unknown>) => void }) {
  const [v, setV] = useState({ gst_filing: s.gst_filing, tds_applicable: s.tds_applicable, tan: s.tan ?? "" });
  const prompt = useUnsavedChanges(v.gst_filing !== s.gst_filing || v.tds_applicable !== s.tds_applicable || v.tan !== (s.tan ?? ""));
  return (
    <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); onSave({ gst_filing: v.gst_filing, tds_applicable: v.tds_applicable, ...(v.tan ? { tan: v.tan } : {}) }); }}>
      {prompt}
      <div className="form-grid">
        <label className="field">GST return filing <Help text="Monthly: GSTR-1 and GSTR-3B every month. QRMP: returns are filed each quarter, with tax paid monthly. FinV then shows due dates for quarter-end months only." />
          <select disabled={!admin} value={v.gst_filing} onChange={(e) => setV({ ...v, gst_filing: e.target.value })}>
            <option value="monthly">Monthly</option><option value="qrmp">Quarterly (QRMP)</option></select></label>
        <label className="field">TDS applies to this client
          <select disabled={!admin} value={v.tds_applicable ? "yes" : "no"} onChange={(e) => setV({ ...v, tds_applicable: e.target.value === "yes" })}>
            <option value="no">No</option><option value="yes">Yes</option></select></label>
        <label className="field">TAN <span className="hint">Ten characters, e.g. DELA12345B.</span>
          <input disabled={!admin} maxLength={10} pattern="[A-Za-z]{4}[0-9]{5}[A-Za-z]" value={v.tan} onChange={(e) => setV({ ...v, tan: e.target.value.toUpperCase() })} /></label>
      </div>
      {admin && <div><button className="btn">Save tax setup</button></div>}
    </form>
  );
}

function ThresholdForm({ s, admin, onSave }: { s: CSettings; admin: boolean; onSave: (b: Record<string, unknown>) => void }) {
  const [v, setV] = useState({ materiality_amount: s.materiality_amount, vouching_threshold: s.vouching_threshold, bank_match_window_days: String(s.bank_match_window_days) });
  return (
    <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); onSave({ ...v, bank_match_window_days: Number(v.bank_match_window_days) }); }}>
      <div className="form-grid">
        <label className="field">Audit materiality (₹)<input disabled={!admin} inputMode="decimal" value={v.materiality_amount} onChange={(e) => setV({ ...v, materiality_amount: e.target.value })} /></label>
        <label className="field">Vouching threshold (₹)<span className="hint">Ledger entries at or above this need evidence.</span><input disabled={!admin} inputMode="decimal" value={v.vouching_threshold} onChange={(e) => setV({ ...v, vouching_threshold: e.target.value })} /></label>
        <label className="field">Bank match window (days)<input disabled={!admin} type="number" min={0} max={30} value={v.bank_match_window_days} onChange={(e) => setV({ ...v, bank_match_window_days: e.target.value })} /></label>
      </div>
      {admin && <div><button className="btn">Save thresholds</button></div>}
    </form>
  );
}

const ROLE_OPTIONS = ["", "bank", "cash", "receivables", "payables", "sales", "sales_returns", "purchases", "purchase_returns",
  "input_cgst", "input_sgst", "input_igst", "input_cess", "output_cgst", "output_sgst", "output_igst", "output_cess", "tds_payable", "tds_receivable", "round_off"];

function Accounts() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const accts = useLoad(() => api<Account[]>(`${base}/accounts`), [company?.id]);
  const admin = can(company!.role, "admin");
  const [n, setN] = useState({ code: "", name: "", type: "expense", role: "" });
  const prompt = useUnsavedChanges(!!(n.code || n.name || n.role));
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); accts.reload(); } catch (e) { setErr((e as Error).message); } };
  return (
    <section className="card stack-sm">
      <h2>Chart of accounts</h2>
      <p className="muted">The role tells FinV which ledger plays which part (for example the bank ledger or input CGST). Each role should be used by one active ledger.</p>
      <ErrorNotice error={err} />
      {admin && (
        <form className="row" onSubmit={(e: FormEvent) => { e.preventDefault(); run(() => api(`${base}/accounts`, { body: { ...n, role: n.role || null } })); setN({ code: "", name: "", type: "expense", role: "" }); }}>{prompt}
          <input aria-label="Code" placeholder="Code" required style={{ width: 90 }} value={n.code} onChange={(e) => setN({ ...n, code: e.target.value })} />
          <input aria-label="Name" placeholder="Ledger name" required className="grow" value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} />
          <select aria-label="Type" value={n.type} onChange={(e) => setN({ ...n, type: e.target.value })}>{["asset", "liability", "equity", "income", "expense"].map((t) => <option key={t}>{t}</option>)}</select>
          <select aria-label="Role" value={n.role} onChange={(e) => setN({ ...n, role: e.target.value })}>{ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r ? label(r) : "No role"}</option>)}</select>
          <button className="btn">Add ledger</button>
        </form>
      )}
      {accts.loading && !accts.data ? <Spinner /> : (
        <div className="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>Type</th><th>Role</th><th>Status</th><th /></tr></thead>
          <tbody>{accts.data?.map((a) => (
            <tr key={a.id}><td className="mono">{a.code}</td><td>{a.name}</td><td>{label(a.type)}</td><td className="muted">{a.role ? label(a.role) : "—"}</td>
              <td><Badge status={a.is_active ? "validated" : "rejected"}>{a.is_active ? "Active" : "Inactive"}</Badge></td>
              <td>{admin && <button className="btn ghost small" onClick={() => run(() => api(`${base}/accounts/${a.id}`, { method: "PATCH", body: { code: a.code, name: a.name, type: a.type, role: a.role, is_active: !a.is_active } }))}>{a.is_active ? "Deactivate" : "Activate"}</button>}</td></tr>))}</tbody></table></div>
      )}
    </section>
  );
}

function Policies() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const pols = useLoad(() => api<Policy[]>(`${base}/policies`), [company?.id]);
  const [kind, setKind] = useState("bank_keyword");
  const [d, setD] = useState<Record<string, string>>({});
  const [rationale, setRationale] = useState("");
  const prompt = useUnsavedChanges(!!(rationale || Object.values(d).some(Boolean)));
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); pols.reload(); } catch (e) { setErr((e as Error).message); } };
  const fields: Record<string, [string, string, string?][]> = {
    bank_keyword: [["keyword", "Description contains"], ["account_code", "Ledger code"], ["direction", "Direction", "in = money received, out = money paid, blank = both"]],
    party_account: [["gstin", "Party GSTIN"], ["party", "Party name"], ["account_code", "Ledger code"]],
    tds_category: [["account_code", "Expense ledger code"], ["section_label", "TDS section (as your firm applies it)"], ["rate_percent", "Rate % (verified by your firm)"]],
  };
  return (
    <div className="stack">
      <div className="notice info">Policies are company-specific. A proposed policy has no effect until an owner or admin approves it. Each change creates a new version; disabled and superseded versions stay in the history. FinV does not ship statutory TDS rates: enter only rates your firm has verified.</div>
      <ErrorNotice error={err} />
      {can(company!.role, "prepare") && (
        <section className="card stack-sm">
          <h2>Propose a policy</h2>
          <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); run(() => api(`${base}/policies`, { body: { kind, definition: d, rationale } })); setD({}); setRationale(""); }}>{prompt}
            <label className="field">Type<select value={kind} onChange={(e) => { setKind(e.target.value); setD({}); }}>
              <option value="bank_keyword">Bank description keyword → ledger</option><option value="party_account">Supplier → expense ledger</option><option value="tds_category">TDS on an expense ledger</option></select></label>
            <div className="form-grid">{fields[kind].map(([k, l, h]) => (
              <label key={k} className="field">{l}{h && <span className="hint">{h}</span>}<input value={d[k] ?? ""} onChange={(e) => setD({ ...d, [k]: e.target.value })} /></label>))}</div>
            <label className="field">Rationale<textarea required value={rationale} onChange={(e) => setRationale(e.target.value)} /></label>
            <div><button className="btn">Propose policy</button></div>
          </form>
        </section>
      )}
      <section className="card stack-sm">
        <h2>Policies</h2>
        {pols.loading && !pols.data ? <Spinner /> : pols.data?.length === 0 ? <p className="muted">No policies yet. Corrections you make may be suggested here as proposed policies.</p> : (
          <div className="table-wrap"><table><thead><tr><th>Policy</th><th>Definition</th><th>Source</th><th>Status</th><th /></tr></thead>
            <tbody>{pols.data?.map((p) => (
              <tr key={p.id}><td><div>{label(p.kind)}</div><div className="faint mono">{p.key} · v{p.version}</div></td>
                <td className="muted" style={{ fontSize: 13 }}>{Object.entries(p.definition).filter(([, v]) => v).map(([k, v]) => `${label(k)}: ${v}`).join(" · ")}<div>{p.rationale}</div></td>
                <td className="muted">{label(p.source)}</td><td><Badge status={p.status} /></td>
                <td>{can(company!.role, "admin") && <div className="row">
                  {p.status === "proposed" && <button className="btn small" onClick={() => run(() => api(`${base}/policies/${p.id}/decision`, { body: { decision: "approve" } }))}>Approve</button>}
                  {["proposed", "approved"].includes(p.status) && <button className="btn ghost small" onClick={() => run(() => api(`${base}/policies/${p.id}/decision`, { body: { decision: "disable" } }))}>Disable</button>}
                </div>}</td></tr>))}</tbody></table></div>
        )}
      </section>
    </div>
  );
}

interface AITest { ok: boolean; stage: string; message: string; sensible?: boolean; provider?: string; model?: string; seconds?: number; runs_on_this_machine?: boolean; sent?: string }

interface PrivacyInfo {
  privacy_mode: string; ai: { provider: string; model: string | null; credentials_present: boolean; runs_on_this_machine?: boolean; endpoint?: string };
  chat?: { configured: boolean; label?: string; model?: string; missing?: string | null; signup?: string };
  data_flow: { stays_local: string[]; may_leave_when_enabled: string[]; never_sent: string[] };
  recent_ai_requests: { id: number; provider: string; model: string | null; status: string; error: string | null; sent_payload: unknown; created_at: string }[];
}

function Privacy() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const info = useLoad(() => api<PrivacyInfo>(`${base}/privacy`), [company?.id]);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<AITest | null>(null);
  const [testing, setTesting] = useState(false);
  const runTest = async () => {
    setTesting(true); setTest(null); setErr(null);
    try { setTest(await api<AITest>(`${base}/ai/test`, { method: "POST" })); info.reload(); }
    catch (e) { setErr((e as Error).message); } finally { setTesting(false); }
  };
  const setMode = async (mode: string) => { setErr(null); try { await api(`${base}/settings`, { method: "PATCH", body: { privacy_mode: mode } }); info.reload(); } catch (e) { setErr((e as Error).message); } };
  if (info.loading && !info.data) return <Spinner />;
  const d = info.data!;
  return (
    <div className="stack">
      <ErrorNotice error={err ?? info.error} />
      <section className="card stack-sm">
        <h2>Processing mode</h2>
        <label className="checkbox"><input type="radio" name="pm" checked={d.privacy_mode === "local_only"} disabled={!can(company!.role, "admin")} onChange={() => setMode("local_only")} />
          <span><strong>Local only</strong> (default). No client data is sent to any AI service. All checks run on this machine. To understand chat messages, the assistant sends only what you type, with names, identifiers and amounts removed.</span></label>
        {d.privacy_mode === "local_ai" && <label className="checkbox"><input type="radio" name="pm" checked disabled />
          <span><strong>AI on this machine only</strong> (no longer available). FinV no longer runs a model on this computer, so AI is off for this company. Choose another option to use AI.</span></label>}
        <label className="checkbox"><input type="radio" name="pm" checked={d.privacy_mode === "pseudonymized_ai"} disabled={!can(company!.role, "admin")} onChange={() => setMode("pseudonymized_ai")} />
          <span><strong>Pseudonymised external AI.</strong> When you ask for a suggestion on a bank line, a minimised, pseudonymised description is sent to the configured provider ({d.ai.provider === "none" ? "not configured" : d.ai.provider}). Pseudonymisation is not anonymisation: amounts and dates can still be identifying.</span></label>
        <label className="checkbox"><input type="radio" name="pm" checked={d.privacy_mode === "cloud_ai"} disabled={!can(company!.role, "admin")} onChange={() => setMode("cloud_ai")} />
          <span><strong>Cloud AI for the assistant{d.chat?.label ? ` (${d.chat.label})` : ""}.</strong> Fast replies from a cloud service. <strong>Client data in the chat (names, amounts, entries) is sent to that service.</strong> Use only if your firm accepts this for this client.</span></label>
        {d.chat && <p className="muted">Chat assistant: {!d.chat.label ? "not configured (set FINV_CHAT_PROVIDER on the server)" :
          `${d.chat.label} · ${d.chat.model ?? ""} · ${d.chat.configured ? "API key present" : `API key missing (add ${d.chat.missing} to backend/.env)`}`}</p>}
        <p className="muted">AI provider: {d.ai.provider === "none" ? "not configured (set FINV_AI_PROVIDER on the server)" :
          `${d.ai.provider} · ${d.ai.model ?? ""} · credentials ${d.ai.credentials_present ? "present" : "missing"}`}</p>
        {can(company!.role, "admin") && (
          <div className="stack-sm">
            <div className="row"><button className="btn" disabled={testing} onClick={runTest}>{testing ? "Testing…" : "Test AI connection"}</button>
              <span className="faint" style={{ fontSize: 12 }}>Sends a made-up sample transaction only, never client data.</span></div>
            {test && (
              <div className={`notice ${!test.ok ? "err" : test.sensible === false ? "warn" : "ok"}`} role="status">
                <div>{test.message}{test.seconds !== undefined && <span className="muted"> ({test.seconds} s{test.model ? `, ${test.provider} ${test.model}` : ""})</span>}</div>
              </div>
            )}
          </div>
        )}
      </section>
      <section className="card stack-sm">
        <h2>Data flow</h2>
        <h3>Stays on this machine</h3><ul>{d.data_flow.stays_local.map((x) => <li key={x}>{x}</li>)}</ul>
        <h3>May leave only when AI assistance is enabled and requested</h3><ul>{d.data_flow.may_leave_when_enabled.map((x) => <li key={x}>{x}</li>)}</ul>
        <h3>Never sent</h3><ul>{d.data_flow.never_sent.map((x) => <li key={x}>{x}</li>)}</ul>
      </section>
      <section className="card stack-sm">
        <h2>AI requests</h2>
        {d.recent_ai_requests.length === 0 ? <p className="muted">No AI requests have been made for this company.</p> : (
          <div className="table-wrap"><table><thead><tr><th>Time</th><th>Provider</th><th>Status</th><th>Exactly what was sent</th></tr></thead>
            <tbody>{d.recent_ai_requests.map((r) => (
              <tr key={r.id}><td className="muted">{dateFmt(r.created_at)}</td><td>{r.provider} {r.model}</td><td><Badge status={r.status === "ok" ? "pass" : "error"}>{label(r.status)}</Badge>{r.error && <div className="muted" style={{ fontSize: 12 }}>{r.error}</div>}</td>
                <td><details><summary>View payload</summary><pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{r.sent_payload ? JSON.stringify(r.sent_payload, null, 2) : "Nothing was sent."}</pre></details></td></tr>))}</tbody></table></div>
        )}
      </section>
    </div>
  );
}

function Retention() {
  const { company } = useSession();
  const base = `/api/companies/${company!.id}`;
  const st = useSettings();
  const [days, setDays] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const admin = can(company!.role, "admin");
  const run = async (fn: () => Promise<{ purged?: number } | unknown>, ok: (r: unknown) => string) => { setErr(null); setMsg(null); try { const r = await fn(); st.reload(); setMsg(ok(r)); } catch (e) { setErr((e as Error).message); } };
  return (
    <section className="card stack-sm">
      <h2>Document retention</h2>
      <p className="muted">Uploaded files are stored encrypted on this machine. Applying retention permanently deletes files older than the period. Extracted records behind approved or posted entries are kept as accounting evidence; other records from the file are deleted. Every deletion is logged.</p>
      <ErrorNotice error={err} />
      {msg && <div className="notice ok">{msg}</div>}
      <p>Current setting: <strong>{st.data?.retention_days ? `${st.data.retention_days} days` : "Keep until deleted manually"}</strong></p>
      {admin && (
        <div className="row">
          <input aria-label="Retention days" type="number" min={1} placeholder="Days" style={{ width: 120 }} value={days} onChange={(e) => setDays(e.target.value)} />
          <button className="btn" disabled={!days} onClick={() => run(() => api(`${base}/settings`, { method: "PATCH", body: { retention_days: Number(days) } }), () => "Retention period saved.")}>Save</button>
          <button className="btn ghost" onClick={() => run(() => api(`${base}/settings`, { method: "PATCH", body: { clear_retention: true } }), () => "Retention period cleared.")}>Clear</button>
          <button className="btn danger" disabled={!st.data?.retention_days} onClick={() => run(() => api(`${base}/retention/apply`, { method: "POST" }), (r) => `${(r as { purged: number }).purged} document(s) deleted.`)}>Apply retention now</button>
        </div>
      )}
    </section>
  );
}

function Team() {
  const { company } = useSession();
  const org = company!.organization_id;
  const members = useLoad(() => api<{ user_id: number; name: string; email: string; role: string }[]>(`/api/organizations/${org}/members`), [org]);
  const [email, setEmail] = useState("");
  const prompt = useUnsavedChanges(!!email);
  const [role, setRole] = useState("accountant");
  const [err, setErr] = useState<string | null>(null);
  const admin = can(company!.role, "admin");
  const add = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api(`/api/organizations/${org}/members`, { body: { email, role } }); setEmail(""); members.reload(); } catch (ex) { setErr((ex as Error).message); } };
  return (
    <section className="card stack-sm">
      <h2>Team</h2>
      <p className="muted">Roles: accountants prepare and correct entries; reviewers approve and post; auditors record audit decisions; viewers read only. Someone who corrects an entry cannot approve it themselves (except the owner).</p>
      <ErrorNotice error={err} />
      {admin && (
        <form className="row" onSubmit={add}>{prompt}
          <input aria-label="Email" type="email" required placeholder="colleague@firm.example" className="grow" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>{["admin", "accountant", "reviewer", "auditor", "viewer"].map((r) => <option key={r}>{r}</option>)}</select>
          <button className="btn">Add member</button>
        </form>
      )}
      <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
        <tbody>{members.data?.map((m) => <tr key={m.user_id}><td>{m.name}</td><td>{m.email}</td><td>{label(m.role)}</td></tr>)}</tbody></table></div>
    </section>
  );
}
