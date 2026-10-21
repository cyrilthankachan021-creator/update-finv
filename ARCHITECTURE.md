# FinV architecture

FinV is a **modular monolith**: one FastAPI process and one database, plus a React single-page app. Nothing else is
needed: no Redis, no queue and no microservices. Document processing runs inside the upload request. That is fine for
the local, single-firm use FinV targets, but background jobs would be needed for very large files (see the
implementation plan).

```
Browser (React SPA) ──/api (same-origin, HttpOnly cookie)──▶ FastAPI app ──▶ SQLite / PostgreSQL
                                                              │
                                                              ├─ ingestion  (parse CSV/XLSX, PDF text, local OCR)
                                                              ├─ engine     (pure, deterministic rules)
                                                              ├─ services   (workflows, persistence, audit log)
                                                              ├─ privacy    (minimise → pseudonymise → outbound gate)
                                                              └─ ai         (optional provider; untrusted output)
                                                                     │
                                                                     ▼ (only when enabled per company and requested)
                                                            Anthropic or OpenAI API
```

## Local versus server

FinV is built to run **on the firm's own machine or server**. The "server" is the local FastAPI process.

- Original documents, extracted data, entries, audit work and the pseudonym mappings never leave it.
- The only outbound call is the optional AI request described in SECURITY_AND_PRIVACY.md.
- If FinV is deployed on a shared server, everything stays on that server.
- The browser receives only the data the signed-in user is authorised to see.

## Responsibilities (the 14 modules in the brief)

| # | Responsibility | Where |
|---|---|---|
| 1 | Document extraction | `ingestion/pipeline.py`, `ingestion/tabular.py`, `ingestion/text_extract.py` |
| 2 | Normalisation | `engine/normalize.py`, `engine/money.py`, `ingestion/tabular.py` (dates, headers) |
| 3 | Accounting classification | `engine/accounting.py` (`propose`) |
| 4 | Deterministic validation | `engine/accounting.py` (`validate`), `engine/gst.py` |
| 5 | Duplicate detection | `engine/duplicates.py` + `services/accounting_service.detect_duplicates` |
| 6 | Document comparison | `engine/duplicates.compare_fields`, `line_item_check`, file SHA-256 in `services/documents_service` |
| 7 | GST/TDS evaluation | `engine/gst.py`; TDS in `engine/accounting.py` (policy-driven only) |
| 8 | Bank reconciliation | `engine/reconciliation.py` + `services/recon_service.py` |
| 9 | Audit anomaly detection | `engine/audit_checks.py` + `services/audit_service.py` |
| 10 | Policy interpretation | `models.Policy`, `api/companies.py` (create/approve), `engine/accounting.py` (applies approved policies) |
| 11 | Human review | `services/accounting_service` (correct / approve / reject / duplicate decisions), audit exception workflow |
| 12 | Posting and exports | `post_entry` (idempotent), CSV reports in `api/accounting.py` and `api/audit.py` |
| 13 | Privacy enforcement | `privacy/transform.py`, `services/ai_service.py`, `CompanySettings.privacy_mode` |
| 14 | Audit logging | `services/audit_log.py` (append-only, SHA-256 hash chain) |

The engine (`app/engine`) imports nothing from the database, web framework, ingestion or AI layers. Every rule can
therefore be unit-tested on plain data, and the rules keep working when AI is unavailable.

## Processing flow (Accounting)

1. **Upload.** The file is checked (extension, magic bytes, size), hashed with SHA-256, encrypted and stored.
   - Identical bytes already uploaded are marked `repeated_upload` and **not** processed again.
   - An `Idempotency-Key` header returns the first result.
2. **Extract.** The extractor detects the kind from the column headers, or uses your choice.
   - Each row becomes a `SourceRecord` with field-level provenance (source, column, row, confidence) and the
     original raw values.
   - Rows that cannot be read are reported with their row number; they are never guessed.
3. **Duplicates.** Each new record is compared with *earlier canonical* records of the same company and type.
   - Results are stored as `DuplicateCandidate` rows: exact, probable or possible duplicates, or possible alterations.
4. **Propose.** `engine.propose` returns lines, an explanation and an optional **question**.
   - It never guesses economic meaning from the debit/credit direction alone.
5. **Validate.** `engine.validate` runs all applicable rules.
   - Results are stored per entry version.
   - `posting_eligible` is true only if no rule returns `error` or `needs_review`.
6. **Review.** Accountants correct lines or source fields; each change re-validates and creates a new version.
   - Reviewers approve. Errors block approval; open review points need explicit acknowledgement and a note.
   - Someone who corrected an entry cannot approve it, unless they are the owner.
