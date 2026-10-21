import { dateFmt } from "../api";
import type { BankValidation } from "../types";
import { Badge } from "./ui";

const STATUS: Record<BankValidation["status"], [string, string]> = {
  passed: ["validated", "Statement checks passed"], needs_attention: ["warning", "Needs attention"], failed: ["failed", "No usable lines"],
};

/** What FinV found when it read a bank statement: period, rows accepted and rejected, and the balance checks. */
export function BankCheck({ v }: { v: BankValidation }) {
  const [tone, text] = STATUS[v.status];
  return (
    <section className="panel stack-sm" aria-label="Bank statement check">
      <div className="row-between"><strong>Bank statement check</strong><Badge status={tone}>{text}</Badge></div>
      <p className="muted">
        {v.period_start ? `Covers ${dateFmt(v.period_start)} to ${dateFmt(v.period_end!)}. ` : ""}
        {v.lines_accepted} line{v.lines_accepted === 1 ? "" : "s"} accepted, {v.lines_rejected} rejected. Debits {v.total_debits}, credits {v.total_credits}.
      </p>
      <ul className="plain">{v.checks.map((c) => <li key={c.check}>{c.ok === true ? "✓" : c.ok === false ? "✗" : "–"} <strong>{c.check}.</strong> {c.detail}</li>)}</ul>
      {v.rejected.length > 0 && (
        <details open><summary>{v.lines_rejected} rejected row{v.lines_rejected === 1 ? "" : "s"}{v.rejected.length < v.lines_rejected ? ` (first ${v.rejected.length} shown)` : ""}</summary>
          <ul className="plain">{v.rejected.map((r) => <li key={r.row}>Row {r.row}: {r.message}</li>)}</ul></details>
      )}
    </section>
  );
}
