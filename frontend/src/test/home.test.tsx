import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { COMPANY, ME, mockApi, renderApp } from "./helpers";

const NO_TDS = { applicable: false, tan: null, gst_filing: "monthly", payable: "0" };
const EMPTY_OVERVIEW = { pending_review: 0, validated: 0, approved: 0, posted: 0, rejected: 0, issues_found: 0, possible_duplicates: 0, recent_documents: [], upload: { max_mb: 20, extensions: [] } };
const BOOKS_ONLY = { ...COMPANY, id: 8, name: "Books Only Pvt Ltd", services: ["accounting"] };
const AUDIT_ONLY = { ...COMPANY, id: 9, name: "Audit Client Ltd", services: ["audit"] };
const ALL = [COMPANY, BOOKS_ONLY, AUDIT_ONLY];

const task = (o: object) => ({ key: "t", client_id: 7, client_name: COMPANY.name, gstin: "27AAACF1234K1ZX", category: "gst", task: "GSTR-3B return", period: "2026-09", financial_year: "2026-27",
  due_date: "2026-10-20", status: "not_started", urgency: "overdue", severity: "high", issue: "Not started", next_action: "Prepare the return.", link: "/accounting/gst", ...o });
const issue = (o: object) => ({ key: "i", client_id: 7, client_name: COMPANY.name, category: "audit_exception", severity: "high", title: "Trial balance does not balance", financial_year: null,
  description: "Debits differ from credits by 500.00.", detection: "Audit rule TB-001", nature: "rule_detected", review_status: "open", next_action: "Investigate.", link: "/audit/exceptions/3", created_at: null, ...o });
const row = (c: { id: number; name: string }, o: object = {}) => ({ ...c, gstin: null, state_code: "27", currency: "INR", organization_id: 1, role: "owner", services: ["accounting", "audit"],
  overdue: 0, due_soon: 0, pending_tasks: 0, awaiting_review: 0, issues: 0, high_issues: 0, missing_data: false, financial_year: "2026-27", work_status: "on_track", last_import_at: null, ...o });
const paged = (items: unknown[], o: object = {}) => ({ items, total: items.length, page: 1, page_size: 25, ...o });

const OVERVIEW = {
  as_of: "2026-10-21", financial_year: "2026-27",
  summary: { clients: 3, gst_pending: 2, gst_overdue: 1, awaiting_review: 1, due_soon: 0, issues_open: 1, issues_high: 1, audit_findings: 1, missing_data: 0 },
  attention: [task({})], top_issues: [issue({})], not_tracked: [{ area: "TDS payments and returns", reason: "No TDS records yet." }],
};
const TASKS = paged([task({}), task({ key: "u", client_id: 8, client_name: BOOKS_ONLY.name, status: "awaiting_review", urgency: "upcoming", severity: "low", issue: null })],
  { facets: { financial_years: ["2026-27"], categories: ["gst"] } });

function setup(extra: Parameters<typeof mockApi>[0] = []) {
  return mockApi([...extra, { match: "/api/auth/me", reply: ME }, { match: "/api/companies", reply: ALL },
    { match: "/api/firm/overview", reply: OVERVIEW }, { match: /^\/api\/firm\/tasks/, reply: TASKS }]);
}

