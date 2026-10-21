# Testing

All test data is fictional. GSTINs in the tests are made up but carry valid check digits.

```bash
cd backend && .venv/bin/python -m pytest -q          # 109 tests (about 55 s; the voice test loads Whisper)
cd frontend && npm test                              # 14 tests (Vitest + Testing Library, mocked API)
cd frontend && npm run typecheck                     # TypeScript strict
cd frontend && npm run build                         # production build
python scripts/e2e_browser.py [url] [shots-dir]      # real-browser walkthrough (Playwright + Chromium)
```

The browser walkthrough needs `pip install playwright && playwright install chromium`, and a running API and web app
(see README). It registers a fresh account each run, so it can be repeated on the same database.

Results on 2026-10-20: **178/178 backend, 23/23 frontend, type check clean.** (An earlier run that day: browser
walkthrough passed with no console errors.)

## Coverage map

### Accounting engine (`tests/test_engine_accounting.py`)

- Debit/credit balancing with an exact paisa difference.
- Decimal parsing: Indian grouping, `(…)`, `Cr` and float inputs with no binary drift.
- Malformed, inactive and unassigned lines, and more than two decimal places.
- Period checks: missing, outside every period, locked.
- Duplicate posting and unresolved duplicate candidates block approval.
- A balanced entry that disagrees with its invoice is **not** eligible.
- Missing total means no entry; a derived taxable value is disclosed.
- Purchase invoices without a category ask a question; sales invoices with IGST.
- Bank lines:
  - A credit is not classified from its direction alone.
  - Payment matched by invoice reference; matching by name and amount asks for confirmation.
  - Several records with the same invoice number.
  - Approved keyword policies.
  - AI suggestions always need review.
- Credit/debit notes: linked to the original; cumulative limit; side unknown; party mismatch.
- TDS: applied only from an approved policy, and always kept for review.

### GST, duplicates, reconciliation, audit (`tests/test_engine_checks.py`)

- GST:
  - GSTIN check digit, length and state code.
  - Arithmetic and round-off.
  - CGST/SGST/IGST structure.
  - Place of supply missing or mismatched.
  - Implied-rate plausibility and ITC GSTIN requirement.
- Duplicates and integrity:
  - Exact duplicate with normalised numbers.
  - Same invoice in a different file.
  - Changed amounts are reported as a possible alteration (and never as "fraud").
  - Different supplier GSTIN means no match.
  - A legitimate recurring same-amount transaction is only a "possible" match, and no match when the date differs.
  - Duplicate credit and debit notes; a note never duplicates an invoice.
  - Bank duplicates by reference; a different reference means separate transactions.
  - Running-balance breaks; line items that do not add up.
- Reconciliation: matched, unmatched, outside-window and near-miss cases; reproducibility; ambiguous same-amount
  candidates are never auto-matched.
- Trial balance: totals, roll-forward, duplicate codes, abnormal balances, materiality.
- Ledger and vouching:
  - Duplicates, round amounts, out-of-period and Sunday postings.
  - Vouching agreement and discrepancies.
  - Net-of-GST vouching against the taxable value.
  - Bank-line evidence; missing-evidence threshold.

### Ingestion and privacy (`tests/test_ingestion_privacy.py`)

- Bank CSV with a preamble and opening-balance rows; column provenance.
- Invoice register with mixed types and bad-date rows reported.
- XLSX formulas are not evaluated.
- File-type, magic-byte and size checks; PDFs require a document type.
- Labelled PDF text extraction, including an embedded injection string.
- The AI context is minimised and pseudonymised (no names, account numbers, UPI ids, references or company name).
- The outbound gate blocks original values and raw GSTINs.
- AI responses are rejected for an unknown record, an invented ledger, extra fields or bad confidence.

### API workflow (`tests/test_api_workflow.py`): the Definition of Done

1. An empty overview shows zeros.
2. Invoices are uploaded, entries proposed and validated, and the credit note linked to its invoice.
3. A second export creates an exact duplicate and a single possible-alteration finding, with explanations.
4. Duplicates are confirmed. A corrected entry is re-validated; a wrong correction is caught and approval refused.
5. Approve, then post (idempotent; a posted entry is immutable), then CSV export.
6. The bank payment is matched to its invoice; the unknown credit asks a question.
7. Reconciliation, then the audit trail chain is verified.

