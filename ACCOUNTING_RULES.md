# FinV accounting rules

The engine is deterministic: the same inputs always give the same results. Every check has a stable id, a version and
a worst-case outcome. Results are stored with the rule id and version against the exact entry version they checked.

Each check returns one of these outcomes:

| Outcome | Meaning | Effect |
|---|---|---|
| `pass` | Check satisfied | None |
| `warning` | Worth a look | Does not block |
| `needs_review` | A person must decide | Blocks automatic eligibility; approval needs explicit acknowledgement and a note |
| `error` | Contradiction | Blocks approval and posting |
| `not_applicable` | Check does not apply | None |

**"Validated" never means "verified correct".** It means none of FinV's checks found an error or an open question
against the recorded evidence. A balanced entry can still be economically wrong: ACC-007 is a separate check that the
entry agrees with its document.

Regenerate the table below with `python scripts/rules_catalogue.py`.

| Rule | Version | Worst outcome | Check | What it does |
|---|---|---|---|---|
| ACC-001 | 1.0 | error | Debits equal credits | Total debits must equal total credits to the paisa. |
| ACC-002 | 1.0 | error | Well-formed journal lines | At least two lines; each line has exactly one positive debit or credit amount. (An empty entry with an open question is `needs_review`.) |
| ACC-003 | 1.0 | error | Valid ledger accounts | Every line uses an active ledger account from this company's chart of accounts. |
| ACC-004 | 1.0 | error | Entry date and accounting period | The entry has a date inside a defined, unlocked accounting period. (No period defined → `needs_review`.) |
| ACC-005 | 1.0 | error | Amount precision | Amounts have at most two decimal places. |
| ACC-006 | 1.0 | review | Supporting document | Entries should be linked to a source record (invoice, note or bank line). |
| ACC-007 | 1.0 | error | Entry agrees with its evidence | Entry amounts, tax lines and control accounts must agree with the source document. |
| ACC-008 | 1.0 | error | No duplicate posting | A source record may only have one approved or posted entry. |
| ACC-009 | 1.0 | review | Open duplicate candidates | Possible duplicates of the source record must be resolved before approval. |
| ACC-010 | 1.0 | review | Classification resolved | The accounting treatment must be supported by evidence; open questions need an answer. |
| GST-001 | 1.0 | review | GSTIN format and checksum | Checks the party GSTIN structure, state code and check digit. Does not confirm registration status. |
| GST-002 | 1.0 | error | Invoice arithmetic | Taxable value + CGST + SGST + IGST + cess must equal the invoice total (round-off up to 1.00 is a warning). |
| GST-003 | 1.0 | error | Tax structure | An invoice charges either CGST+SGST (equal amounts) or IGST, not both. |
| GST-004 | 1.0 | review | Place of supply consistency | Under the general rule, intra-state supplies carry CGST+SGST and inter-state supplies carry IGST. Missing data or a mismatch needs review. |
| GST-005 | 1.0 | warning | Implied GST rate plausibility | The implied rate (tax / taxable value) should be one of the configured slab rates. |
| GST-006 | 1.0 | review | Input tax credit support | Input tax on a purchase needs a valid supplier GSTIN to support an input tax credit claim. |
| NOTE-001 | 1.0 | review | Note references an original invoice | A credit/debit note should cite the invoice it adjusts. |
| NOTE-002 | 1.0 | review | Original invoice found | The referenced original invoice exists in this company's records. |
| NOTE-003 | 1.0 | error | Note party matches original | The note's party (GSTIN or name) matches the original invoice. |
| NOTE-004 | 1.0 | error | Credit notes do not exceed the invoice | Total credit notes against an invoice must not exceed the invoice value. |
| NOTE-005 | 1.0 | review | Note tax heads match original | A note normally uses the same tax heads (CGST/SGST vs IGST) as the original invoice. |
| TDS-001 | 1.0 | review | TDS consideration | Where an approved company TDS policy covers the expense, the entry should account for TDS; thresholds and section must be confirmed. |
| TB-001 | 1.0 | error | Trial balance totals agree | Total debits equal total credits (closing balances, or movements when no closing column). |
| TB-002 | 1.0 | error | Opening + movement = closing | For each account, opening balance + debits - credits equals the closing balance. |
| TB-003 | 1.0 | warning | Duplicate account codes | Each account code appears once. |
| TB-004 | 1.0 | warning | Missing account details | Each line has an account code or name. |
| TB-005 | 1.0 | warning | Abnormal balance direction | Assets/expenses normally carry debit balances; liabilities/equity/income carry credit balances. |
| TB-006 | 1.0 | warning | Unexpected classification | Account names that clearly indicate a type (e.g. 'Bank', 'Sales') should be classified accordingly. |
| TB-007 | 1.0 | warning | Material movement | Account movements above materiality are highlighted for review. |
| LED-001 | 1.0 | warning | Possible duplicate ledger entries | Same account, date, amount and voucher/reference appear more than once. |
| LED-002 | 1.0 | warning | Large round-amount entries | Round amounts at or above materiality are a common audit focus area. |
| LED-003 | 1.0 | warning | Entries outside the period | Entry dates fall outside the import period. |
| LED-004 | 1.0 | error | Ledger agrees with trial balance | Ledger movements per account equal the trial balance movements for the same period. |
| LED-005 | 1.0 | warning | Sunday postings | Entries dated on a Sunday can indicate unusual activity. |
| VCH-001 | 1.0 | warning | Missing supporting evidence | Ledger entries at or above the vouching threshold should be linked to evidence. |
| VCH-002 | 1.0 | error | Evidence agrees with entry | Amount, date, counterparty and reference of the evidence agree with the ledger entry. |
| VCH-003 | 1.0 | warning | Evidence used for several entries | The same supporting document is linked to more than one ledger entry. |

