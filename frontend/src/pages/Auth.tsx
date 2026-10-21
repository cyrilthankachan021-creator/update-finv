import { FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ErrorNotice } from "../components/ui";
import { useSession } from "../session";
import type { Me } from "../types";

export function Login() {
  const { refresh } = useSession();
  const nav = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from;
  const back = from && from.startsWith("/") && !from.startsWith("//") ? from : "/";   // only pages inside FinV
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await api<Me>("/api/auth/login", { body: { email, password } }); await refresh(); nav(back, { replace: true }); }
    catch (ex) { setErr((ex as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="auth-wrap">
      <form className="card auth-card stack" onSubmit={submit}>
        <div className="wordmark"><span className="dot" />FinV</div>
        <div><h1>Sign in</h1><p className="muted">Accounting correctness and audit intelligence.</p></div>
        <ErrorNotice error={err} />
        <label className="field">Email<input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field">Password<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted">New to FinV? <Link to="/register">Create an account</Link></p>
      </form>
    </div>
  );
}

export function Register() {
  const { refresh } = useSession();
  const nav = useNavigate();
  const [f, setF] = useState({ name: "", email: "", password: "", organization_name: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (f.password.length < 10) { setErr("Use a password of at least 10 characters."); return; }
    setBusy(true); setErr(null);
    try { await api("/api/auth/register", { body: f }); await refresh(); nav("/", { replace: true }); }
    catch (ex) { setErr((ex as Error).message); }
    finally { setBusy(false); }
  };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="auth-wrap">
      <form className="card auth-card stack" onSubmit={submit}>
        <div className="wordmark"><span className="dot" />FinV</div>
        <div><h1>Create your account</h1><p className="muted">You will be the owner of your firm's FinV workspace.</p></div>
        <ErrorNotice error={err} />
        <label className="field">Your name<input required value={f.name} onChange={set("name")} autoComplete="name" /></label>
        <label className="field">Firm or organisation name<input required value={f.organization_name} onChange={set("organization_name")} /></label>
        <label className="field">Email<input type="email" required value={f.email} onChange={set("email")} autoComplete="username" /></label>
        <label className="field">Password<span className="hint">At least 10 characters.</span>
          <input type="password" required minLength={10} value={f.password} onChange={set("password")} autoComplete="new-password" /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
        <p className="muted">Already have an account? <Link to="/login">Sign in</Link></p>
      </form>
    </div>
  );
}
