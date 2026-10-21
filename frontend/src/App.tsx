import { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { StartNotice } from "./components/StartNotice";
import { WorkspaceLayout } from "./components/Shell";
import { useScrollRestoration } from "./lib/navigation";
import { Spinner } from "./components/ui";
import { Login, Register } from "./pages/Auth";
import { ClientWorkspace, Clients, Compliance, ErrorCentre, FirmDashboard, Imports } from "./pages/firm/Firm";
import { Settings } from "./pages/Settings";
import { AccountingOverview } from "./pages/accounting/Overview";
import { Transactions } from "./pages/accounting/Transactions";
import { RecordDetail } from "./pages/accounting/RecordDetail";
import { Bank, Duplicates, Entries } from "./pages/accounting/Lists";
import { Reports } from "./pages/accounting/Reports";
import { GstReturnDetail, GstReturns, GstReviewLink } from "./pages/accounting/GstReturns";
import { AuditOverview, AuditReports, ExceptionDetail, Exceptions, LedgerEntryDetail, LedgerReview, TrialBalance } from "./pages/audit/Audit";
import { useSession } from "./session";
import { servicesOf, type Service } from "./types";

function RequireAuth({ children, needCompany = false, service }: { children: ReactNode; needCompany?: boolean; service?: Service }) {
  const { me, loading, company } = useSession();
  const loc = useLocation();
  if (loading) return <div className="auth-wrap"><Spinner /></div>;
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  if (needCompany && !company) return <Navigate to="/" replace />;
  if (service && !servicesOf(company).includes(service)) return <Navigate to="/" replace />;   // not offered for this client
  return <>{children}</>;
}

const FIRM_NAV = [
  { to: "/", label: "Firm Dashboard", icon: "home", end: true },
  { to: "/clients", label: "Clients", icon: "list" },
  { to: "/compliance", label: "Compliance and Deadlines", icon: "flag" },
  { to: "/errors", label: "Error Center", icon: "alert" },
  { to: "/imports", label: "Integrations and Imports", icon: "upload" },
  { to: "/settings", label: "Settings", icon: "settings" },
];
const ACCOUNTING_NAV = [
  { to: "/accounting", label: "Overview", icon: "home", end: true },
  { to: "/accounting/transactions", label: "Transactions", icon: "list" },
  { to: "/accounting/entries", label: "Entries", icon: "book" },
  { to: "/accounting/bank", label: "Bank Reconciliation", icon: "bank" },
  { to: "/accounting/duplicates", label: "Duplicates", icon: "copy" },
  { to: "/accounting/gst", label: "GST Returns", icon: "flag" },
  { to: "/accounting/reports", label: "Reports", icon: "chart" },
];
const AUDIT_NAV = [
  { to: "/audit", label: "Overview", icon: "home", end: true },
  { to: "/audit/trial-balance", label: "Trial Balance", icon: "scale" },
  { to: "/audit/ledger", label: "Ledger Review", icon: "book" },
  { to: "/audit/vouching", label: "Vouching", icon: "search" },
  { to: "/audit/exceptions", label: "Exceptions", icon: "flag" },
  { to: "/audit/reports", label: "Audit Reports", icon: "chart" },
];

/** After signing in, return to the page that asked for it (e.g. a GST review link), only within FinV. */
function AfterLogin() {
  const from = (useLocation().state as { from?: string } | null)?.from;
  return <Navigate to={from && from.startsWith("/") && !from.startsWith("//") ? from : "/"} replace />;
}

export function App() {
  const { me, loading } = useSession();
  useScrollRestoration();
  return (
    <>
    <StartNotice />
    <Routes>
      <Route path="/login" element={!loading && me ? <AfterLogin /> : <Login />} />
      <Route path="/register" element={!loading && me ? <Navigate to="/" replace /> : <Register />} />
      <Route element={<RequireAuth><WorkspaceLayout name="Firm" items={FIRM_NAV} /></RequireAuth>}>
        <Route path="/" element={<FirmDashboard />} />
        <Route path="/clients" element={<Clients />} />
        <Route path="/clients/:id" element={<ClientWorkspace />} />
        <Route path="/compliance" element={<Compliance />} />
        <Route path="/errors" element={<ErrorCentre />} />
        <Route path="/imports" element={<Imports />} />
      </Route>
      <Route path="/settings" element={<RequireAuth needCompany><Settings /></RequireAuth>} />
      <Route path="/accounting" element={<RequireAuth needCompany service="accounting"><WorkspaceLayout name="Accounting" items={ACCOUNTING_NAV} /></RequireAuth>}>
        <Route index element={<AccountingOverview />} />
        <Route path="transactions" element={<Transactions />} />
        <Route path="transactions/:id" element={<RecordDetail />} />
        <Route path="entries" element={<Entries />} />
        <Route path="bank" element={<Bank />} />
        <Route path="duplicates" element={<Duplicates />} />
        <Route path="gst" element={<GstReturns />} />
        <Route path="gst/:id" element={<GstReturnDetail />} />
        <Route path="reports" element={<Reports />} />
      </Route>
      <Route path="/gst/review" element={<RequireAuth><GstReviewLink /></RequireAuth>} />
      <Route path="/audit" element={<RequireAuth needCompany service="audit"><WorkspaceLayout name="Audit" items={AUDIT_NAV} /></RequireAuth>}>
        <Route index element={<AuditOverview />} />
        <Route path="trial-balance" element={<TrialBalance />} />
        <Route path="trial-balance/:id" element={<TrialBalance />} />
        <Route path="ledger" element={<LedgerReview />} />
        <Route path="ledger/:id" element={<LedgerEntryDetail />} />
        <Route path="vouching" element={<LedgerReview vouchingOnly />} />
        <Route path="exceptions" element={<Exceptions />} />
        <Route path="exceptions/:id" element={<ExceptionDetail />} />
        <Route path="reports" element={<AuditReports />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </>
  );
}