In the Audit workspace an "error" outcome is shown as **high severity**. Audit findings are anomalies for the auditor's
judgement, not confirmed errors.

## Proposed treatments

The starter chart gives each ledger a *role* (bank, payables, input_cgst and so on). The engine uses ledgers by role,
so firms can rename or renumber accounts freely.

| Document | Proposed entry | When FinV asks instead |
|---|---|---|
| Purchase invoice | Dr expense/purchases (taxable), Dr input CGST/SGST/IGST/cess, Dr/Cr round-off, Cr Trade Payables (total) | Unless an **approved** party policy or the document's category names the ledger, FinV proposes Purchases and asks whether the purchase is stock, an expense or an asset |
| Sales invoice | Dr Trade Receivables (total), Cr Sales (taxable), Cr output taxes | Missing total |
| Credit note, sales side (issued to customer) | Dr Sales Returns, Dr output taxes, Cr Receivables | Side unknown: no original invoice found and no side given |
| Credit note, purchase side (received from supplier) | Dr Payables, Cr Purchase Returns, Cr input taxes | Same |
| Debit note, sales side | Dr Receivables, Cr Sales, Cr output taxes | Same |
| Debit note, purchase side | Treated as the supplier's debit note (additional charge): Dr Purchases, Dr input taxes, Cr Payables. Flagged as uncertain: a buyer-issued debit note for a return needs a different treatment | Same |
| Bank line | (1) Reference contains the number of exactly one open invoice in the right direction → supplier payment or customer receipt. (2) Party name plus exact outstanding amount → proposed, with a confirmation question. (3) An **approved** keyword policy → that ledger. (4) An AI suggestion (if enabled) → always needs review | Otherwise no lines are proposed, and FinV asks which ledger to use. **Direction alone is never used to decide** (a credit can be a receipt, loan, capital, refund or transfer) |

Further details of how the engine behaves:

- **Missing data is never invented.** If the taxable value is missing but the taxes are present, the taxable value is
  derived as total minus taxes, and the explanation says so. If the total is missing, no entry is proposed.
- **Credit notes do not have to reverse the whole invoice.** NOTE-004 only checks that cumulative credit notes do not
  exceed the invoice value.
- **TDS uses only company policies.** FinV contains **no statutory TDS rates or thresholds**. A `tds_category` policy
  must be entered and approved by the firm, with the section label and rate the firm has verified. Even then, TDS-001
  stays `needs_review` so that a person confirms the section and threshold.