Also covered:

- Repeated identical upload and idempotency keys.
- Correcting a record re-proposes the entry, and review points need acknowledgement and a note.
- Failed uploads explain themselves.
- Policies have no effect until approved.
- Corrections only *suggest* policies.

### Security (`tests/test_api_security.py`)

- Unauthenticated access and CSRF header.
- Cross-client access: direct ids, and objects addressed through another company's id.
- Roles: an accountant cannot approve, post or change privacy; a viewer cannot upload or edit.
- Posting before approval is refused.
- Logs contain no GSTINs, names or amounts; errors are sanitised.
- AI:
  - Blocked in local-only mode, with nothing transmitted.
  - The payload sent contains no originals, and the log shows exactly what was sent.
  - Prompt injection cannot produce an invented ledger or extra fields.
  - When the provider fails, the deterministic engine still works.
  - No provider is configured by default.
- Files are encrypted at rest; purge deletes the file and records, but keeps records behind posted entries.
- The retention policy purges old documents.
- Tampering with the audit trail is detected.
- CSV formula injection is neutralised.

### Audit workspace (`tests/test_api_audit.py`)

- Trial balance import with a mis-keyed balance (TB-001 and TB-002).
- Ledger import with a duplicate entry, missing evidence and disagreement with the trial balance.
- Vouching: consistent, discrepancy, double-link refused, evidence reuse flagged, and missing-evidence findings
  resolved automatically once evidence is linked.
- Exception workflow: a comment is required to resolve, and the history is kept.
- Vouching review decisions.
- Audit work never changes accounting entries.
- Reports separate unresolved anomalies from accepted ones and carry the "not a statutory audit" note.
- Re-running checks is idempotent.
- An accountant cannot record audit decisions.

### PDF and OCR (`tests/test_pdf_ocr.py`)

- A generated PDF invoice is uploaded through the API: its fields are extracted with `pdf_text` provenance and
  confidence below 1, and the entry validates.
- A PDF with missing fields gets no invented values and no entry lines; it is held for review.
- The same invoice rendered to PNG is read by local tesseract OCR (skipped if `pdftoppm` or `tesseract` is missing).

### GST returns (`tests/test_gst_filing.py`, `tests/test_gst_portal_browser.py`)

- GSTR-1 sections (B2B, B2CL, B2CS, CDNR) and totals; documents outside the month are excluded. GSTR-3B 3.1(a), 3.2
  and 4(A)(5), with no ITC where the supplier GSTIN is missing.
- Unapproved, undated and multi-rate documents block; preparing twice without changes keeps the version.
- Telegram message has no names, full GSTINs, invoice numbers or exact amounts; review links are stored hashed,
  single-use, expire, need sign-in and firm membership, and never approve.
- Approval: wrong password, missing or short override, stale version or payload hash, accountant role and reviewer
  override are all refused; two simultaneous approvals: only one wins.
- A changed record or entry version invalidates the approval and blocks the portal job and the JSON download.
- Portal jobs (fake portal): duplicate jobs refused; wrong taxpayer stops before upload; totals mismatch stops with
  the differences; an expired session resumes without a second upload; a crash between upload and checkpoint adopts
  the existing upload; a second worker cannot take a live job; a data change mid-job stops it; GSTR-3B fill.
- Filing: "filed" is recorded as reported until the portal shows Filed with an ARN; verified returns are not
  re-prepared or invalidated; every step is in the verified audit chain.
- Real browser (headless Chromium) against a local mock portal: upload, totals read back and matched, verification
  from the status page; the mock's Submit and "File with EVC" buttons are never pressed, and safe_click refuses them.
  This does not prove the selectors match the live GST portal.

### Tally and local AI (`tests/test_tally_localai.py`)

- Tally:
  - Voucher XML nets to zero, uses Tally's sign convention, party ledgers and correct escaping.
  - Masters carry the right groups.
  - Only posted entries are exported, and each only once.
  - Push succeeds against a simulated Tally server; Tally errors are reported and nothing is marked exported.
  - Tally not running gives a clear message; non-local addresses are refused.
- Local AI:
  - AI off and the retired `local_ai` mode refuse the (cloud) provider; pseudonymised mode sends minimised data only.
  - Minimisation still applies, and the schema restricts the answer to real ledger codes.
  - A `"null"` string answer means no suggestion.
  - The connection test uses fictional data only and flags wrong or invalid answers.
  - The Gemini request format (OpenAI-compatible, no thinking, JSON schema) and errors (rejected key); tool-call
    thought signatures are sent back.

