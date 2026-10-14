// Shared UI pieces used by the companion, conversation and workspace windows.
import { useEffect, useRef, useState } from "react";
import { currentClient, getClient } from "../api/client";
import { store, useStore, type TaskView } from "../api/store";
import { isTerminal } from "../shared/protocol";
import { PushToTalkRecorder } from "../voice/recorder";

export function useClient() {
  const [ready, setReady] = useState(!!currentClient());
  useEffect(() => {
    getClient().then(() => setReady(true));
  }, []);
  return ready ? currentClient() : null;
}

export function ConnectionPill({ compact }: { compact?: boolean }) {
  const conn = useStore((s) => s.connection);
  const llm = useStore((s) => s.llm);
  const devices = useStore((s) => s.devices.count);
  const cls = conn === "connected" ? "ok" : conn === "connecting" ? "warn" : "err";
  const text = conn === "connected" ? (compact ? "Mac" : "Mac backend") : conn === "connecting" ? "Connecting…" : "Backend offline";
  return (
    <span className="row" style={{ gap: 6, flexWrap: "wrap", minWidth: 0 }}>
      <span className="pill" role="status" aria-live="polite" title={`Backend: ${conn}`}>
        <span className={`dot ${cls}`} /> {text}
      </span>
      {conn === "connected" && (
        <span className="pill" title={llm?.message ?? "checking local model"}>
          <span className={`dot ${llm?.available ? "ok" : "warn"}`} /> {llm?.available ? llm.model : compact ? "No model" : "No local model"}
        </span>
      )}
      <span className="pill" title="Paired iPhones connected over the local network">
        <span className={`dot ${devices ? "ok" : ""}`} /> iPhone{compact ? "" : devices ? " connected" : " not connected"}
      </span>
    </span>
  );
}

const MicIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </svg>
);

export function useVoice() {
  const [level, setLevel] = useState(0);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<PushToTalkRecorder | null>(null);
  const sleeping = useStore((s) => s.sleeping);
  const estop = useStore((s) => s.estop.engaged);
  // Sleep and Emergency Stop end any capture in progress: the mic is released and the audio discarded.
  useEffect(() => {
    if ((sleeping || estop) && rec.current?.active) rec.current.cancel();
  }, [sleeping, estop]);

  const toggle = async () => {
    const client = currentClient();
    if (!client) return;
    if (rec.current?.active) {
      rec.current.stop();
      return;
    }
    setError(null);
    rec.current = new PushToTalkRecorder({
      onLevel: setLevel,
      onState: (s) => {
        setListening(s === "listening");
        client.sendMicState(s);
      },
    });
    try {
      const r = await rec.current.start();
      if (!r.speech) {
        client.sendMicState("idle");
        return;
      }
      client.sendMicState("transcribing");
      const res = await client.transcribe(r.wav, true);
      if (!res.ok) setError(res.error ?? "Transcription failed");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return { level, listening, error, toggle };
}

export function MicButton({ voice, disabled }: { voice: ReturnType<typeof useVoice>; disabled?: boolean }) {
  return (
    <button
      className={`mic no-drag ${voice.listening ? "on" : ""}`}
      onClick={voice.toggle}
      disabled={disabled}
      aria-pressed={voice.listening}
      aria-label={voice.listening ? "Stop listening" : "Push to talk"}
      title={voice.listening ? "Listening — click to stop" : "Push to talk (microphone is off until you click)"}
    >
      <span className="ring" style={{ opacity: voice.listening ? 0.25 + voice.level * 0.75 : 0, transform: `scale(${0.95 + voice.level * 0.25})` }} />
      <MicIcon />
    </button>
  );
}

export function ChatInput({ placeholder = "Ask BAYMAX…", disabled }: { placeholder?: string; disabled?: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    const t = text.trim();
    const client = currentClient();
    if (!t || !client) return;
    setBusy(true);
    setText("");
    try {
      await client.post("/api/chat", { text: t, speak: false });
    } catch (e) {
      store.dispatch({ type: "assistant.message", payload: { text: `Couldn't send: ${(e as Error).message}`, messageId: `err-${Date.now()}`, role: "system" }, seq: 0, ts: Date.now() / 1000 });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="row no-drag" style={{ flex: 1 }} onSubmit={(e) => { e.preventDefault(); send(); }}>
      <label className="sr-only" htmlFor="chat-input">Message</label>
      <input id="chat-input" className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder}
        disabled={disabled} autoComplete="off" maxLength={4000} />
      <button className="btn primary" type="submit" disabled={busy || !text.trim() || disabled}>Send</button>
    </form>
  );
}

export function EStopButton({ compact }: { compact?: boolean }) {
  const estop = useStore((s) => s.estop);
  const client = useClient();
  const toggle = () => client?.post("/api/estop", { engaged: !estop.engaged, reason: "Mac UI" });
  return (
    <button className={`btn no-drag ${estop.engaged ? "" : "danger"} ${compact ? "small" : ""}`} onClick={toggle}
      title={estop.engaged ? "Reset Emergency Stop" : "Emergency Stop: cancel and block all controlled actions"}>
      {estop.engaged ? "Reset E-Stop" : compact ? "STOP" : "Emergency Stop"}
    </button>
  );
}

export function useActiveTasks(): TaskView[] {
  const tasks = useStore((s) => s.tasks);
  return Object.values(tasks).filter((t) => !isTerminal(t.status)).sort((a, b) => b.updatedAt - a.updatedAt);
}

export function ProgressBar({ value }: { value?: number }) {
  const known = typeof value === "number";
  return (
    <span className={`progress ${known ? "" : "indeterminate"}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={known ? Math.round(value! * 100) : undefined} aria-valuetext={known ? undefined : "in progress, no measurable progress reported"}>
      <i style={known ? { width: `${Math.round(value! * 100)}%` } : undefined} />
    </span>
  );
}

export function ApprovalBanners() {
  const approvals = useStore((s) => Object.values(s.approvals));
  const client = useClient();
  if (!approvals.length) return null;
  return (
    <>
      {approvals.map((a) => (
        <div className="banner no-drag" key={a.approvalId} role="alertdialog" aria-label="Approval required">
          <div><strong>Approval needed</strong> <span className={`tag ${a.risk === "high" ? "err" : "warn"}`}>{a.risk} risk</span></div>
          <div className="mono">{a.summary}</div>
          <div className="row">
            <button className="btn primary small" onClick={() => client?.post(`/api/approvals/${a.approvalId}`, { approve: true })}>Approve</button>
            <button className="btn small" onClick={() => client?.post(`/api/approvals/${a.approvalId}`, { approve: false })}>Deny</button>
          </div>
        </div>
      ))}
    </>
  );
}

export function statusTag(status: string, verified?: boolean) {
  const cls = status === "COMPLETED" ? (verified ? "ok" : "warn") : status === "FAILED" ? "err" : status === "CANCELLED" ? "" : "warn";
  const label = status === "COMPLETED" ? (verified ? "verified" : "done, unverified") : status.toLowerCase().replace("_", " ");
  return <span className={`tag ${cls}`}>{label}</span>;
}
