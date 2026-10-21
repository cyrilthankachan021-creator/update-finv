export interface Org { id: number; name: string; role: string }
export interface Me { id: number; name: string; email: string; organizations: Org[] }
export interface Company { id: number; name: string; gstin: string | null; state_code: string | null; currency: string; organization_id: number; role: string | null; services?: Service[] }
export type Service = "accounting" | "audit";
/** Workspaces the firm offers for this client; older data without the field gets both. */
export const servicesOf = (c: Company | null | undefined): Service[] => c?.services?.length ? c.services : ["accounting", "audit"];

export interface Doc {
  id: number; filename: string; kind: string; workspace: string; status: string; error: string | null;
  size_bytes: number; sha256: string; record_count: number; notes: string[]; duplicate_of_id: number | null;
  content_type: string; has_file: boolean; created_at: string; idempotent_replay?: boolean; validation?: BankValidation | null;
}

export interface BankValidation {
  status: "passed" | "needs_attention" | "failed"; period_start: string | null; period_end: string | null;
  lines_accepted: number; lines_rejected: number; rejected: { row: number; message: string }[];
  total_debits: string; total_credits: string; first_balance: string | null; last_balance: string | null;
  balance_breaks: { row: number; expected: string; stated: string }[]; repeated_lines: number;
  checks: { check: string; ok: boolean | null; detail: string }[];
}

export interface RecordBrief {
  id: number; record_type: string; status: string; party_name: string | null; party_gstin: string | null;
  doc_number: string | null; doc_date: string | null; total_amount: string | null; description: string | null;
  reference: string | null; debit_amount: string | null; credit_amount: string | null; document_id: number | null;
  review_question: string | null; has_open_duplicate?: boolean;
}

export interface RecordFull extends RecordBrief {
  note_side: string | null; original_doc_number: string | null; original_record_id: number | null;
  place_of_supply: string | null; taxable_amount: string | null; cgst: string | null; sgst: string | null;
  igst: string | null; cess: string | null; due_date: string | null; category_hint: string | null;
  bank_account_code: string | null; balance: string | null; row_index: number | null;
  provenance: Record<string, { source?: string; column?: string; confidence?: number; method?: string }>;
  raw: Record<string, unknown>; document: Doc | null;
}

export interface Check { rule_id: string; rule_version: string; status: string; message: string; evidence: Record<string, unknown> }

export interface Line { id?: number; account_id: number | null; account_code?: string | null; account_name?: string | null; debit: string; credit: string; description?: string | null }

export interface Explanation {
  summary?: string; accounts?: { code: string; name: string; reason: string }[];
  evidence?: { field: string; value: string | null; source: string }[]; uncertainties?: string[];
  next_action?: string; question?: string | null; matched_record_id?: number; policy?: { key: string; version: number } | null;
  correction_note?: string; resolved_question?: string;
}

export interface Entry {
  id: number; source_record_id: number | null; entry_date: string | null; narration: string | null; treatment: string | null;
  status: string; posting_eligible: boolean; version: number; origin: string; explanation: Explanation; lines: Line[];
  total_debit: string; total_credit: string; validation?: Check[]; validation_counts?: Record<string, number>;
  rejection_reason: string | null; approved_at: string | null; created_at: string;
  source_record?: RecordFull | null; posting?: { id: number; posted_at: string } | null; history?: Event[];
}

export interface Dup {
  id: number; record_id: number; matched_record_id: number; kind: string; strength: string; score: number;
  signals: Record<string, unknown>; explanation: string; status: string; decision_note: string | null;
  record: RecordBrief | null; matched_record: RecordBrief | null; decided_at: string | null;
}

export interface Account { id: number; code: string; name: string; type: string; role: string | null; is_active: boolean }
export interface Event { id: number; action: string; object_type: string | null; object_id: number | null; details: Record<string, unknown>; user_name: string | null; created_at: string; hash: string }
export interface Policy { id: number; key: string; version: number; kind: string; definition: Record<string, string | null>; status: string; source: string; rationale: string | null; effective_from: string | null; created_at: string }

export interface AuditException {
  id: number; import_id: number | null; object_type: string; object_id: number | null; rule_id: string; severity: string;
  title: string; explanation: string; evidence: Record<string, unknown>; suggested_action: string | null; status: string;
  created_at: string; history?: { id: number; user_name?: string; from_status: string | null; to_status: string | null; comment: string | null; created_at: string }[];
  ledger_entry?: LedgerEntry | null; tb_line?: Record<string, string | null> | null;
}
export interface TBImport { id: number; kind: string; document_id: number | null; period_label: string | null; period_start: string | null; period_end: string | null; summary: Record<string, unknown>; created_at: string }
export interface LedgerEntry { id: number; import_id: number; row_index: number; entry_date: string | null; account_code: string | null; account_name: string | null; voucher_no: string | null; voucher_type: string | null; party: string | null; reference: string | null; narration: string | null; debit: string; credit: string; vouching?: Vouching[] }
export interface Vouching { id: number; ledger_entry_id: number; record_id: number; result: { checks: { check: string; ok: boolean | null; detail: string }[]; outcome: string; summary: string }; outcome: string; review_status: string; comment: string | null; record?: RecordBrief | null }
export interface Paged<T> { total: number; page: number; page_size: number; items: T[] }