7. **Post.** Only approved entries can be posted. The entry is re-validated first. Posting is idempotent and
   immutable.
8. **Reconcile and report.** Reconciliation is recomputed from stored records and confirmed matches every time.

## Data model (tables)

| Area | Tables |
|---|---|
| Identity and tenancy | `users`, `session_tokens`, `organizations` (CA firms), `memberships` (role per organisation), `companies` (clients), `company_settings` |
| Master data | `accounts` (chart of accounts with engine roles), `accounting_periods` (lockable), `policies` (versioned, approval status, effective date) |
| Documents | `documents` (hash, encrypted path, status, notes), `source_records` (extracted invoices, notes and bank lines with provenance) |
| Entries | `journal_entries` (status, version, explanation), `journal_lines`, `validation_results` (rule id/version/status per entry version), `postings` (unique per entry) |
| Review | `duplicate_candidates` (decision, note, decider), `bank_matches` |
| Audit | `tb_imports`, `tb_lines`, `ledger_entries`, `vouching_links`, `audit_exceptions` (status, assignee, fingerprint), `exception_comments` |
| Trail and AI | `audit_events` (hash chain), `ai_request_logs` (the minimised payload actually sent, tokens, status) |

- Amounts use the `Money` type: `NUMERIC(18,2)` on PostgreSQL, and exact decimal strings on SQLite (whose numeric
  affinity would otherwise go through binary floats).
- All financial rows carry `company_id`. The API resolves the company only through the caller's membership, so
  isolation is enforced on the server.
- Migrations live in `backend/alembic` (`alembic upgrade head`).

## API surface

The full list is at `/api/docs`.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/register`, `/login`, `/logout`, `GET /api/auth/me` |
| Companies | list/create, members, settings, privacy status, retention, accounts, periods (+ lock), policies (+ decision) |
| Accounting | overview, documents (upload with progress and idempotency, file, retry, purge), records (filter, detail, correct, repropose, ai-suggest), entries (list, detail, correct, revalidate, approve, reject, post), duplicates (+ decision), bank reconciliation (+ manual match, confirm auto, unmatch), reports summary and CSV exports, activity (+ verify), rules catalogue, internal general ledger |
| Audit | overview, imports (+ run checks), ledger (filter, detail), evidence search, link evidence, review vouching, exceptions (+ update), CSV reports |

All error responses are plain-language messages. Stack traces, SQL and provider responses are never returned.

## Frontend

React 18 + TypeScript + Vite and React Router, with no UI framework. Design tokens and components are in
`src/styles/app.css` and `src/components/ui.tsx`.

- The landing screen (`pages/Home.tsx`) is a choice between Accounting and Audit, never a dashboard.
- Each workspace has its own compact sidebar.
- Settings is a secondary location in the user menu.
- The selected company is remembered in `localStorage`, as a per-browser convenience only.

## Assistant

`app/assistant/agent.py` drives the chat assistant, `app/api/assistant.py` exposes it, and
`frontend/src/components/Assistant.tsx` renders the panel. A message goes through these steps:

1. **Built-in commands.** Regular-expression shortcuts (approve, post or reject entry N; use ledger X for entry N;
   go to a page; what needs review; show duplicates or exceptions; help) run without a model.
2. **Exact answers** (`app/assistant/intents.py`) for greetings, thanks, help, summaries, counts, lists by status,
   bank lines needing a ledger, questions about one entry (by number or by words) and natural-language ledger changes.
   These are instant and built only from FinV data.
3. **Otherwise, the cloud model with tools.** The model gets a small, fixed tool set and a static system prompt, so
   the model sees a stable prompt prefix. Tool calls printed as JSON text are recovered; raw JSON is never shown.
   The cloud chat model (Gemini Flash by default, OpenAI-compatible API, tool calling) runs for at most 4 steps.
   - Read tools: overview, find_entries, get_entry, list_ledgers, list_duplicates, reconciliation, list_exceptions.
   - Write tools (these only prepare an action): set_ledger, approve_entry, reject_entry, post_entry,
     decide_duplicate, update_exception.
   - Navigation: open_page.
4. **Confirmation.** Confirming (`/actions/{id}/confirm`, or typing or saying "yes") calls the same service functions as
   the buttons (`set_ledger` → `correct_entry`, `approve_entry`, `post_entry` and so on), so the same validation,
   versioning and audit trail apply.
5. **Refresh.** After a confirmed change, the panel dispatches `finv:refresh` and every page re-loads its data.

Voice uses `MediaRecorder` in the browser, which posts to `/assistant/transcribe`. There, faster-whisper (CPU, int8)
transcribes the audio, and the text is sent as a chat message.
