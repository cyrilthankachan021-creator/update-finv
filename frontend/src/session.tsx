import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { Company, Me } from "./types";

interface Session {
  me: Me | null;
  loading: boolean;
  companies: Company[];
  company: Company | null;
  selectCompany: (id: number | null) => void;
  refresh: () => Promise<void>;
  setMe: (m: Me | null) => void;
  logout: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);
const KEY = "finv.company";

function storedCompany(): number | null {
  try { const v = localStorage.getItem(KEY); return v ? Number(v) : null; } catch { return null; }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<number | null>(storedCompany());

  const refresh = useCallback(async () => {
    try {
      const m = await api<Me>("/api/auth/me");
      setMe(m);
      setCompanies(await api<Company[]>("/api/companies"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { setMe(null); setCompanies([]); }
      else throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh().catch(() => setLoading(false)); }, [refresh]);

  const selectCompany = (id: number | null) => {
    setCompanyId(id);
    try { if (id) localStorage.setItem(KEY, String(id)); else localStorage.removeItem(KEY); } catch { /* storage unavailable */ }
  };

  const logout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    setMe(null); setCompanies([]); selectCompany(null);
  };

  const company = companies.find((c) => c.id === companyId) ?? null;
  return (
    <Ctx.Provider value={{ me, loading, companies, company, selectCompany, refresh, setMe, logout }}>{children}</Ctx.Provider>
  );
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside provider");
  return s;
}

/** Fetch helper with loading/error/reload for pages. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void; setData: (d: T) => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const onRefresh = () => setTick((t) => t + 1);   // the assistant changed data: reload what is on screen
    window.addEventListener("finv:refresh", onRefresh);
    return () => window.removeEventListener("finv:refresh", onRefresh);
  }, []);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn().then((d) => { if (alive) setData(d); })
      .catch((e: Error) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

export const can = (role: string | null | undefined, perm: "prepare" | "approve" | "post" | "audit" | "admin"): boolean => {
  const m: Record<string, string[]> = {
    prepare: ["owner", "admin", "accountant"], approve: ["owner", "admin", "reviewer"], post: ["owner", "admin", "reviewer"],
    audit: ["owner", "admin", "auditor"], admin: ["owner", "admin"],
  };
  return !!role && m[perm].includes(role);
};
