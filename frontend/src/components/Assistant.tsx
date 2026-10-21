import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../session";
import { Badge, Icon } from "./ui";

interface Action { id: number; tool: string; summary: string; status: string; result: string | null; details: { review_points?: string[]; needs_note?: boolean } }
interface Msg { id: number | string; role: "user" | "assistant"; content: string; meta?: { navigate?: string | null; actions?: Action[]; steps?: { tool: string; label: string; ok: boolean }[]; used_model?: boolean; changed?: boolean; error?: boolean } }
interface Status { model: boolean; model_note: string | null; voice: boolean; model_name: string | null; runs_on_this_machine?: boolean; service?: string }

export const refreshData = () => window.dispatchEvent(new Event("finv:refresh"));

const SUGGEST: Record<string, string[]> = {
  accounting: ["What needs review?", "Show duplicates", "Go to bank reconciliation", "Help"],
  audit: ["Show open exceptions", "Go to trial balance", "Go to vouching", "Help"],
  client: ["What is due?", "Open bank reconciliation", "Prepare GSTR-1 for last month", "Reconcile the bank"],
};

/** Shown above the message box on every assistant. Wording is fixed. */
export const ASSISTANT_DISCLAIMER = "Ask anything. FinV will do the work for you. Filing, payments and approvals always need your confirmation.";

