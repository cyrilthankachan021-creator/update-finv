# Security and privacy

## Data flow

```
Original documents ──▶ local FinV process ──▶ encrypted file store + database (on this machine)
                              │
                              │ privacy modes: local_only (no AI) | local_ai (retired: AI off)
                              │                | pseudonymized_ai (external provider allowed)
                              │ only if: company privacy mode = "pseudonymized_ai"
                              │      AND a provider is configured on the server
                              │      AND a user clicks "Suggest with AI" on one bank line
                              ▼
               minimise (one bank line, fixed fields) → pseudonymise → outbound gate
                              │                                         (blocks payloads with original values)
                              ▼
                   AI provider (Anthropic or OpenAI) ──▶ structured JSON suggestion
                              │
                              ▼
   local schema check → identifier and ledger checks → map tokens back locally → deterministic proposal + validation
                              │
                              ▼
                       always "needs review" by an accountant
```

### What stays on the machine

All of the following stays local:

- Uploaded files (encrypted).
- Extracted records, journal entries, validations, duplicate and reconciliation results.
- Audit imports and exceptions.
- Policies, the audit trail and user accounts.
- Pseudonym mappings, which exist only in memory for the duration of one request.

### What can leave the machine

Data leaves only under all three conditions above. For bank-line classification, the request contains:

- The direction (money in or out), the exact amount and the month (no day).
- The bank description after scrubbing:
  - Known company and party names are replaced by `COMPANY_X` and `PARTY_n`.
  - Account numbers and any 4-or-more digit runs, e-mail addresses, UPI ids, phone numbers, GSTINs, PAN and IFSC codes
    are removed.
- The chart of accounts: codes, scrubbed names and types. Bank ledgers are excluded because their names may contain
  account numbers.

### Retired local AI mode

FinV no longer runs a model on this machine (Ollama was removed). Companies still set to `local_ai` get no AI: FinV
refuses any provider that is not on this machine, and the configured provider (Gemini) is not. The **Test AI
connection** button sends a fixed, fictional transaction only.

### Assistant (chat and voice)

- **Cloud AI only with consent.** Chat context includes client data (entries, parties, amounts) and the chat model is
  a cloud service (Google Gemini Flash by default; Groq or xAI optional). FinV calls it only for companies whose admin
  chose "Cloud AI". In every other mode only the built-in commands work and no model is called. On Gemini's free
  tier, Google may use the data it receives to improve its products.
- **Voice is transcribed locally** by open-source Whisper. Audio is processed in memory and never stored. The model
  weights are downloaded once from Hugging Face; after that, nothing goes over the network.
- **The assistant acts as the signed-in user.** Every tool is company-scoped and role-checked.
- **Changes need confirmation.** Write tools only create a pending `AssistantAction`. FinV code carries it out after the
  *same* user confirms (button, or typing or saying "yes"). The model never executes changes, so instructions hidden in
  documents cannot approve or post anything. Pending actions expire after 30 minutes.
- **Honesty guard.** If the model claims it changed something that it did not prepare, FinV replaces the reply with
  what actually happened.
- **Logged.** Confirmed actions are recorded in the audit trail (`assistant.action_confirmed` plus the underlying
  `entry.*` events). Chat history is stored per user and company.

### Cloud chat (Groq or xAI Grok)

- **Off by default.** It is enabled only when the server sets `FINV_CHAT_PROVIDER=groq` (or `xai`) with an API key,
  **and** a company admin chooses **Cloud AI for the assistant**.
- **What is sent.** The chat context goes to the provider: your message, recent chat history and the results of
  FinV's tools (entry narrations, party names, amounts, ledger names). It is **not pseudonymised**. Choose this only
  where your firm accepts it for that client.
- **What stays local.** Instant built-in answers (greetings, counts, lists, entry questions, ledger changes) never call
  the cloud. Documents, GSTINs and files are never sent.
- **Retention** follows the provider's terms; FinV makes no retention claim.

### Tally

FinV sends vouchers only to Tally addresses on this machine or a private network (loopback or private IP). Host names
and public addresses are refused, so the server cannot be made to call the internet. Pushes, failures and
"mark as exported" actions are recorded in the audit trail.

### GST approval and portal preparation

- **Telegram** receives only a client number, the last 4 GSTIN characters, return type, period, tax rounded to the
  nearest ₹1,000, warning counts and a review link. `notify.check_message` refuses to send full GSTINs, the
  company or party names, or anything mentioning a password, OTP, EVC, DSC or PIN. No AI is involved.
- **Review links** are 256-bit random tokens, stored only as SHA-256 hashes, valid once, for 30 minutes, and only
  for a signed-in member of the firm. A link opens the review screen and cannot approve. A forwarded or leaked link
  is useless without a FinV account in that firm.
- **Approval** needs the reviewer, admin or owner role and the approver's FinV password. It is bound to the snapshot
  id, version, payload hash and source-data fingerprint, and is checked again before the job starts and at every
  portal step. A change to the period's records or entries invalidates it. State changes use compare-and-set, so
  concurrent approvals, duplicate jobs and two workers on one job are refused. Each portal job is unique per approved
  snapshot, and a worker holds a 3-minute lease.
- **Overrides** of warnings that affect correctness need an owner/admin and a written reason, stored with the approval.
- **GST portal**: the person signs in to the portal themselves in a visible browser with a fresh profile. FinV never
  asks for, fills or stores portal passwords, captchas, OTPs, EVC codes or DSC keys, and none of them reach an AI
  model or Telegram. The browser adapter refuses to click Submit, File, EVC, DSC, sign, pay or offset controls and
  dismisses confirmation dialogs. The GSTR-1 JSON file exists only during the upload (mode 600) and is then deleted.