**Live check (2026-10-20, llama3.2 on CPU, before the switch to Gemini):** the connection test passed in 28.6 s. A real
bank line was suggested as Purchases instead of Bank Charges; FinV held it for review as designed.

**Live check (2026-10-20, gemini-3.5-flash, fictional data):** the connection-test transaction was classified as
5500 Bank Charges (confidence 0.95) in 2.1 s. A two-step tool call (list_ledgers, then answer) took 3.0 s plain and
3.3 s streamed. `gemini-3.8-flash` (what `gemini-flash-latest` pointed to) did not answer within 60 s on the free tier.

### Assistant (`tests/test_assistant.py`)

- Built-in commands work without AI; changes wait for "yes" or Confirm; "no" cancels.
- `use ledger X for entry N` classifies a bank line through the normal correction path.
- Another user's action cannot be confirmed, and an accountant cannot prepare an approval.
- A scripted model's tool calls are executed and the tool results returned to it.
- A model following injected document text can only create *pending* actions; unknown tools are ignored.
- The cloud model is used only with the company's "Cloud AI" consent; a model failure is reported and stored in the history.
- Audit-workspace tools are separate from accounting tools.
- False "done" claims are corrected; ledgers can be given by name.
- Undecodable audio is rejected.
- Greetings, counts, summaries, entry questions and ledger changes are answered without calling the model.
- Raw JSON from the model is never shown.

**Accuracy and speed check (2026-10-20, 8 typical questions):**
- Before: "hello" got an unrelated reply about duplicates (25 s); a count question returned raw JSON (18 s); a question
  about entry 104 got a confident but wrong ledger (29 s).
- After: all 8 were answered correctly in under 0.1 s, without the model.

**Live check (2026-10-20, before the switch to Gemini):**
- Headless Chromium with a fake microphone said "What needs review?". Local Whisper transcribed it in about 8 s and the
  assistant listed the 3 entries needing review.
- `llama3.2` answered "which bank lines still need a ledger?" correctly in 29 s.
- It prepared "set the SMS line to Bank Charges" in 27 s; after Confirm, the entry was re-validated and the page
  updated.
- Defects found and fixed during the live checks: PyAV 19 is incompatible with faster-whisper (pinned 13.1.0); a false
  success claim (honesty guard added); 0.00 amounts on unclassified lines.

### Cloud chat and streaming (`tests/test_cloud_chat.py`)

These run against a simulated Groq API; there is no real Groq key on this machine.

- Cloud chat is refused unless the company chose "Cloud AI".
- The OpenAI-format tool loop sends tool call ids, JSON-string arguments and the right endpoint and key.
- Invalid-key, rate-limit and missing-key errors are shown in plain language.
- Streaming sends words, then the final message, which is saved.
- Instant answers never call the cloud.

### Frontend (`frontend/src/test/app.test.tsx`)

- The landing screen offers exactly Accounting and Audit, with no sidebar or tables.
- Each card opens its own workspace, with its own navigation; keyboard Enter works.
- Company onboarding is shown when no company exists; unauthenticated users are redirected.
- The empty overview shows zeros.
- Failure plus retry.
- Upload: progress, result and the type sent; unsupported types are refused before upload.
- Entry review:
  - Findings, explanation and duplicate warning are shown.
  - Approve is disabled while errors exist.
  - Line correction sends the chosen ledger.
  - Approval with review points needs acknowledgement and a note.
- Duplicate decision with a note.
- Audit exception resolution needs a comment, and the history is shown.
- Assistant: absent from the landing screen; in workspaces it sends messages, follows navigation and confirms actions;
  an approval with review points needs an explanation.

**Responsive layout** is verified in the browser walkthrough at 390 px: no horizontal page scroll on the Accounting
overview, and screenshots 17–18. It is not verified in jsdom.

## Not covered by automated tests

- Real-world scanned invoices of varying quality. OCR is tested on one clean, generated image only.
- PostgreSQL. All tests run on SQLite.
- Live calls to Anthropic or OpenAI. No credentials are available; provider code is exercised through a fake provider
  only.
- Concurrency and load.
