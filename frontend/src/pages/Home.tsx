import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { TopBar } from "../components/Shell";
import { ErrorNotice, Icon } from "../components/ui";
import { useUnsavedChanges } from "../components/Unsaved";
import { useSession } from "../session";
import { servicesOf, type Company, type Service } from "../types";

const WORK: Record<Service, { title: string; icon: string; text: string; path: string }> = {
  accounting: { title: "Company", icon: "ledger", text: "Prepare accurate entries and catch mistakes before posting.", path: "/accounting" },
  audit: { title: "CA", icon: "shield", text: "Review financial records, verify evidence and identify anomalies.", path: "/audit" },
};
const SERVICE_TEXT: Record<Service, string> = { accounting: "Company", audit: "CA" };

/** The first screen: the firm's client companies, then the work (Accounting and/or Audit) for the selected one. */
export function Home() {
  const { me, company, companies } = useSession();
  const nav = useNavigate();
  const [adding, setAdding] = useState(false);
  const first = me?.name.split(" ")[0];

  if (!company || adding) {
    return (
      <>
        <TopBar />
        <div className="landing">
          <div className="landing-inner" style={{ maxWidth: 560 }}>
            {companies.length > 0 && !adding ? <SelectCompany onAdd={() => setAdding(true)} />
              : <CreateCompany onCancel={companies.length ? () => setAdding(false) : undefined} onDone={() => setAdding(false)} />}
          </div>
        </div>
      </>
    );
  }

  const services = servicesOf(company);
  return (
    <>
      <TopBar />
      <div className="landing">
        <div className="landing-inner">
          <h1>Welcome to FinV{first ? `, ${first}` : ""}</h1>
          <p className="muted" style={{ marginTop: 6 }}>What would you like to work on today?</p>
          <p className="faint" style={{ marginTop: 4, fontSize: 13 }}>{company.name}</p>
          <div className="choices" style={services.length === 1 ? { gridTemplateColumns: "1fr", maxWidth: 372, marginInline: "auto" } : undefined}>
            {services.map((s) => (
              <button key={s} className="choice" onClick={() => nav(WORK[s].path)} aria-label={`${WORK[s].title}: ${WORK[s].text}`}>
                <div className="icon-wrap"><Icon name={WORK[s].icon} /></div>
                <h2>{WORK[s].title}</h2>
                <p className="muted">{WORK[s].text}</p>
              </button>
            ))}
          </div>
          <ClientList onAdd={() => setAdding(true)} />
        </div>
      </div>
    </>
  );
}