/** Chat assistant, available on every workspace page. */
export function Assistant({ workspace }: { workspace: "accounting" | "audit" | "client" }) {
  const { company } = useSession();
  const nav = useNavigate();
  const loc = useLocation();
  const onOverview = loc.pathname === "/accounting" || loc.pathname === "/audit" || /^\/clients\/\d+$/.test(loc.pathname);
  const [userOpen, setUserOpen] = useState(() => { try { return localStorage.getItem("finv.chat") === "open"; } catch { return false; } });
  // The assistant is open on the overview pages when there is room beside the page (it would cover the content on a tablet); elsewhere it remembers the user's choice.
  const narrow = () => typeof window !== "undefined" && window.innerWidth < 1100;
  const [closedOnOverview, setClosedOnOverview] = useState(narrow);
  useEffect(() => { setClosedOnOverview(narrow()); }, [loc.pathname]);
  const open = onOverview ? !closedOnOverview : userOpen;
  const setOpen = (v: boolean) => { if (onOverview) setClosedOnOverview(!v); setUserOpen(v); };
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [status, setStatus] = useState<Status | null>(null);
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [voiceMsg, setVoiceMsg] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const base = company ? `/api/companies/${company.id}/assistant` : "";

  useEffect(() => { try { localStorage.setItem("finv.chat", userOpen ? "open" : "closed"); } catch { /* ignore */ } }, [userOpen]);
  useEffect(() => {
    if (!open || !company) return;
    api<Status>(`${base}/status`).then(setStatus).catch(() => setStatus(null));
    api<Msg[]>(`${base}/messages?workspace=${workspace}`).then(setMsgs).catch(() => undefined);
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [open, company?.id, workspace]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const el = listRef.current; if (el) el.scrollTop = el.scrollHeight; }, [msgs, busy]);
  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  const afterReply = (m: Msg) => {
    if (m.meta?.navigate) nav(m.meta.navigate);
    if (m.meta?.changed) refreshData();
  };

  const send = async (raw: string) => {
    const message = raw.trim();
    if (!message || busy || !company) return;
    setUserOpen(true);   // once the user is chatting, keep the panel open as the assistant moves between pages
    setText("");
    setMsgs((xs) => [...xs, { id: `u${Date.now()}`, role: "user", content: message }]);
    setBusy(true);
    setStreaming(null);
    try {
      const reply = await streamChat(`${base}/chat/stream`, { message, workspace, page: loc.pathname, client_id: company.id },
        (textSoFar) => setStreaming(textSoFar), (st) => setStage(st));
      setMsgs((xs) => [...xs, reply]);
      afterReply(reply);
    } catch (e) {
      setMsgs((xs) => [...xs, { id: `e${Date.now()}`, role: "assistant", content: (e as Error).message, meta: { error: true } }]);
    } finally { setBusy(false); setStreaming(null); setStage(null); }
  };

  const decide = async (a: Action, confirm: boolean, note?: string) => {
    try {
      const r = await api<{ message?: string; action: Action }>(`${base}/actions/${a.id}/${confirm ? "confirm" : "cancel"}`, { body: confirm ? { note: note || null } : {} });
      setMsgs((xs) => xs.map((m) => ({ ...m, meta: m.meta && { ...m.meta, actions: m.meta.actions?.map((x) => (x.id === a.id ? r.action : x)) } })));
      if (confirm) { setMsgs((xs) => [...xs, { id: `c${Date.now()}`, role: "assistant", content: r.message ?? "Done." }]); refreshData(); }
    } catch (e) {
      setMsgs((xs) => [...xs, { id: `e${Date.now()}`, role: "assistant", content: (e as Error).message, meta: { error: true } }]);
    }
  };

  const startVoice = async () => {
    setVoiceMsg(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setVoiceMsg("This browser cannot record audio."); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => chunks.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRec(null);
        const form = new FormData();
        form.append("audio", new Blob(chunks, { type: r.mimeType || "audio/webm" }), "voice.webm");
        setVoiceMsg("Transcribing on this computer…");
        try {
          const res = await api<{ text: string }>(`${base}/transcribe`, { form });
          setVoiceMsg(null);
          if (res.text) send(res.text); else setVoiceMsg("I did not hear anything. Try again.");
        } catch (e) { setVoiceMsg((e as Error).message); }
      };
      r.start();
      setRec(r);
      setTimeout(() => { if (r.state === "recording") r.stop(); }, 60000);
    } catch { setVoiceMsg("Microphone access was blocked. Allow it in the browser to use voice."); }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } };

  if (!company) return null;
  if (!open) {
    return <button className="chat-fab btn primary" onClick={() => setOpen(true)} aria-label="Open FinV assistant"><Icon name="chat" />Ask FinV</button>;
  }
  return (
    <aside className="chat-panel" aria-label="FinV assistant">
      <header className="chat-head">
        <div><strong>FinV assistant</strong>
          <div className="faint" style={{ fontSize: 12 }}>{status ? (status.model ? `${status.service} · ${status.model_name} · cloud` : "Built-in commands only") : "…"}</div></div>
        <button className="btn ghost small" onClick={() => setOpen(false)} aria-label="Close assistant"><Icon name="x" /></button>
      </header>
      <div className="chat-list" ref={listRef} aria-live="polite">
        {msgs.length === 0 && (
          <div className="stack-sm">
            <p className="muted">Type or speak what you want to do. I prepare changes; nothing is approved, posted or changed until you press Confirm.</p>
            {status && !status.model && status.model_note && <div className="notice info" style={{ fontSize: 13 }}>{status.model_note}</div>}
            <div className="row">{SUGGEST[workspace].map((s) => <button key={s} className="btn small" onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}${m.meta?.error ? " error" : ""}`}>
            {m.meta?.steps && m.meta.steps.length > 0 && (
              <ul className="chat-steps" aria-label="What I did">
                {m.meta.steps.map((st) => <li key={st.tool} className={st.ok ? "ok" : "err"}><Icon name={st.ok ? "check" : "alert"} size={14} />{st.label}{st.ok || st.tool === "refused" ? "" : " (did not work)"}</li>)}
              </ul>)}
            <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
            {m.meta?.actions?.map((a) => <ActionCard key={a.id} a={a} onDecide={decide} />)}
          </div>
        ))}
        {busy && streaming && <div className="chat-msg assistant"><div style={{ whiteSpace: "pre-wrap" }}>{streaming}<span className="caret" aria-hidden="true">▍</span></div></div>}
        {busy && !streaming && <div className="chat-msg assistant"><span className="row muted"><span className="spinner" />{stage ?? "Thinking…"} {elapsed > 3 ? `${elapsed}s` : ""}</span></div>}
      </div>
      {voiceMsg && <div className="notice warn" style={{ margin: "0 12px", fontSize: 13 }}>{voiceMsg}</div>}
      <p className="chat-disclaimer">{ASSISTANT_DISCLAIMER}</p>
      <form className="chat-input" onSubmit={(e: FormEvent) => { e.preventDefault(); send(text); }}>
        <label className="sr-only" htmlFor="chat-text">Message</label>
        <textarea id="chat-text" ref={inputRef} rows={2} placeholder={rec ? "Listening… press stop when done" : "Ask or tell FinV what to do…"}
          value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={busy || !!rec} />
        <div className="stack-sm">
          {status?.voice && (rec
            ? <button type="button" className="btn danger small" onClick={() => rec.stop()} aria-label="Stop recording"><Icon name="stop" /></button>
            : <button type="button" className="btn small" onClick={startVoice} disabled={busy} aria-label="Speak"><Icon name="mic" /></button>)}
          <button className="btn primary small" disabled={busy || !text.trim()} aria-label="Send"><Icon name="arrow" /></button>
        </div>
      </form>
    </aside>
  );
}

function ActionCard({ a, onDecide }: { a: Action; onDecide: (a: Action, confirm: boolean, note?: string) => void }) {
  const [note, setNote] = useState("");
  const needsNote = !!a.details?.needs_note;
  return (
    <div className="chat-action">
      <div className="row-between"><strong style={{ fontSize: 13 }}>{a.status === "pending" ? "Waiting for your confirmation" : "Requested change"}</strong><Badge status={a.status === "done" ? "pass" : a.status === "pending" ? "needs_review" : "rejected"}>{a.status === "done" ? "Done" : a.status === "pending" ? "Pending" : a.status[0].toUpperCase() + a.status.slice(1)}</Badge></div>
      <p style={{ fontSize: 13 }}>{a.summary}</p>
      {a.details?.review_points && a.details.review_points.length > 0 && (
        <ul style={{ fontSize: 12, margin: "4px 0", paddingLeft: 18 }}>{a.details.review_points.map((p) => <li key={p}>{p}</li>)}</ul>)}
      {a.status === "pending" && (
        <div className="stack-sm">
          {needsNote && <input aria-label="Explanation" placeholder="Why are the review points acceptable?" value={note} onChange={(e) => setNote(e.target.value)} />}
          <div className="row">
            <button className="btn primary small" disabled={needsNote && !note.trim()} onClick={() => onDecide(a, true, note)}>Confirm</button>
            <button className="btn ghost small" onClick={() => onDecide(a, false)}>Cancel</button>
          </div>
        </div>
      )}
      {a.result && a.status !== "pending" && <p className="muted" style={{ fontSize: 12 }}>{a.result}</p>}
    </div>
  );
}

/** POST a chat message and read the newline-delimited JSON event stream until the final message. */
async function streamChat(url: string, body: unknown, onText: (t: string) => void, onStage: (s: string) => void): Promise<Msg> {
  const res = await fetch(url, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "finv" }, body: JSON.stringify(body) });
  if (!res.ok || !res.body) {
    let detail = "The assistant is not available right now.";
    try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", text = "", final: Msg | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.type === "delta") { text = ev.reset ? ev.text : text + ev.text; onText(text); }
      else if (ev.type === "status") onStage(ev.text);
      else if (ev.type === "done") final = ev.message as Msg;
    }
    if (done) break;
  }
  if (!final) throw new Error("The reply was interrupted. Try again.");
  return final;
}
