import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi, renderApp, signedIn } from "./helpers";

const EMPTY_OVERVIEW = { pending_review: 0, validated: 0, approved: 0, posted: 0, rejected: 0, issues_found: 0, possible_duplicates: 0, recent_documents: [], upload: { max_mb: 20, extensions: [] } };
const AUDIT_OVERVIEW = { imports: [], exceptions_by_status: {}, open_by_severity: {}, awaiting_review: 0, vouching: { linked: 0, discrepancies: 0, pending_review: 0 }, recent_activity: [] };

const FIRM = {
  as_of: "2026-10-21", financial_year: "2026-27", attention: [], top_issues: [],
  summary: { clients: 0, gst_pending: 0, gst_overdue: 0, awaiting_review: 0, due_soon: 0, issues_open: 0, issues_high: 0, audit_findings: 0, missing_data: 0 },
  not_tracked: [{ area: "TDS payments and returns", reason: "No TDS records yet." }],
};

describe("After sign-in", () => {
  it("lands directly on the firm dashboard with the firm sidebar, with no client details step first", async () => {
    mockApi(signedIn([{ match: "/api/firm/overview", reply: { ...FIRM, summary: { ...FIRM.summary, clients: 1 } } }]));
    renderApp("/", { company: false });          // nothing selected: the dashboard must not ask for it
    expect(await screen.findByRole("heading", { name: "Firm dashboard" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Firm navigation" });
    for (const l of ["Firm Dashboard", "Clients", "Compliance and Deadlines", "Error Center", "Integrations and Imports", "Settings"]) {
      expect(within(nav).getByRole("link", { name: l })).toBeInTheDocument();
    }
    expect(screen.queryByRole("heading", { name: "Add a company" })).not.toBeInTheDocument();
  });

  it("shows an empty state with Add new client when the firm has no clients", async () => {
    mockApi(signedIn([{ match: "/api/firm/overview", reply: FIRM }], false));
    renderApp("/", { company: false });
    expect(await screen.findByText("No clients yet")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Add new client" })[0]);
    expect(await screen.findByRole("heading", { name: "Add a company" })).toBeInTheDocument();
  });

  it("opens the Accounting workspace with its own navigation", async () => {
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }]));
    renderApp("/accounting");
    const nav = await screen.findByRole("navigation", { name: "Accounting navigation" });
    for (const l of ["Overview", "Transactions", "Entries", "Bank Reconciliation", "Duplicates", "Reports"]) {
      expect(within(nav).getByRole("link", { name: l })).toBeInTheDocument();
    }
    expect(await screen.findByText("Upload financial documents")).toBeInTheDocument();
  });

  it("opens a separate Audit workspace", async () => {
    mockApi(signedIn([{ match: /audit\/overview$/, reply: AUDIT_OVERVIEW }]));
    renderApp("/audit");
    const nav = await screen.findByRole("navigation", { name: "Audit navigation" });
    for (const l of ["Trial Balance", "Ledger Review", "Vouching", "Exceptions", "Audit Reports"]) {
      expect(within(nav).getByRole("link", { name: l })).toBeInTheDocument();
    }
    expect(within(nav).queryByRole("link", { name: "Bank Reconciliation" })).not.toBeInTheDocument();
    expect(await screen.findByText("Upload a trial balance or ledger export")).toBeInTheDocument();
  });

  it("redirects to sign in when not authenticated", async () => {
    mockApi([{ match: "/api/auth/me", reply: { detail: "Sign in to continue." }, status: 401 }]);
    renderApp("/accounting");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("Accounting overview", () => {
  it("shows zero figures and an empty state from real (empty) data", async () => {
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }]));
    renderApp("/accounting");
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    const pending = screen.getByRole("button", { name: /Pending review/ });
    expect(within(pending).getByText("0")).toBeInTheDocument();
  });

  it("shows a failure with a working retry", async () => {
    let n = 0;
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: () => { n += 1; if (n === 1) throw new Error("x"); return { ...EMPTY_OVERVIEW, pending_review: 3 }; } }]));
    // first call rejects inside the handler -> fetch mock throws -> network error message
    renderApp("/accounting");
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach/i);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(within(screen.getByRole("button", { name: /Pending review/ })).getByText("3")).toBeInTheDocument());
  });

  it("uploads a file with progress and reports the result", async () => {
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }]));
    const sent: FormData[] = [];
    class FakeXHR {
      upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null; onerror: (() => void) | null = null; onabort: (() => void) | null = null;
      status = 0; responseText = ""; headers: Record<string, string> = {};
      open() { /* noop */ }
      setRequestHeader(k: string, v: string) { this.headers[k] = v; }
      abort() { this.onabort?.(); }
      send(f: FormData) {
        sent.push(f);
        this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
        setTimeout(() => { this.status = 201; this.responseText = JSON.stringify({ id: 1, filename: "bank.csv", status: "processed", record_count: 4, notes: [], kind: "bank_statement" }); this.onload?.(); }, 0);
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    renderApp("/accounting");
    await screen.findByText("Upload financial documents");
    await userEvent.selectOptions(screen.getByLabelText("Document type"), "bank_statement");
    const file = new File(["Date,Description,Debit,Credit\n"], "bank.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByTestId("file-input"), file);
    expect(await screen.findByText("4 records extracted and checked.")).toBeInTheDocument();
    expect(sent[0].get("kind")).toBe("bank_statement");
  });

  it("refuses unsupported file types before uploading", async () => {
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }]));
    renderApp("/accounting");
    await screen.findByText("Upload financial documents");
    const input = screen.getByTestId("file-input");
    fireEvent.change(input, { target: { files: [new File(["x"], "virus.exe")] } });
    expect(await screen.findByText(/this file type is not supported/)).toBeInTheDocument();
  });
});

