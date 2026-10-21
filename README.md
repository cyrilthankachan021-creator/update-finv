# FinV

FinV is an accounting-correctness and audit-intelligence tool for Chartered Accountant firms and finance teams in India.
Its rule is **correctness before posting**. FinV extracts financial documents and proposes journal entries, checks them
with deterministic rules, flags duplicates and inconsistent documents, and holds uncertain entries until a person has
reviewed them. AI can help (it is off by default), but it never decides whether an entry is correct.

FinV is a working local prototype. It is **not production-ready** and it is **not a statutory audit**. See
"Known limitations" below and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## What it does

- **First screen:** lists the firm's client companies (switch between them or **Add company**), then offers
  **Accounting** and/or **Audit** for the selected one. Each company records which work the firm does for it
  (Accounting, Audit or both), chosen when it is added and changeable by an owner/admin in Settings → Services.
  A workspace that is switched off is hidden for that company; no data is deleted.
- **Accounting:**
  - Upload bank statements and invoice or note registers (CSV/XLSX), and individual invoices and notes (PDF, JPG, PNG).
  - FinV proposes entries with an explanation and validates them against 22 accounting, GST, credit/debit-note and TDS rules.
  - It detects duplicates and altered copies of documents.
  - You correct, approve, reject or post entries, reconcile the bank and export CSV reports.
  - A hash-chained audit trail records every action.
- **Audit:**
  - Import a trial balance or ledger and run 15 trial-balance, ledger and vouching checks.
  - Vouch ledger entries against supporting documents.
  - Manage exceptions with statuses and history, and export audit reports.
- **Assistant (chat + voice):** an "Ask FinV" panel on every Accounting, Audit and Settings page. Type or speak.
  - It answers questions and navigates.
  - It prepares changes (set a ledger, approve, reject, post, duplicate and exception decisions) that run only after
    you press **Confirm** or say "yes".
  - Free-form messages ("could you sign off entry twelve", "what is GST?") work with no setting to change: only
    the typed text, with names, GSTIN/PAN, account numbers and amounts removed, goes to Gemini, which returns one
    action that FinV checks and runs locally (`backend/app/assistant/intent_llm.py`). If Gemini is down, the
    built-in commands still work, and they accept typos and number words ("aprove entry twelve").
  - The full chat model, which sees client data, runs only where an admin chose "Cloud AI". It uses Google Gemini
    Flash (free tier) by default, or Groq or xAI Grok. Speech is transcribed by Whisper on
    this computer. The model needs an API key in `backend/.env` and the per-company "Cloud AI" choice, because
    client data in the chat is sent to that service.
  - Replies stream word by word, as in ChatGPT.
  - Common commands work instantly, even with AI switched off.
- **GST returns (GSTR-1, GSTR-3B), approval-gated:** Accounting → GST Returns. See "GST returns" below.
- **Tally:** posted entries export as TallyPrime XML (ledger masters + vouchers), or are sent directly to Tally's
  HTTP server on this machine or your local network. Each entry is sent once.
- **AI suggestions:** Gemini Flash suggests ledgers for bank lines, constrained to your chart of accounts, with a
  **Test AI connection** button (Settings → Privacy & AI) that uses fictional data only.
- **Privacy:**
  - Everything runs on your machine, and uploaded files are encrypted at rest.
  - AI assistance is off unless an admin enables it for a company. Even then, only a minimised, pseudonymised
    description of one bank line is sent, and a local check blocks any payload that still contains original identifiers.

## Quick start (local)

Requirements: Python 3.12, Node 20 or later. Optional: `tesseract` (OCR for images and scanned PDFs) and `pdftoppm`.

```bash
# backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export FINV_DATA_DIR=$PWD/data            # database, encrypted files and storage key live here
.venv/bin/alembic upgrade head            # create or upgrade the database
.venv/bin/uvicorn app.asgi:app --host 127.0.0.1 --port 8765

# frontend (second terminal)
cd frontend
npm install
npm run dev                                # http://127.0.0.1:5180, proxies /api to 127.0.0.1:8765
```

Open http://127.0.0.1:5180, create an account, then add a company.

The sample files in `samples/` are fictional and dated June 2025. To use them, add an accounting period from
2025-04-01 to 2026-03-31 in **Settings → Company & periods** (new companies get the current financial year), then
follow `samples/README.txt`.

To use a different API address: `FINV_API_URL=http://127.0.0.1:9000 npm run dev`.

