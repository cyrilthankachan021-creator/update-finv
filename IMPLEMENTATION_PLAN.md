# Implementation plan and status

Phase 1 (inspection) found the `finv` directory **empty**: there was no existing code, design reference or data. The
stack was chosen for a local-first, privacy-preserving tool:

- FastAPI modular monolith, SQLAlchemy and Alembic, SQLite by default (PostgreSQL-capable).
- React + TypeScript + Vite.
- Local OCR (tesseract). Optional AI behind a provider interface.

No Redis, queues or containers were introduced.

Status labels:

- **Tested**: implemented and tested; automated tests cover it, and the user-facing flows were also checked in a real
  browser.
- **Partly tested**: implemented, but not every path is tested.
- **Partial**: some of the feature exists, with limits noted.
- **Not implemented**: no code exists yet.

## Checklist

| Area | Item | Status |
|---|---|---|
| **Frontend** | Two-choice landing screen (Accounting / Audit), keyboard-accessible, no dashboard | Tested |
| | Company onboarding and selection before the workspaces | Tested |
| | Separate Accounting and Audit workspaces, each with its own compact sidebar | Tested |
| | Design tokens, reusable components, empty, loading, error and retry states | Tested |
| | Responsive layout (desktop first, checked at 390 px) | Partly tested (browser screenshots; not unit-tested) |
| | Accessibility: labels, focus styles, keyboard navigation, dialogs (Escape, focus return) | Partly tested (no full audit with a screen reader or axe) |
| **Data foundation** | Models and migrations for all required entities; Decimal-safe money type | Tested |
| | Organisations, client companies, memberships, six roles; server-side isolation | Tested |
| | Authentication: scrypt, HttpOnly session cookie, CSRF header, sign-in throttling | Tested |
| | Overview metrics from stored records (no fabricated figures) | Tested |
| **Accounting correctness** | Deterministic engine, independent of database, HTTP and AI | Tested |
| | Proposals for purchase, sales, credit and debit notes and bank lines, with explanations and questions | Tested |
| | 22 versioned accounting, GST, note and TDS rules; results stored per entry version | Tested |
| | Posting eligibility separate from "balanced"; evidence-agreement rule (ACC-007) | Tested |
| **Documents and duplicates** | CSV/XLSX ingestion with header detection, provenance and row errors | Tested |
| | PDF (embedded text) and JPG/PNG/scanned-PDF OCR, pattern-based | Tested on generated documents; accuracy on real scans not measured |
| | Duplicate detection (exact, probable, possible), possible-alteration detection, repeated-upload hash | Tested |
| | Credit/debit-note linking, party check, cumulative limit, tax-head check | Tested |
| | Line-item total check | Partial: runs only when line items are extracted, which the current extractors do not do |
| **Review and reconciliation** | Correct entries or source fields, re-validate, approve (with acknowledgement), reject, re-propose | Tested |
| | Segregation: a corrector cannot approve (owner exception) | Tested |
| | Idempotent posting to FinV's internal ledger; posted entries immutable; CSV exports | Tested |
| | Bank reconciliation: auto, ambiguous, manual match, unmatch, near misses, transfers | Engine and API tested; manual match/unmatch in the UI only partly tested |
| | Hash-chained audit trail with verification | Tested |
| **Audit** | Trial balance and ledger import; TB, ledger and vouching checks (15 rules) | Tested |
| | Vouching workflow (link evidence, comparison, auditor review) | Tested |
| | Exception workflow (5 statuses, comments, history, assignee field) | Tested; assignee is API-only (no UI picker) |
| | Audit reports (exceptions, vouching, evidence gaps, review history) | Tested |
| **Privacy and AI** | Local-only default; per-company opt-in; minimisation, pseudonymisation, outbound gate | Tested |
| | Provider interface: Anthropic (official SDK, structured JSON output) and OpenAI (REST, JSON schema) | Partly tested (fake provider only; no live calls, since no credentials) |
| | Local validation of AI output (schema, identifiers, ledger allow-list); results always need review | Tested |
| | Company policies: propose, approve, version, supersede, disable; effective date; corrections only *suggest* | Tested |
| | Gemini Flash provider (replaced local Ollama), ledger codes constrained by schema | Tested (fake provider) and checked live with gemini-3.5-flash |
| | Test AI connection (fictional data, latency, answer check) | Tested, and run live |
| **Assistant** | Chat panel on all workspace and Settings pages (not on sign-in or landing) | Tested, and run live in a browser |
| | Agent with read, write and navigation tools on Gemini Flash (cloud, "Cloud AI" consent); changes only after Confirm; honesty guard | Tested (scripted model); tool loop checked live with gemini-3.5-flash |
| | Built-in commands that work without AI; "yes"/"no" confirmations | Tested |
| | Local voice input (Whisper base, CPU) | Run live end to end with a synthesized voice through a fake microphone; real human speech and accents not measured |
| **Tally** | Ledger masters + voucher XML export (party ledgers, Tally sign convention, escaping) | Tested (XML structure); not imported into real Tally |
| | Direct push to Tally's HTTP server; once-only export; local/LAN-only addresses | Tested against a simulated Tally server; real TallyPrime not available here |
| | AI-assisted policy drafting from minimised context | **Not implemented**: policies are drafted by people or suggested from corrections |
| **Security** | Encrypted file storage, retention and purge, sanitised errors, CSV formula escaping, security headers | Tested |
| | HTTPS, database encryption, key rotation | Not implemented (see SECURITY_AND_PRIVACY.md) |

## Not implemented

- **Tax data:** GSTN live verification; GSTR-1, 2A/2B and 3B reconciliation; 26AS/AIS reconciliation; statutory TDS
  rate tables.
- **Integrations:** importing *from* Tally (FinV only sends to Tally); other ERPs; e-invoice IRN or QR validation.
- **Scale:** background job queue for very large files (processing is synchronous).
- **Accounts:** multi-currency, advances and partial-payment tracking, reversing-entry helper.
- **Administration:**
  - Company and user deletion; password reset; invitations for people without an account (members must register
    first); assignee picker for exceptions.
  - Audit sampling methodology and materiality computation (materiality is a configurable amount).

## Next priorities

1. **Real-document extraction quality.** Collect anonymised real invoices and bank exports from pilot firms and measure
   field accuracy. Add bank-specific column profiles and line-item extraction so the line-item check runs.
2. **GST reconciliation.** Match purchase records with GSTR-2B (imported JSON or Excel) to support input tax credit
   claims, extending GST-006.
3. **Verified rule content.** Have a Chartered Accountant review the rules in ACCOUNTING_RULES.md (place-of-supply
   special cases, rate list). Add versioned, cited data for TDS sections and thresholds.
4. **Deployment hardening.** HTTPS reverse proxy, PostgreSQL testing, encrypted database or disk, key rotation, backups,
   React Router 7 and tooling upgrades (see the dependency audit), and an accessibility audit.
5. **Reversals and adjustments.** A reversing-entry workflow for posted entries later confirmed as duplicates, and
   tracking of advances and part payments.
6. **AI scope.** If firms want it, add AI-assisted policy drafting through the same minimise → gate → validate path,
   with an explicit consent screen showing the exact payload before sending.