/** All client companies of the firm, to switch between them or add another. */
function ClientList({ onAdd }: { onAdd: () => void }) {
  const { me, company, companies, selectCompany } = useSession();
  const canAdd = (me?.organizations ?? []).some((o) => o.role === "owner" || o.role === "admin");
  return (
    <section className="card stack-sm" style={{ marginTop: 32, textAlign: "left" }} aria-label="Client companies">
      <div className="row-between">
        <h2>Client companies <span className="faint" style={{ fontWeight: 400 }}>({companies.length})</span></h2>
        {canAdd && <button className="btn small" onClick={onAdd}>Add company</button>}
      </div>
      <ul className="stack-sm" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {companies.map((c) => (
          <li key={c.id}>
            <button className="btn" aria-current={c.id === company?.id ? "true" : undefined}
              style={{ width: "100%", justifyContent: "space-between", borderColor: c.id === company?.id ? "var(--accent)" : undefined }}
              onClick={() => selectCompany(c.id)} aria-label={`Switch to ${c.name}`}>
              <span>{c.name}{c.id === company?.id && <span className="faint"> · selected</span>}</span>
              <span className="row" style={{ gap: 8 }}>
                <span className="faint mono" style={{ fontSize: 12 }}>{c.gstin ?? ""}</span>
                {servicesOf(c).map((s) => <span key={s} className="badge">{SERVICE_TEXT[s]}</span>)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SelectCompany({ onAdd }: { onAdd: () => void }) {
  const { me, companies, selectCompany } = useSession();
  const canAdd = (me?.organizations ?? []).some((o) => o.role === "owner" || o.role === "admin");
  return (
    <div className="card stack" style={{ textAlign: "left" }}>
      <div><h1>Select a company</h1><p className="muted">Choose the client company you want to work on.</p></div>
      <div className="stack-sm">
        {companies.map((c) => (
          <button key={c.id} className="btn" style={{ justifyContent: "space-between", width: "100%" }} onClick={() => selectCompany(c.id)}>
            <span>{c.name}</span>
            <span className="row" style={{ gap: 8 }}><span className="faint mono">{c.gstin ?? ""}</span>
              {servicesOf(c).map((s) => <span key={s} className="badge">{SERVICE_TEXT[s]}</span>)}</span>
          </button>
        ))}
      </div>
      {canAdd && <button className="btn ghost" onClick={onAdd}>Add another company</button>}
    </div>
  );
}

export function ServicePicker({ value, onChange, disabled }: { value: Service[]; onChange: (v: Service[]) => void; disabled?: boolean }) {
  const toggle = (s: Service) => onChange(value.includes(s) ? value.filter((x) => x !== s) : (["accounting", "audit"] as Service[]).filter((x) => x === s || value.includes(x)));
  return (
    <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ marginBottom: 4, fontSize: 13, fontWeight: 500 }}>Work your firm does for this company</legend>
      {(["accounting", "audit"] as Service[]).map((s) => (
        <label key={s} className="checkbox"><input type="checkbox" checked={value.includes(s)} disabled={disabled || (value.length === 1 && value.includes(s))}
          onChange={() => toggle(s)} /> {SERVICE_TEXT[s]} <span className="muted">— {WORK[s].text}</span></label>
      ))}
    </fieldset>
  );
}

export function CreateCompany({ onCancel, onDone }: { onCancel?: () => void; onDone?: () => void }) {
  const { me, refresh, selectCompany } = useSession();
  const orgs = (me?.organizations ?? []).filter((o) => o.role === "owner" || o.role === "admin");
  const [name, setName] = useState("");
  const [gstin, setGstin] = useState("");
  const [state, setState] = useState("");
  const [services, setServices] = useState<Service[]>(["accounting", "audit"]);
  const [gstFiling, setGstFiling] = useState("monthly");
  const [tds, setTds] = useState(false);
  const [tan, setTan] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prompt = useUnsavedChanges(!busy && !!(name || gstin || state || tan));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!orgs.length) return;
    setBusy(true); setErr(null);
    try {
      const c = await api<Company>("/api/companies", { body: { organization_id: orgs[0].id, name, gstin: gstin || null, state_code: state || null, services, gst_filing: gstFiling, tds_applicable: tds, ...(tan ? { tan } : {}) } });
      await refresh();
      selectCompany(c.id);
      onDone?.();
    } catch (ex) { setErr((ex as Error).message); } finally { setBusy(false); }
  };
  if (!orgs.length) {
    return <div className="card"><h1>No companies yet</h1><p className="muted" style={{ marginTop: 8 }}>Ask your firm's owner or admin to give you access to a company.</p></div>;
  }
  return (
    <form className="card stack" style={{ textAlign: "left" }} onSubmit={submit}>
      {prompt}
      <div><h1>Add a company</h1><p className="muted">Set up the client company whose books you will work on. A starter chart of accounts and the current financial year are created for you; you can change them in Settings.</p></div>
      <ErrorNotice error={err} />
      <label className="field">Company name<input required value={name} onChange={(e) => setName(e.target.value)} /></label>
      <div className="form-grid">
        <label className="field">GSTIN <span className="hint">Optional. Checked for format and check digit.</span>
          <input value={gstin} maxLength={15} onChange={(e) => setGstin(e.target.value.toUpperCase())} /></label>
        <label className="field">State code <span className="hint">Two digits, e.g. 27. Taken from the GSTIN if given.</span>
          <input value={state} maxLength={2} inputMode="numeric" pattern="\d{2}" onChange={(e) => setState(e.target.value)} /></label>
      </div>
      <ServicePicker value={services} onChange={setServices} />
      <div className="form-grid">
        <label className="field">GST return filing <span className="hint">Quarterly (QRMP) clients get quarter-end due dates.</span>
          <select value={gstFiling} onChange={(e) => setGstFiling(e.target.value)}><option value="monthly">Monthly</option><option value="qrmp">Quarterly (QRMP)</option></select></label>
        <label className="field">TDS applies <span className="hint">Turn on if this client deducts TDS.</span>
          <select value={tds ? "yes" : "no"} onChange={(e) => setTds(e.target.value === "yes")}><option value="no">No</option><option value="yes">Yes</option></select></label>
        {tds && <label className="field">TAN <span className="hint">Ten characters, e.g. DELA12345B.</span>
          <input value={tan} maxLength={10} pattern="[A-Za-z]{4}[0-9]{5}[A-Za-z]" onChange={(e) => setTan(e.target.value.toUpperCase())} /></label>}
      </div>
      <div className="row">
        <button className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create company"}</button>
        {onCancel && <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}