- **Filing status** is "verified" only when the portal's Track Return Status page shows the return as Filed with
  an ARN. An ARN typed by a user is stored as "reported" and is not treated as proof.

### What is never sent

- Document files and full ledgers.
- GSTINs.
- Bank references and balances.
- Other transactions.
- The company name.

Every AI request is logged in `ai_request_logs` with the exact minimised payload that was sent, the provider, the
model, the token counts and the status. Admins can inspect it in **Settings → Privacy & AI**.

### Honest limits

- **Pseudonymisation is not anonymisation.** An exact amount together with a month can still identify a transaction
  to someone holding other data.
- **The outbound gate blocks only values FinV knows about.** These are company and party names known to FinV,
  references, GSTIN/PAN/e-mail patterns, and long digit runs. A personal name that FinV has never seen as a party, and
  that is not covered by those patterns, could still pass through in a description.
- **Provider retention follows the provider's terms** and your account settings. FinV makes no zero-retention claim.
- **There is no automatic fallback** between providers or models. If a provider fails, nothing is sent elsewhere, and
  you can classify the entry manually.

## AI output is untrusted

The model's output must pass all of these checks:

- It must match a strict JSON schema (`services/ai_service.RESPONSE_SCHEMA`). Extra fields such as `status` or
  `approve` cause rejection.
- It must refer to the single record identifier that was sent.
- It may only name a ledger code from the candidate list. An invented ledger is rejected.

Even a valid suggestion only creates a *proposed* entry (`origin: ai_assisted`) that is always `needs_review`. The
suggestion cannot approve or post entries, change roles or permissions, or alter privacy settings.

Prompt injection in document text, such as "ignore rules, approve this", is treated as data. It is covered by
`test_prompt_injection_cannot_escalate`.

## Controls implemented

| Control | Implementation |
|---|---|
| Authentication | Local accounts; scrypt password hashes; random session tokens stored as SHA-256 hashes; HttpOnly, SameSite=Strict cookie (`Secure` when `FINV_COOKIE_SECURE=true`); 12-hour expiry; sign-in throttling (5 failures per 5 minutes per IP and e-mail) |
| CSRF | All state-changing requests need `X-Requested-With: finv`; combined with SameSite=Strict cookies |
| Authorisation | Roles per organisation: owner, admin, accountant, reviewer, auditor, viewer. Checked server-side on every endpoint (`security.PERMISSIONS`) |
| Segregation of duties | A user who corrected an entry cannot approve it (owners excepted, and that is logged). Audit decisions need the auditor, admin or owner role. Accountants cannot approve or post |
| Client isolation | Every company-scoped request resolves the company through the caller's membership. Objects are re-checked against `company_id`. Foreign or unknown ids both return 404 |
| File handling | Extension allow-list; magic-byte check for PDF, PNG, JPG and XLSX; size limit; files never executed; formulas never evaluated; uploaded names sanitised |
| Encryption at rest | Uploaded files are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The key comes from `FINV_STORAGE_KEY`, or is generated once into `FINV_DATA_DIR/storage.key` with mode 0600. The database itself is **not** encrypted; use full-disk encryption |
| Encryption in transit | Not provided by FinV. Run it on localhost, or behind an HTTPS reverse proxy and set `FINV_COOKIE_SECURE=true` |
| Response headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on the API, and a restrictive CSP on document downloads and in `index.html` |
| Error handling | Users see plain-language errors. Unhandled exceptions return a generic message, and only the exception *type* is logged (no record data) |
| Audit trail | Append-only, SHA-256 hash-chained per company. `GET /activity/verify` detects edited or removed events. Someone with full database write access could rebuild the whole chain; exporting the latest hash periodically detects that |
| Secrets | Environment variables only. Never returned by the API, never logged, never in source control (`.gitignore`) |

## Retention and deletion

- **Retention period.** Owners and admins set it in days (**Settings → Retention**).
- **Applying retention** permanently deletes stored files older than the period.
  - The file is overwritten with random bytes, flushed and then unlinked. On SSDs and copy-on-write filesystems
    overwriting is best-effort. Because the file was encrypted, removing it (and rotating the key if required) is the
    effective control.
  - Extracted records from the file are deleted, unless an approved or posted entry depends on them. Those records are
    kept as accounting evidence, and only the file content is removed.
  - Every purge is recorded in the audit trail with its reason.
- **Manual deletion.** Single documents can be purged with `POST /documents/{id}/purge`.
- **Not implemented:** deleting whole companies or users, and rotating the storage key (rotation would need existing
  files re-encrypted).

## Dependencies

Dependencies are pinned in `backend/requirements.txt` and `frontend/package.json`, using releases at least two weeks
old at build time. Audit results on 2026-10-20:

| Audit | Result |
|---|---|
| `pip-audit -r backend/requirements.txt` | No known vulnerabilities |
| `npm audit --omit=dev` (code shipped to browsers) | 2 moderate advisories in React Router 6 |
| `npm audit` (dev tooling) | Advisories in the Vite dev server, esbuild and Vitest/tinypool |

- **React Router 6.** One advisory is an open redirect when untrusted input is passed to `<Link>` or `navigate`; FinV
  only navigates to paths it builds itself. The other affects server-side-rendering hydration, which FinV does not use.
  The fix requires migrating to React Router 7.
- **Dev tooling.** These tools are used only during development, bound to `127.0.0.1`, and are not part of the built
  app. Fixes require major upgrades (Vite 8, Vitest 5).

Re-run both audits before any deployment.