const RECORD = {
  record: { id: 5, record_type: "purchase_invoice", status: "needs_review", party_name: "Kaveri Traders", party_gstin: null, doc_number: "INV-002",
    doc_date: "2025-06-12", total_amount: "5900.00", description: null, reference: null, debit_amount: null, credit_amount: null, document_id: 1,
    review_question: "Confirm the ledger for this purchase: goods for resale, an expense, or a fixed asset?", note_side: null, original_doc_number: null,
    original_record_id: null, place_of_supply: "27", taxable_amount: "5000.00", cgst: null, sgst: null, igst: "900.00", cess: null, due_date: null,
    category_hint: null, bank_account_code: null, balance: null, row_index: 2, provenance: {}, raw: { "Invoice No": "INV-002" }, document: null },
  entries: [{ id: 11, source_record_id: 5, entry_date: "2025-06-12", narration: "Purchase Invoice INV-002", treatment: "purchase_invoice", status: "needs_review",
    posting_eligible: false, version: 1, origin: "engine", rejection_reason: null, approved_at: null, created_at: "2025-06-12T00:00:00",
    explanation: { summary: "FinV identified a purchase-side purchase invoice INV-002 from Kaveri Traders for 5,900.00.", question: "Confirm the ledger for this purchase: goods for resale, an expense, or a fixed asset?",
      accounts: [{ code: "5100", name: "Purchases", reason: "Defaulted to the Purchases ledger." }], uncertainties: ["No policy or category identifies whether this is stock, an expense or an asset."] },
    lines: [{ id: 1, account_id: 21, account_code: "5100", account_name: "Purchases", debit: "5000.00", credit: "0.00" },
      { id: 2, account_id: 6, account_code: "1302", account_name: "Input IGST", debit: "900.00", credit: "0.00" },
      { id: 3, account_id: 11, account_code: "2100", account_name: "Trade Payables", debit: "0.00", credit: "5900.00" }],
    total_debit: "5900.00", total_credit: "5900.00",
    validation: [{ rule_id: "ACC-001", rule_version: "1.0", status: "pass", message: "Debits 5,900.00 equal credits 5,900.00.", evidence: {} },
      { rule_id: "ACC-010", rule_version: "1.0", status: "needs_review", message: "Confirm the ledger for this purchase: goods for resale, an expense, or a fixed asset?", evidence: {} },
      { rule_id: "GST-001", rule_version: "1.0", status: "error", message: "GSTIN check digit is X but should be Y; the number may be mistyped.", evidence: {} }],
    validation_counts: { pass: 1, needs_review: 1, error: 1 } }],
  duplicates: [{ id: 3, record_id: 5, matched_record_id: 2, kind: "duplicate", strength: "probable", score: 75, signals: {}, explanation: "Probable duplicate purchase invoice: same party and total.", status: "open", decision_note: null, record: null, matched_record: null, decided_at: null }],
  related: [],
};