describe("Firm dashboard", () => {
  it("shows totals and findings computed by the server, not invented in the page", async () => {
    setup();
    renderApp("/", { company: false });
    const overdue = await screen.findByRole("button", { name: /GST returns overdue/ });
    expect(within(overdue).getByText("1")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /GST returns pending/ })).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Trial balance does not balance")).toBeInTheDocument();
    expect(screen.getByText("Rule-detected")).toBeInTheDocument();          // a rule result is not presented as a confirmed error
    expect(screen.getByText(/TDS payments and returns:/)).toBeInTheDocument();   // and gaps in coverage are stated
  });

  it("names the client on every alert and opens the finding in that client's workspace", async () => {
    setup([{ match: /audit\/exceptions\/3$/, reply: { id: 3, history: [], title: "Trial balance does not balance", severity: "high", status: "open", explanation: "", evidence: {}, rule_id: "TB-001" } },
      { match: /audit\/overview$/, reply: {} }]);
    renderApp("/", { company: false });
    const finding = await screen.findByText("Trial balance does not balance");
    expect(within(finding.closest("tr")!).getByText(COMPANY.name)).toBeInTheDocument();
    await userEvent.click(finding);
    await waitFor(() => expect(localStorage.getItem("finv.company")).toBe(String(COMPANY.id)));
    expect(await screen.findByRole("region", { name: "Selected client" })).toHaveTextContent(COMPANY.name);   // banner says whose records these are
  });

  it("asks the server to filter and page the compliance list", async () => {
    const calls = setup();
    renderApp("/compliance", { company: false });
    await screen.findByRole("table");
    await userEvent.selectOptions(screen.getByLabelText("Filter tasks"), "overdue");
    await userEvent.selectOptions(screen.getByLabelText("Filter by financial year"), "2026-27");
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=overdue") && c.url.includes("financial_year=2026-27"))).toBe(true));
  });

  it("groups tasks under their client", async () => {
    setup();
    renderApp("/compliance", { company: false });
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("checkbox", { name: /Group by client/ }));
    const headers = screen.getAllByRole("columnheader").filter((h) => h.getAttribute("colspan") === "7");
    expect(headers.map((h) => h.textContent)).toEqual([BOOKS_ONLY.name, COMPANY.name].sort((a, b) => a.localeCompare(b)));
  });

  it("lists clients one page at a time with status, FY and last import, and searches on the server", async () => {
    const calls = setup([{ match: /^\/api\/firm\/clients\?/, reply: paged([row(COMPANY, { overdue: 2, work_status: "overdue", pending_tasks: 3, last_import_at: "2026-10-01T10:00:00" }), row(BOOKS_ONLY)], { total: 60 }) }]);
    renderApp("/clients", { company: false });
    const table = await screen.findByRole("table");
    expect(within(within(table).getByText(COMPANY.name).closest("tr")!).getByText("Overdue")).toBeInTheDocument();
    expect(within(table).getAllByText("2026-27").length).toBe(2);
    expect(within(table).getByText("Never")).toBeInTheDocument();
    expect(screen.getByText("1–25 of 60")).toBeInTheDocument();             // pagination is over the server's total, not the loaded rows
    await userEvent.type(screen.getByLabelText("Search clients"), "Books");
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=Books"))).toBe(true));
  });

  it("opens a client's own workspace with its modules and keeps that client selected", async () => {
    setup([{ match: "/api/firm/clients/7", reply: { client: row(COMPANY, { gstin: "27AAACF1234K1ZX" }), tasks: [task({})], issues: [], tds: NO_TDS } }]);
    renderApp("/clients/7", { company: false });
    expect(await screen.findByRole("heading", { name: COMPANY.name })).toBeInTheDocument();
    const mods = screen.getByRole("navigation", { name: "Client modules" });
    for (const l of ["Vouching", "Ledger", "Trial Balance", "Audit Report", "GST", "Bank Reconciliation"]) expect(within(mods).getByText(l)).toBeInTheDocument();
    expect(within(mods).getByText("Not set up")).toBeInTheDocument();             // TDS is not pretended: it says it is not set up
    await waitFor(() => expect(localStorage.getItem("finv.company")).toBe("7"));
  });

  it("has the assistant open on the client page: fixed green disclaimer, client_id on every message, visible steps", async () => {
    const calls = setup([
      { match: "/api/firm/clients/7", reply: { client: row(COMPANY), tasks: [], issues: [], tds: NO_TDS } },
      { match: /assistant\/status$/, reply: { model: false, model_note: null, voice: false, model_name: null } },
      { match: /assistant\/messages\?workspace=client$/, reply: [] },
      { method: "POST", match: /assistant\/chat\/stream$/, reply: { __ndjson: [{ type: "done", message: { id: 1, role: "assistant", content: "Opening bank.",
        meta: { navigate: null, steps: [{ tool: "open_page", label: "Opened a page", ok: true }], actions: [] } } }] } },
    ]);
    renderApp("/clients/7", { company: false });
    const panel = await screen.findByRole("complementary", { name: "FinV assistant" });
    const note = within(panel).getByText("Ask anything. FinV will do the work for you. Filing, payments and approvals always need your confirmation.");
    expect(note).toHaveClass("chat-disclaimer");
    await userEvent.type(within(panel).getByLabelText("Message"), "open bank reconciliation");
    await userEvent.click(within(panel).getByRole("button", { name: "Send" }));
    expect(await within(panel).findByText("Opened a page")).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/chat/stream"))!;
    expect(sent.url).toBe("/api/companies/7/assistant/chat/stream");
    expect(sent.body).toMatchObject({ workspace: "client", client_id: 7, message: "open bank reconciliation" });
  });

  it("does not open a client the user has no access to", async () => {
    setup();
    renderApp("/clients/999", { company: false });
    expect(await screen.findByText("Client not found")).toBeInTheDocument();
  });

  it("adds a client from the dashboard for accounting only", async () => {
    const calls = setup([{ method: "POST", match: "/api/companies", reply: BOOKS_ONLY }, { match: /^\/api\/firm\/clients\?/, reply: paged([row(COMPANY)]) }]);
    renderApp("/clients", { company: false });
    await userEvent.click((await screen.findAllByRole("button", { name: "Add new client" }))[0]);
    await userEvent.type(screen.getByLabelText(/Company name/), "Books Only Pvt Ltd");
    await userEvent.click(screen.getByRole("checkbox", { name: /CA/ }));
    expect(screen.getByRole("checkbox", { name: /Company/ })).toBeDisabled();     // at least one service stays on
    await userEvent.click(screen.getByRole("button", { name: "Create company" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/companies")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as { services: string[] }).services).toEqual(["accounting"]);
  });

  it("switching client in the top bar goes to that client's workspace, not a stale page of the previous client", async () => {
    setup([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }, { match: "/api/firm/clients/8", reply: { client: row(BOOKS_ONLY), tasks: [], issues: [], tds: NO_TDS } }]);
    renderApp("/accounting");
    await screen.findByText("Upload financial documents");
    await userEvent.selectOptions(screen.getByLabelText("Client"), String(BOOKS_ONLY.id));
    expect(await screen.findByRole("heading", { name: BOOKS_ONLY.name })).toBeInTheDocument();
    expect(localStorage.getItem("finv.company")).toBe("8");
  });

  it("keeps people out of a workspace the firm does not offer for that client", async () => {
    setup([{ match: /accounting\/overview$/, reply: EMPTY_OVERVIEW }]);
    localStorage.setItem("finv.company", String(AUDIT_ONLY.id));
    renderApp("/accounting", { company: false });
    expect(await screen.findByRole("heading", { name: "Firm dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Accounting navigation" })).not.toBeInTheDocument();
  });
});
