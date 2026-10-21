import { ReactNode, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "../session";
import { Assistant } from "./Assistant";
import { Icon } from "./ui";

/** Which module a page belongs to, so its header carries that module's accent colour. */
export function moduleOf(path: string): string {
  if (/^\/audit/.test(path)) return "audit";
  if (/^\/(accounting\/gst|gst)/.test(path)) return "gst";
  if (/^\/accounting\/bank/.test(path)) return "bank";
  if (/^\/(accounting\/(transactions|entries|documents|records|duplicates)|imports)/.test(path)) return "docs";
  return "";
}

export function TopBar({ workspace }: { workspace?: string }) {
  const { me, companies, company, selectCompany, logout } = useSession();
  const nav = useNavigate();
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  return (
    <header className="topbar">
      <Link to="/" className="wordmark" aria-label="FinV home"><span className="dot" aria-hidden="true" />FinV</Link>
      {workspace && <span className="workspace-tag">{workspace}</span>}
      <div className="grow" />
      {me && companies.length > 0 && (
        <>
          <label className="sr-only" htmlFor="company-switch">Client</label>
          <select id="company-switch" value={company?.id ?? ""} style={{ maxWidth: 240 }}
            onChange={(e) => { const id = e.target.value ? Number(e.target.value) : null; if (!id) selectCompany(null); nav(id ? `/clients/${id}` : "/", { replace: /^\/clients\/\d+$/.test(loc.pathname) }); }}>
            <option value="">Select client…</option>
            {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </>
      )}
      {me && (
        <div style={{ position: "relative" }}>
          <button className="btn ghost" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {me.name.split(" ")[0]}
          </button>
          {open && (
            <div role="menu" className="card tight" style={{ position: "absolute", right: 0, top: 42, minWidth: 180, zIndex: 20, padding: 6 }}
              onMouseLeave={() => setOpen(false)}>
              <div className="faint" style={{ padding: "4px 10px", fontSize: 12 }}>{me.email}</div>
              {company && <button role="menuitem" className="btn ghost" style={{ width: "100%", justifyContent: "flex-start" }}
                onClick={() => { setOpen(false); nav("/settings"); }}><Icon name="settings" />Settings</button>}
              <button role="menuitem" className="btn ghost" style={{ width: "100%", justifyContent: "flex-start" }}
                onClick={async () => { setOpen(false); await logout(); nav("/login", { replace: true }); }}>Sign out</button>
            </div>
          )}
        </div>
      )}
    </header>
  );
}

export interface NavItem { to: string; label: string; icon: string; end?: boolean }

/** Indian financial year (April to March) as "2026-27". */
export const financialYear = (d: Date = new Date()): string => {
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${y}-${String(y + 1).slice(2)}`;
};

/** Always says whose records are on screen, and the way back to the firm view. */
function ClientBanner() {
  const { company } = useSession();
  if (!company) return null;
  return (
    <div className="notice info" role="region" aria-label="Selected client" style={{ margin: "0 0 16px", alignItems: "center" }}>
      <div className="grow">
        <strong>{company.name}</strong>
        <span className="muted"> · Client ID {company.id}{company.gstin ? ` · GSTIN ${company.gstin}` : ""} · FY {financialYear()}</span>
      </div>
      <Link className="btn small" to={`/clients/${company.id}`}>Client workspace</Link>
      <Link className="btn small" to="/">Firm dashboard</Link>
    </div>
  );
}

export function WorkspaceLayout({ name, items, footer }: { name: string; items: NavItem[]; footer?: ReactNode }) {
  const loc = useLocation();
  return (
    <>
      <TopBar workspace={name} />
      <div className="shell">
        <nav className="sidebar" aria-label={`${name} navigation`}>
          {items.map((i) => (
            <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={i.icon} />{i.label}
            </NavLink>
          ))}
          <div className="spacer" />
          {footer}
        </nav>
        <main className="main" id="main" data-module={moduleOf(loc.pathname)}>{name !== "Firm" && <ClientBanner />}<Outlet /></main>
      </div>
      {name !== "Firm" && <Assistant workspace={name === "Audit" ? "audit" : "accounting"} />}
    </>
  );
}
