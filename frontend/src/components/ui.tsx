import { ReactNode, useEffect, useRef } from "react";
import { label } from "../api";
import { arrowNav } from "../lib/navigation";
import type { Check } from "../types";

// Small, restrained stroke icons (1.6px), 18px by default.
const paths: Record<string, ReactNode> = {
  ledger: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 7h8M8 11h8M8 15h5" /></>,
  shield: <><path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z" /><path d="M9 12l2 2 4-4" /></>,
  home: <><path d="M4 11l8-7 8 7" /><path d="M6 10v10h12V10" /></>,
  list: <><path d="M8 6h12M8 12h12M8 18h12" /><circle cx="4" cy="6" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="18" r="1" /></>,
  book: <><path d="M5 4h10a4 4 0 014 4v12H9a4 4 0 01-4-4V4z" /><path d="M9 8h6M9 12h6" /></>,
  bank: <><path d="M3 9l9-5 9 5" /><path d="M5 10v8M10 10v8M14 10v8M19 10v8M3 20h18" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2" /></>,
  chart: <><path d="M4 20V4M4 20h16" /><path d="M8 16v-4M12 16V8M16 16v-6" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></>,
  upload: <><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" /></>,
  scale: <><path d="M12 3v18M5 7h14" /><path d="M5 7l-3 7a3 3 0 006 0L5 7zM19 7l-3 7a3 3 0 006 0l-3-7z" /></>,
  search: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
  flag: <><path d="M5 21V4M5 4h11l-2 4 2 4H5" /></>,
  check: <path d="M5 12l4 4 10-10" />,
  alert: <><path d="M12 3l9 16H3l9-16z" /><path d="M12 10v4M12 17h.01" /></>,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  chat: <><path d="M4 5h16v11H9l-5 4V5z" /><path d="M8 9h8M8 12h5" /></>,
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0014 0M12 18v3" /></>,
  stop: <rect x="7" y="7" width="10" height="10" rx="1.5" />,
};

export function Icon({ name, size = 18 }: { name: keyof typeof paths | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>
  );
}

const STATUS_TONE: Record<string, string> = {
  validated: "ok", approved: "ok", posted: "ok", processed: "ok", pass: "ok", resolved: "ok", consistent: "ok", agreed: "ok", needs_verification: "warn",
  confirmed_duplicate: "warn", not_duplicate: "ok", accepted_with_explanation: "ok",
  needs_review: "warn", warning: "warn", open: "warn", under_review: "warn", needs_information: "warn", repeated_upload: "warn",
  queried: "warn", proposed: "info", processing: "info", extracted: "info",
  error: "err", failed: "err", rejected: "err", duplicate: "err", discrepancy: "err", high: "err", medium: "warn", low: "",
};
const STATUS_TEXT: Record<string, string> = {
  needs_review: "Needs review", validated: "Validated", pass: "Passed", error: "Error", warning: "Warning",
  not_applicable: "Not applicable", confirmed_duplicate: "Confirmed duplicate", not_duplicate: "Not a duplicate",
  repeated_upload: "Repeated upload", accepted_with_explanation: "Accepted with explanation", needs_information: "Needs information",
  possible_alteration: "Possible alteration",
};

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  return <span className={`badge ${STATUS_TONE[status] ?? ""}`}>{children ?? STATUS_TEXT[status] ?? label(status)}</span>;
}

/** Severity as a labelled chip: the word and a shape carry the meaning, colour only reinforces it. */
export function SeverityChip({ severity }: { severity: string }) {
  const tone = severity === "high" || severity === "critical" ? "high" : severity === "medium" ? "medium" : "low";
  const mark = tone === "high" ? "▲" : tone === "medium" ? "◆" : "●";
  return <span className={`chip sev-${tone}`}><span aria-hidden="true">{mark} </span>{label(severity)}</span>;
}

/** A small "?" that explains a term in plain words; works on hover, keyboard focus and touch. */
export function Help({ text }: { text: string }) {
  return <span className="help"><button type="button" className="help-btn" aria-label={`What does this mean? ${text}`}>?</button><span role="tooltip" className="help-tip">{text}</span></span>;
}

/** A tab strip: one tab is in the Tab order, the arrow keys move between tabs and stop at the first and last (they do not wrap). */
export function TabList({ items, value, onChange, label: aria, className = "tabs" }:
  { items: readonly (readonly [string, string])[]; value: string; onChange: (v: string) => void; label?: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const at = Math.max(0, items.findIndex(([v]) => v === value));
  const go = (i: number) => { onChange(items[i][0]); ref.current?.querySelectorAll<HTMLElement>('[role="tab"]')[i]?.focus(); };
  return (
    <div className={className} role="tablist" aria-label={aria} ref={ref} onKeyDown={arrowNav(at, items.length, go)}>
      {items.map(([v, l], i) => (
        <button key={v} type="button" role="tab" aria-selected={value === v} tabIndex={i === at ? 0 : -1}
          className={className === "tabs" ? undefined : `btn small ${value === v ? "primary" : ""}`} onClick={() => onChange(v)}>{l}</button>))}
    </div>
  );
}

export function Spinner({ text = "Loading…" }: { text?: string }) {
  return <span className="row muted" role="status"><span className="spinner" aria-hidden="true" />{text}</span>;
}

export function ErrorNotice({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="notice err" role="alert">
      <Icon name="alert" />
      <div className="grow">{error}</div>
      {onRetry && <button className="btn small" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children, icon = "list" }: { title: string; children?: ReactNode; icon?: string }) {
  return (
    <div className="empty">
      <div className="faint" style={{ marginBottom: 8 }}><Icon name={icon} size={24} /></div>
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function PageHead({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{subtitle && <p className="muted">{subtitle}</p>}</div>
      {children && <div className="row">{children}</div>}
    </div>
  );
}

const CHECK_ORDER: Record<string, number> = { error: 0, needs_review: 1, warning: 2, pass: 3, not_applicable: 4 };

export function CheckList({ checks, showPassed = false }: { checks: Check[]; showPassed?: boolean }) {
  const sorted = [...checks].sort((a, b) => (CHECK_ORDER[a.status] ?? 9) - (CHECK_ORDER[b.status] ?? 9));
  const shown = showPassed ? sorted : sorted.filter((c) => c.status !== "pass" && c.status !== "not_applicable");
  const passed = sorted.filter((c) => c.status === "pass").length;
  return (
    <div className="stack-sm">
      {shown.length === 0 && <div className="notice ok"><Icon name="check" /><div>All {passed} applicable checks passed.</div></div>}
      <ul className="checks">
        {shown.map((c, i) => (
          <li key={c.rule_id + i} className={c.status}>
            <Badge status={c.status} />
            <div className="grow">{c.message}</div>
            <span className="rule" title={`Rule ${c.rule_id} version ${c.rule_version}`}>{c.rule_id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="row-between" style={{ marginBottom: 16 }}>
          <h2>{title}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <div className="pagination">{total} item{total === 1 ? "" : "s"}</div>;
  return (
    <div className="pagination">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}</span>
      <div className="row">
        <button className="btn small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <button className="btn small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}