## Duplicates and document integrity

| Finding | Signals | Notes |
|---|---|---|
| Exact duplicate | Same type and party (GSTIN, or normalised name), same normalised number, same amounts and date | Numbers normalise case, separators and leading zeros within digit groups: `INV-001` = `inv 001`, but `INV-001` ≠ `INV-0010` and `2024/01` ≠ `202401` |
| Possible alteration | Same party and number, but different taxable value, tax, total, date or name | Labelled "Document inconsistency detected". Never called fraud: a changed hash is not proof of tampering |
| Probable duplicate | Same party and total, numbers one character apart | |
| Possible duplicate | Same party, date and total, different number | Informational. Recurring transactions are legitimate |
| Bank exact duplicate | Same date, amount, direction and reference | Typical cause: overlapping statements |
| Bank probable duplicate | Same date, amount and description, no reference | Different references mean separate transactions |
| Repeated upload | Identical SHA-256 of the file | The file is not processed again |
| Line items | If line items were extracted, their sum must equal the taxable value | |

Further behaviour:

- The same supplier, amount and date **alone never establishes a duplicate**.
- New records are compared only with *canonical* earlier records, so one problem is reported once.
- Records are never deleted. A confirmed duplicate's open entry is rejected, with the decision recorded in the audit
  trail. If its entry was already posted, FinV warns and asks for a reversing entry through the normal workflow.

## Bank reconciliation

- **Version 1.0.** An automatic match requires equal amounts (to the paisa), the same direction and the same bank
  ledger, within the date window (default 3 days, configurable).
- **Score.** Date proximity, a reference found in the narration and shared words add to the score.
- **Ties** are never auto-matched. They are reported as "needs manual match".
- **Also reported:** near misses (small amount differences), possible transfers between the company's own bank
  accounts, unmatched lines and unmatched entries.
- **Unmatched does not mean an error.** An unmatched line often only means its entry has not been approved yet.
- **Running balance.** Statement running balances are checked in both orders, and breaks are reported in the
  document notes.

## Input conventions

- **Dates** use the Indian day-first convention (DD/MM/YYYY) for numeric dates. ISO dates (YYYY-MM-DD) are also
  accepted. A value that is not a date is reported for its row and the row is skipped.
- **Amounts** accept Indian grouping (`1,23,456.78`), `(500)` and `500 Cr` as negative, `Dr` and `₹`/`Rs`/`INR`
  prefixes. Anything else is rejected, never guessed. All arithmetic uses `Decimal`.
- **Spreadsheet formulas** are never evaluated. FinV reads the value the spreadsheet application cached, or nothing.
- **Exported CSV cells** that start with `= + - @` are prefixed with `'` to prevent formula injection.

## Limitations needing professional verification

These need verification or extension before relying on them:

- **GST-004** models only the general place-of-supply rule. SEZ supplies, exports, imports, specific services
  (sections 12–13 of the IGST Act), reverse charge and composition dealers are not modelled. A mismatch is therefore
  `needs_review`, never `error`.
- **GST-005** checks plausibility against a slab list (version `2025-10`: 0, 0.1, 0.25, 1.5, 3, 5, 6, 12, 18, 28, 40).
  It does not determine the correct rate for any goods or service. Update the list in `engine/gst.py` when rates change.
- **GST-001** checks structure and check digit only. There is no GSTN lookup of registration status.
- **ITC eligibility** (blocked credits under section 17(5), time limits, matching with GSTR-2B) is not implemented.
  GST-006 only checks that a valid supplier GSTIN is present.
- **TDS** has no built-in sections, rates, thresholds or 26AS reconciliation. It works only from firm-approved policies.
- **Purchase-side debit notes** are assumed to be supplier debit notes. A buyer-issued debit note for a return needs a
  manual correction.
- **Partial payments and advances** are proposed with a confirmation question. FinV does not track advance adjustments.
- **Multi-currency.** Currency is stored, but FinV assumes INR everywhere.
