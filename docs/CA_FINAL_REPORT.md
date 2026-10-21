# FinV CA-side: final report (brief section 15)

Date: 21 October 2026. Scope: the CA-facing side only. The company-facing site was not redesigned.

## 1. What changed

- **Firm dashboard** is the first screen after login: compliance counts, a searchable and filterable task table, a firm-wide error centre, and client creation, all computed from real data at request time.
- **Clients** page: paginated, searchable, filterable list with status, financial year and last import. Each client has its own workspace at `/clients/:id` with module cards, a Next steps checklist, deadlines and findings.
- **Sidebar** is Firm Dashboard, Clients, Compliance and Deadlines, Error Center, Integrations and Imports, Settings. A banner names the selected client on every client page.
- **Imports**: bank statements (CSV, XLSX) with a validation report; trial balances and ledgers (CSV, XLSX); Tally voucher XML; Tally pull from a running TallyPrime.
- **Audit**: vouching, ledger (totals, sort, date range, opening and closing balances), trial balance (imported, and built from FinV's own posted entries), exceptions, report exports, versioned report with sign-off.
- **Per-client setup**: GST filing (monthly or QRMP), TDS applicable, TAN, services, at creation and in Settings. Duplicate clients are refused.
- **Per-client access**: an admin can restrict a client to named staff; owners and admins always see everything.
- **FinV AI assistant** on the client page, scoped to that client, with the green disclaimer, step reporting and audit logging.
- **Navigation guard**: Back never leaves FinV, filters and tabs replace history, "Leave without saving?" on unsaved work, arrow-key tab strips stop at both ends, scroll position restored.
- **Redesign**: coloured sidebar, module accent colours, severity chips, "?" tips, attention-row borders. All text and background pairs used for the new colours are at least 5.3:1.

## 2. Root cause of the client details screen before the dashboard

The Home page rendered the "Add a company" form whenever no company was selected. A signed-in CA with no selected client was therefore always sent to that form first. The fix changed the routing: `/` is now the firm dashboard, which works with no client selected, and the add-client form is a dialog opened from the dashboard and the Clients page. The data model and onboarding fields were not touched. It was not hidden with CSS.

## 3. Features and their actual status

| Area | Status |
| --- | --- |
| Firm dashboard, task table, filters (including missing documents) | Working, tested |
| Firm-wide error centre | Working. Audit findings show expected and actual values where the rule records them, the period, the source import, and a period filter. Bank-balance, GSTR-2B and TDS-reconciliation issue types are not there |
| Client list, client workspace, Next steps | Working, tested |
| Add client with GST/TDS setup, duplicate refusal | Working, tested |
| GST due dates: monthly and QRMP | Working. QRMP 22nd/24th state grouping written from memory; verify against the current notification |
| TDS | Setup, TAN and deposit tasks from posted FinV entries only. Challans and returns (24Q/26Q) are not tracked |
| Bank statement import and validation report | Working, tested (CSV, XLSX). PDF statements are not supported |
| Bank reconciliation | Existing module, preserved |
| Tally file import (voucher XML, CSV, XLSX) | Working, tested; repeated and overlapping imports do not duplicate lines |
| Tally pull from a running Tally | Built and tested against a fake Tally server only. Not run against a real TallyPrime |
| Tally export from FinV (file and push) | Existing, preserved |
| Vouching | Working. An entry is "consistent" only when at least two fields agree; otherwise "needs verification" |
| Ledger, trial balance (imported and from entries) | Working, tested. States plainly when opening or closing balances are not available |
| Audit report versions and sign-off | Working, tested. A qualified human signs; FinV issues no opinion |
| Assistant | Working. Prepares GST returns but never approves or files. Tally requests open the right page |
| Per-client access restriction | Working, tested |
| Read-access logging | Client open, document view and report export are logged in the hash-chained log |
| Dashboard cache | 20 seconds, only for firms with 200 or more clients |

## 4. Existing features preserved

Authentication, accounting entries and approvals, GST return preparation and the authorised-person approval flow, documents and duplicates, bank matching, policies, accounting periods, audit exceptions, the hash-chained audit trail, and the company-facing pages. Existing tests were kept; two were changed because the rules changed: "file gstr-1" is now refused, and a client without a GSTIN has no GST tasks (it now also has an "Upload accounting data" task).

## 5. Tests executed

- Backend: 246 passed (pytest).
- Frontend: 58 passed (vitest), TypeScript clean, production build succeeds.
- Browser (Playwright, Chromium): Back five times from GST stays inside FinV and stops at the dashboard; Forward stops at the last page; the unsaved-form prompt appears, Stay keeps the input and Leave goes back; four filter-tab clicks add no history entries; scroll position is restored on Back; client page, GST page, entries table and Error Center checked at laptop and tablet width with no horizontal scroll; the assistant demo ("open bank reconciliation", "prepare GSTR-1 for September", and the refusal of "file my GST return").

A real bug was found and fixed during this testing: the "Leave without saving?" buttons sat inside the form and submitted it. A second one: the client switcher selected the new client before the unsaved-work prompt could block the move.

## 6. Integrations that work and prerequisites

- **Tally file import**: works with Day Book or voucher XML (UTF-8 or UTF-16), CSV and XLSX. Files that declare XML entities are refused.
- **Tally pull**: needs TallyPrime open with the company loaded and its HTTP server on (F1 Help, Settings, Connectivity, port 9000). FinV only contacts this machine or the local network. Failures are reported as failures and nothing is imported.
- **Tally push** (FinV to Tally): existing, same prerequisite.
- **AI understanding**: needs a Gemini key in `backend/.env`; without it the assistant understands built-in commands only.

## 7. Remaining incomplete items and blockers

- Tally pull has not been run against a real Tally.
- Error centre: expected-versus-actual is shown only for audit findings whose rule records those values; no GSTR-2B mismatch, bank-versus-book balance or TDS reconciliation issue types.
- Compliance obligations beyond GST and TDS deposits are not tracked.
- Bank statements in PDF are not supported.
- There is no multi-step flow in the app, so the stepper component is only unit-tested and the "past both ends" browser test could not be run.
- The GST approval and Back behaviour is covered by unit tests only, not in a real browser.
- The assistant lists tasks but does not resolve them.
- No dark mode (the app has none).
- The Apple design skill was not installed (the clone was blocked).
- Company-facing redesign is deliberately not started.

## 8. Database migrations and configuration

Migrations, applied in this order (the live database is at `f7d4a0c3e8b5`):

1. `c3a1d7e9b2f4`: `documents.validation` (bank statement validation report)
2. `d5b2e8a1c6f3`: `company_settings.gst_filing`, `tds_applicable`, `tan`
3. `e6c3f9b2d7a4`: `audit_report_versions` table
4. `f7d4a0c3e8b5`: `company_assignments` table

The service runs `alembic upgrade head` on start. Backups taken before the live migrations: `backend/data/finv.db.bak-before-bank-validation` and `backend/data/finv.db.bak-before-firm-features`. No new environment variables. The Tally address defaults to the existing `tally_url` setting.