Optional voice input: `.venv/bin/pip install -r requirements-voice.txt`. The Whisper "base" model (about 142 MB)
downloads once on first use.

To enable AI, get a free key at https://aistudio.google.com/apikey and put it in `backend/.env` with
`GEMINI_API_KEY=...`, `FINV_AI_PROVIDER=gemini` and `FINV_CHAT_PROVIDER=gemini`. Restart the API, choose
"Pseudonymised external AI" (bank-line suggestions only) or "Cloud AI" (also the chat assistant) in Settings →
Privacy & AI, and click **Test AI connection**. On Gemini's free tier, Google may use what is sent to improve its
products; use the paid tier for real client data.

## Running permanently

On this laptop, FinV runs as two user services that start automatically at login: `finv-api` (port 8765) and
`finv-web` (port 5180).

```bash
systemctl --user status finv-api finv-web     # check
systemctl --user restart finv-api             # after changing backend/.env
journalctl --user -u finv-api -f              # logs
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q        # 111 tests: engine, ingestion, PDF/OCR, privacy, API, security, audit, Tally, AI, assistant
cd frontend && npm test && npm run typecheck       # 18 UI tests + TypeScript
python scripts/e2e_browser.py                      # browser walkthrough against a running app (needs Playwright)
```

See [TESTING.md](TESTING.md) for what each suite covers.

## Project structure

```
backend/
  app/engine/        deterministic core, with no database, HTTP or AI imports:
                     accounting.py (proposals + ACC/NOTE/TDS rules), gst.py, duplicates.py,
                     reconciliation.py, audit_checks.py, money.py (Decimal), normalize.py, rules.py
  app/ingestion/     CSV/XLSX parsing and header mapping, PDF text and OCR extraction, file checks
  app/privacy/       minimisation, pseudonymisation, outbound gate
  app/ai/            provider interface (none | anthropic | openai), rate limits, error handling
  app/services/      workflows: documents, accounting review, reconciliation, audit, AI assist, storage, audit log
  app/api/           FastAPI routers (auth, companies/settings/policies, accounting, audit)
  app/models.py      SQLAlchemy models; alembic/ holds the migrations
  tests/             pytest suites (fictional data only)
frontend/src/
  pages/Home.tsx     the two-choice landing screen and company onboarding
  pages/accounting/  Overview, Transactions, record detail + EntryPanel, Entries, Bank, Duplicates, Reports
  pages/audit/       Overview, Trial Balance, Ledger Review, Vouching, Exceptions, Audit Reports
  pages/Settings.tsx periods, chart of accounts, policies, privacy & AI, retention, team
  components/        design-system pieces (ui.tsx, UploadArea, Shell); styles/app.css holds the tokens
samples/             fictional demo files (regenerate with scripts/make_samples.py)
scripts/             sample generator, rule catalogue, browser walkthrough
```

## GST returns

FinV prepares GSTR-1 and GSTR-3B from **approved and posted** entries, gets them approved, puts the data on the GST
portal and stops. **You file the return yourself**: payment, OTP, EVC and DSC never pass through FinV.

1. **Prepare.** Choose the return and month. FinV saves a numbered version (snapshot): totals, sections, warnings,
   bank-reconciliation status and a fingerprint of the source data. Warnings marked **Must resolve** (unapproved or
   undated documents, unknown rate, invalid customer GSTIN, missing HSN summary, ITC not matched to GSTR-2B) must be
   fixed, or an owner/admin approves with a written override reason.
2. **Send for approval.** Telegram gets a short message: client number, last 4 GSTIN characters, period, tax rounded
   to the nearest ₹1,000, warning counts and a review link. The link works once, expires after 30 minutes, needs a
   FinV sign-in and **cannot approve anything** by itself. No names, invoices or full GSTINs are sent.
3. **Approve** in FinV (reviewer, admin or owner), re-entering your FinV password. The approval is tied to that
   exact version. If any record or entry for the month changes afterwards, the approval is cancelled and the return
   must be prepared and approved again.
