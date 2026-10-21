import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Help, SeverityChip } from "../components/ui";

describe("Severity chip and help tip", () => {
  it("names the severity in words, not only in colour", () => {
    render(<><SeverityChip severity="high" /><SeverityChip severity="medium" /><SeverityChip severity="low" /></>);
    for (const w of ["High", "Medium", "Low"]) expect(screen.getByText(w).closest(".chip")).toBeInTheDocument();
    expect(screen.getByText("High").closest(".chip")).toHaveClass("sev-high");
  });

  it("gives the explanation to screen readers and keyboard users", async () => {
    render(<Help text="Plain words." />);
    const btn = screen.getByRole("button", { name: /what does this mean\? plain words\./i });
    await userEvent.tab();
    expect(btn).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("Plain words.");
  });
});

describe("Bank statement check", () => {
  it("shows the period, the rejected rows with their reasons, and each check", async () => {
    const { BankCheck } = await import("../components/BankCheck");
    render(<BankCheck v={{ status: "needs_attention", period_start: "2025-06-01", period_end: "2025-06-30", lines_accepted: 3, lines_rejected: 1,
      rejected: [{ row: 4, message: "Bank line has no date." }], total_debits: "2500.00", total_credits: "1500.00", first_balance: "11000.00", last_balance: "9000.00",
      balance_breaks: [], repeated_lines: 0, checks: [{ check: "Dates found", ok: true, detail: "ok" }, { check: "Running balance agrees", ok: false, detail: "1 line does not agree." }] }} />);
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText(/3 lines accepted, 1 rejected/)).toBeInTheDocument();
    expect(screen.getByText("Row 4: Bank line has no date.")).toBeInTheDocument();
    expect(screen.getByText(/Running balance agrees\./)).toBeInTheDocument();
  });
});