describe("Transaction detail and review", () => {
  it("shows findings, explanation, duplicate warning, and blocks approval while errors exist", async () => {
    mockApi(signedIn([{ match: /\/records\/5$/, reply: RECORD }]));
    renderApp("/accounting/transactions/5");
    expect(await screen.findByText(/FinV identified a purchase-side purchase invoice/)).toBeInTheDocument();
    expect(screen.getAllByText(/Confirm the ledger for this purchase/).length).toBeGreaterThan(0);
    expect(screen.getByText(/GSTIN check digit is X/)).toBeInTheDocument();
    expect(screen.getByText(/Probable duplicate purchase invoice/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText("1 error · 1 to review")).toBeInTheDocument();
  });

  it("lets the accountant correct the lines and sends them for re-validation", async () => {
    const calls = mockApi(signedIn([
      { match: /\/records\/5$/, reply: RECORD },
      { match: /\/accounts$/, reply: [{ id: 21, code: "5100", name: "Purchases", type: "expense", role: "purchases", is_active: true },
        { id: 30, code: "5600", name: "Office Expenses", type: "expense", role: null, is_active: true },
        { id: 6, code: "1302", name: "Input IGST", type: "asset", role: "input_igst", is_active: true },
        { id: 11, code: "2100", name: "Trade Payables", type: "liability", role: "payables", is_active: true }] },
      { method: "PUT", match: /\/entries\/11$/, reply: {} },
    ]));
    renderApp("/accounting/transactions/5");
    await userEvent.click(await screen.findByRole("button", { name: "Correct entry" }));
    const ledger1 = await screen.findByLabelText("Ledger for line 1");
    await waitFor(() => expect(within(ledger1).getByRole("option", { name: "5600 · Office Expenses" })).toBeInTheDocument());
    await userEvent.selectOptions(ledger1, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save and re-check" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect((put.body as { lines: { account_id: number }[] }).lines[0].account_id).toBe(30);
  });

  it("requires acknowledgement and a note to approve with open review points", async () => {
    const rec = structuredClone(RECORD);
    rec.entries[0].validation = rec.entries[0].validation.filter((v) => v.status !== "error");
    const calls = mockApi(signedIn([{ match: /\/records\/5$/, reply: rec }, { method: "POST", match: /\/entries\/11\/approve$/, reply: {} }]));
    renderApp("/accounting/transactions/5");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve entry" });
    const confirm = within(dialog).getByRole("button", { name: "Approve" });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("checkbox"));
    await userEvent.type(within(dialog).getByRole("textbox"), "Office supplies confirmed with client");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/approve"))?.body).toEqual({ acknowledge_review: true, note: "Office supplies confirmed with client" }));
  });
});

describe("Duplicate review", () => {
  it("records a confirmed-duplicate decision with a note", async () => {
    const dup = { ...RECORD.duplicates[0], kind: "possible_alteration", strength: "exact",
      signals: { differences: [{ field: "total", new: "1298.00", existing: "1180.00" }], recommended_action: "Compare both copies." },
      record: { ...RECORD.record, id: 5 }, matched_record: { ...RECORD.record, id: 2 } };
    const calls = mockApi(signedIn([{ match: /\/duplicates\?status=open$/, reply: [dup] }, { method: "POST", match: /\/duplicates\/3\/decision$/, reply: {} }]));
    renderApp("/accounting/duplicates");
    expect(await screen.findByText("Possible alteration")).toBeInTheDocument();
    expect(screen.getByText(/Fields that differ: Total \(1298.00 vs 1180.00\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm duplicate" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("textbox"), "Mis-keyed copy");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "confirmed_duplicate", note: "Mis-keyed copy" }));
  });
});

describe("Audit exception review", () => {
  it("requires a comment to resolve and shows the review history", async () => {
    const ex = { id: 9, import_id: 1, object_type: "ledger_entry", object_id: 4, rule_id: "LED-001", severity: "medium", title: "Possible duplicate entries in Rent",
      explanation: "2 entries on 2025-06-05 for 60,000.00 share the same account, date and amount.", evidence: {}, suggested_action: "Vouch both entries.",
      status: "open", created_at: "2025-06-05T00:00:00", history: [], ledger_entry: null, tb_line: null };
    const calls = mockApi(signedIn([
      { match: /\/audit\/exceptions\/9$/, reply: ex },
      { method: "POST", match: /\/audit\/exceptions\/9$/, reply: { ...ex, status: "accepted_with_explanation", history: [{ id: 1, user_name: "Asha Owner", from_status: "open", to_status: "accepted_with_explanation", comment: "Two months paid together", created_at: "2025-06-06T00:00:00" }] } },
    ]));
    renderApp("/audit/exceptions/9");
    expect(await screen.findByText(/share the same account, date and amount/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Change status"), "accepted_with_explanation");
    expect(screen.getByText("Required: explain the resolution.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Comment/), "Two months paid together");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Two months paid together")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ status: "accepted_with_explanation", comment: "Two months paid together" });
  });
});

describe("Assistant chat", () => {
  const STATUS = { model: true, model_note: null, voice: true, model_name: "gemini-3.5-flash", runs_on_this_machine: false, service: "Google Gemini" };

  it("is open automatically on the Accounting overview", async () => {
    mockApi(signedIn([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }, { match: /assistant\/status$/, reply: STATUS },
      { match: /assistant\/messages\?workspace=accounting$/, reply: [] }]));
    renderApp("/accounting");
    expect(await screen.findByRole("complementary", { name: "FinV assistant" })).toBeInTheDocument();
  });

  it("is not shown on the firm dashboard", async () => {
    mockApi(signedIn([{ match: "/api/firm/overview", reply: FIRM }]));
    renderApp("/");
    await screen.findByRole("heading", { name: "Firm dashboard" });
    expect(screen.queryByRole("button", { name: "Open FinV assistant" })).not.toBeInTheDocument();
  });

  it("is available in the workspaces, sends a message, navigates and confirms a prepared action", async () => {
    const action = { id: 4, tool: "approve", summary: "Approve entry #12 (Rent, 47,200.00).", status: "pending", result: null, details: {} };
    const calls = mockApi(signedIn([
      { match: /accounting\/overview$/, reply: EMPTY_OVERVIEW },
      { match: /\/duplicates\?status=open$/, reply: [] },
      { match: /assistant\/status$/, reply: STATUS },
      { match: /assistant\/messages\?workspace=accounting$/, reply: [] },
      { method: "POST", match: /assistant\/chat\/stream$/, reply: (_u: string, init: RequestInit) => {
        const m = JSON.parse(String(init.body)).message as string;
        const message = m.startsWith("go")
          ? { id: 1, role: "assistant", content: "Opening duplicates.", meta: { navigate: "/accounting/duplicates", actions: [] } }
          : { id: 2, role: "assistant", content: "Approve entry #12? Press Confirm.", meta: { actions: [action] } };
        return { __ndjson: [{ type: "delta", text: "Work", reset: true }, { type: "delta", text: "ing", reset: false }, { type: "done", message }] };
      } },
      { method: "POST", match: /assistant\/actions\/4\/confirm$/, reply: { message: "Done. Entry #12 is approved.", action: { ...action, status: "done", result: "Done." } } },
    ]));
    renderApp("/accounting");
    const panel = await screen.findByRole("complementary", { name: "FinV assistant" });
    expect(await within(panel).findByText(/Google Gemini · gemini-3.5-flash · cloud/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Speak" })).toBeInTheDocument();
    await userEvent.type(within(panel).getByLabelText("Message"), "go to duplicates{Enter}");
    expect(await screen.findByRole("heading", { name: "Duplicates" })).toBeInTheDocument();   // navigated
    await userEvent.type(within(panel).getByLabelText("Message"), "approve entry 12{Enter}");
    await userEvent.click(await within(panel).findByRole("button", { name: "Confirm" }));
    expect(await within(panel).findByText("Done. Entry #12 is approved.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/assistant/actions/4/confirm"))).toBe(true);
  });

  it("asks for an explanation before confirming an approval with review points", async () => {
    const action = { id: 5, tool: "approve", summary: "Approve entry #9.", status: "pending", result: null,
      details: { needs_note: true, review_points: ["Place of supply is missing."] } };
    mockApi(signedIn([
      { match: /audit\/overview$/, reply: AUDIT_OVERVIEW },
      { match: /assistant\/status$/, reply: { ...STATUS, model: false, model_note: "AI is switched off for this company." } },
      { match: /assistant\/messages\?workspace=audit$/, reply: [{ id: 1, role: "assistant", content: "Prepared.", meta: { actions: [action] } }] },
    ]));
    renderApp("/audit");
    const panel = await screen.findByRole("complementary", { name: "FinV assistant" });
    expect(await within(panel).findByText("Built-in commands only")).toBeInTheDocument();
    expect(within(panel).getByText("Place of supply is missing.")).toBeInTheDocument();
    const confirm = within(panel).getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(panel).getByLabelText("Explanation"), "POS confirmed with client");
    expect(confirm).toBeEnabled();
  });
});