4. **Portal preparation.** Click **Start portal preparation**. A separate browser window opens on this computer at
   the GST login page; sign in there yourself. (If it was closed, use **Open the GST portal window**. Where FinV has
   no screen, e.g. on a server, it shows `backend/.venv/bin/python scripts/gst_portal.py <job id>` to run instead;
   `FINV_GST_OPEN_BROWSER=0` forces that.)
   FinV checks the GSTIN and period, uploads the GSTR-1 JSON (or fills GSTR-3B tables 3.1(a), 3.1(c) and 4(A)(5)),
   reads the portal's totals back and compares them with the approved figures. It refuses to press any Submit,
   File, EVC, DSC or payment button. If the session expires or anything differs, it stops; run the command again to
   resume. Steps already done, including an upload, are not repeated.
5. **File** on the portal yourself, then click **I have filed it on the portal**.
6. **Verify.** FinV reads the ARN and status from the portal's Track Return Status page. Only then does it show
   **Filed and verified**.

Every step is in the audit trail. To set up Telegram: create a bot with @BotFather, send it a message, then add to
`backend/.env`:

```
TELEGRAM_BOT_TOKEN=<token from BotFather>
FINV_TELEGRAM_CHAT_ID=<your chat id, from https://api.telegram.org/bot<token>/getUpdates>
FINV_PUBLIC_URL=http://<address your phone can reach FinV on>   # default http://localhost:5180 works only on this PC
```

Without Telegram, approval works the same way inside FinV. WhatsApp is not supported yet.

**Limits.** The portal steps follow the portal's published layout but have not been run against a live portal
session yet; expect to adjust selectors in `backend/app/portal/gst_portal.py` on the first real run (a mismatch
stops safely, nothing is filed). Not prepared: HSN summary (Table 12), exports, SEZ, Tables 8, 11 and 13, reverse
charge, ineligible ITC, interest and late fee. Each invoice must carry a single GST rate.

## Configuration

Copy `.env.example` and adjust. The important settings:

- `FINV_DATA_DIR`: where data lives.
- `DATABASE_URL`: SQLite by default; PostgreSQL is supported by SQLAlchemy, but FinV has not been tested on it.
- `FINV_STORAGE_KEY`: document encryption key.
- `FINV_AI_PROVIDER`, `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `FINV_AI_MODEL`: optional AI assistance.
- `TELEGRAM_BOT_TOKEN`, `FINV_TELEGRAM_CHAT_ID`, `FINV_PUBLIC_URL`, `FINV_REVIEW_LINK_MINUTES`: GST approval messages.

There are no default or bundled credentials.

## API

Interactive OpenAPI docs are at http://127.0.0.1:8765/api/docs while the API runs. All endpoints are under `/api`:

- Every state-changing request needs the header `X-Requested-With: finv` (anti-CSRF) and a session cookie from
  `/api/auth/login`.
- Company data lives under `/api/companies/{id}/…` and is authorised per request against the caller's organisation
  membership and role.
- Uploads accept an `Idempotency-Key` header.
- Posting is idempotent.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): modules, data flow, data model and API
- [ACCOUNTING_RULES.md](ACCOUNTING_RULES.md): every rule, the proposed treatments and their limitations
- [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md): what stays local, what can leave, controls, retention
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): checklist with verification status, and next steps
- [TESTING.md](TESTING.md): test suites and how to run them

## Known limitations

- **No legal or tax data.**
  - There is no live GSTIN verification and no GSTR-2A/2B or 26AS reconciliation.
  - FinV does not ship statutory TDS rates. TDS works only from company policies your firm approves.
- **PDF and image extraction is pattern-based.**
  - It works on clearly labelled invoices; anything it cannot read is left blank for review.
  - Bank statements and trial balances must be CSV or XLSX.
- **Tally integration is untested against real TallyPrime.** It was tested against a simulated Tally server. Ledger
  names must match Tally's (FinV creates missing ones), and GST tax ledgers may need GST details configured in Tally.
- **Free-form questions need the cloud model.** Greetings, counts, summaries, lists, questions about a specific entry
  ("why does the Kaveri invoice need review?", "what is entry 104 booked to?") and ledger changes are answered
  instantly and exactly from FinV's data. Only other free-form questions go to Gemini Flash (about 2–5 s per
  answer; the free tier allows only a few requests per minute). In testing the model once claimed a change it had not made; FinV now replaces such claims with what
  actually happened, and no change is ever made without Confirm.
- **AI suggestions can be wrong.** An earlier local model mis-classified a bank charge as Purchases; FinV held it for
  review as designed. Every AI suggestion is held for review.
- **Not built for production.** It is single-process, with no background job queue and no HTTPS termination.
  PostgreSQL has not been tested.
