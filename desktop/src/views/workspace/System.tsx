// Artifacts, audit log, memory, devices/pairing and settings.
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { currentClient } from "../../api/client";
import { useStore } from "../../api/store";
import type { Artifact } from "../../shared/protocol";
import { ArtifactLink, fmtTime, useApi } from "./shared";

export function Artifacts() {
  const [kind, setKind] = useState("");
  const list = useApi<{ artifacts: Artifact[] }>(`/api/artifacts${kind ? `?kind=${kind}` : ""}`);
  return (
    <>
      <h1>Artifacts</h1>
      <select className="input" style={{ maxWidth: 220 }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Filter by kind">
        <option value="">All kinds</option>
        {["model3d", "image", "document", "research", "simulation", "diagram", "code", "screenshot"].map((k) => <option key={k}>{k}</option>)}
      </select>
      <table style={{ marginTop: 10 }}>
        <thead><tr><th>Title</th><th>Kind</th><th>Validated</th><th>Path</th><th>Updated</th></tr></thead>
        <tbody>{list.data?.artifacts.map((a) => (
          <tr key={a.id}><td><ArtifactLink a={a} /></td><td>{a.kind}{a.meta.kind ? ` · ${a.meta.kind}` : ""}</td>
            <td><span className={`tag ${a.validated ? "ok" : "warn"}`}>{a.validated ? "yes" : "no"}</span></td>
            <td className="mono muted">{a.path}</td><td className="muted">{fmtTime(a.updatedAt)}</td></tr>
        ))}</tbody>
      </table>
    </>
  );
}

export function Logs() {
  const [n, setN] = useState(0);
  const logs = useApi<{ entries: any[]; chainValid: boolean }>("/api/audit?limit=300", [n]);
  return (
    <>
      <h1>Activity &amp; Audit Log</h1>
      <div className="row"><span className={`tag ${logs.data?.chainValid ? "ok" : "err"}`}>{logs.data?.chainValid ? "hash chain intact" : "chain check failed"}</span>
        <span className="spacer" /><button className="btn small" onClick={() => setN(n + 1)}>Refresh</button></div>
      <table style={{ marginTop: 10 }}>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Risk</th><th>Outcome</th><th>Detail</th></tr></thead>
        <tbody>{logs.data?.entries.map((e) => (
          <tr key={e.id}><td className="muted">{fmtTime(e.ts)}</td><td>{e.actor}</td><td className="mono">{e.action}</td><td>{e.risk}</td>
            <td><span className={`tag ${e.outcome === "ok" || e.outcome === "approved" ? "ok" : e.outcome === "failed" || e.outcome === "denied" ? "err" : ""}`}>{e.outcome}</span></td>
            <td className="mono muted">{e.detail.slice(0, 140)}</td></tr>
        ))}</tbody>
      </table>
    </>
  );
}

export function Memory() {
  const rev = useStore((s) => s.memoryRevision);
  const list = useApi<{ memories: { id: string; content: string; createdAt: number }[] }>("/api/memory", [rev]);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const c = currentClient();
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      list.reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <>
      <h1>Memory</h1>
      <p className="muted">BAYMAX only remembers what you explicitly ask it to. Secrets (passwords, keys, card numbers) are refused.</p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); act(() => c!.post("/api/memory", { content: text })).then(() => setText("")); }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="I prefer metric units" aria-label="New memory" />
        <button className="btn primary" disabled={!text.trim()}>Add</button>
      </form>
      {err && <div className="banner" role="alert">{err}</div>}
      <table style={{ marginTop: 10 }}><tbody>{list.data?.memories.map((m) => (
        <tr key={m.id}><td>
          <input className="input" defaultValue={m.content} aria-label="Edit memory"
            onBlur={(e) => e.target.value !== m.content && act(() => c!.patch(`/api/memory/${m.id}`, { content: e.target.value }))} /></td>
          <td className="muted">{fmtTime(m.createdAt)}</td>
          <td><button className="btn small" onClick={() => act(() => c!.del(`/api/memory/${m.id}`))}>Delete</button></td></tr>
      ))}</tbody></table>
      {!!list.data?.memories.length && <button className="btn small" style={{ marginTop: 10 }} onClick={() => confirm("Delete all memories?") && act(() => c!.del("/api/memory"))}>Delete all</button>}
    </>
  );
}

export function Devices() {
  const devices = useApi<{ paired: any[]; connected: any[]; pairingActive: boolean }>("/api/devices", [useStore((s) => s.devices.count)]);
  const [pairing, setPairing] = useState<{ code: string; qr: string; expiresAt: number; addresses: string[]; port: number; fingerprint: string } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const c = currentClient();
  useEffect(() => {
    if (pairing && canvas.current) QRCode.toCanvas(canvas.current, pairing.qr, { width: 220, margin: 1, color: { dark: "#180509", light: "#F5F5F2" } });
  }, [pairing]);
  const start = async () => {
    setErr(null);
    try {
      setPairing(await c!.post("/api/pairing/start"));
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <>
      <h1>iPhone Pairing</h1>
      <div className="split">
        <div className="card">
          <p>Pairing turns on the TLS listener on your local network and shows a one-time code (valid 5 minutes, 5 attempts). Scan the QR code with the BAYMAX iPhone app, or type the code.</p>
          <button className="btn primary" onClick={start}>Pair iPhone</button>
          {err && <div className="banner" role="alert">{err}</div>}
          {pairing && (
            <div style={{ marginTop: 12 }}>
              <canvas ref={canvas} aria-label="Pairing QR code" />
              <p style={{ fontSize: 28, letterSpacing: 6, fontFamily: "ui-monospace, monospace" }} aria-label="Pairing code">{pairing.code}</p>
              <p className="muted mono">Mac: {pairing.addresses.join(", ") || "no LAN address found"} : {pairing.port}<br />TLS fingerprint: {pairing.fingerprint.slice(0, 32)}…<br />
                Expires {new Date(pairing.expiresAt * 1000).toLocaleTimeString()}</p>
              <button className="btn small" onClick={() => c!.post("/api/pairing/cancel").then(() => setPairing(null))}>Cancel pairing</button>
            </div>
          )}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Paired devices</h2>
          <table><tbody>{devices.data?.paired.map((d) => (
            <tr key={d.id}><td>{d.name}<div className="muted mono">{d.id}</div></td>
              <td>{d.revoked ? <span className="tag">revoked</span> : devices.data?.connected.some((x) => x.deviceId === d.id) ? <span className="tag ok">connected</span> : <span className="tag">offline</span>}</td>
              <td>{!d.revoked && <button className="btn small" onClick={() => c!.del(`/api/devices/${d.id}`).then(devices.reload)}>Revoke</button>}</td></tr>
          ))}</tbody></table>
          {!devices.data?.paired.length && <p className="muted">No devices paired yet.</p>}
        </div>
      </div>
      <CursorPresenceDebug />
    </>
  );
}

/** Live view of the real cursor relay (backend /api/status → cursor): what the paired iPhone is being sent. */
function CursorPresenceDebug() {
  const [st, setSt] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const c = currentClient();
    if (!c) return;
    let alive = true;
    const tick = () => c.get<any>("/api/status").then((d) => alive && (setSt(d.cursor), setErr(null))).catch((e) => alive && setErr(e.message));
    tick();
    const t = setInterval(tick, 500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  const last = st?.last;
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>Cursor presence (debug)</h2>
      {err && <div className="banner" role="alert">{err}</div>}
      {!st ? <p className="muted">Waiting for status…</p> : (
        <p className="mono" style={{ lineHeight: 1.7 }}>
          relay: {st.sleeping ? "paused (sleeping)" : "active"} · received {st.received} · sent to iPhone {st.published} · rejected {st.rejected}<br />
          last sample: {last ? `display #${last.displayId} ${last.displayWidth}×${last.displayHeight} @${last.scale}x · x ${last.x} y ${last.y} · vx ${last.vx} vy ${last.vy} · seq ${last.seq}` : "none yet (streams only while an iPhone is connected and BAYMAX is awake)"}<br />
          last event: {st.lastAgeSeconds == null ? "—" : `${st.lastAgeSeconds}s ago`}
        </p>
      )}
    </div>
  );
}

export function Settings() {
  const cfg = useApi<{ config: any }>("/api/config");
  const status = useApi<any>("/api/status");
  const models = useApi<{ status: any; recommended: any[] }>("/api/models");
  const [msg, setMsg] = useState<string | null>(null);
  const c = currentClient();
  const set = async (key: string, value: unknown) => {
    try {
      await c!.patch("/api/config", { [key]: value });
      setMsg(`Saved ${key}`);
      cfg.reload();
      status.reload();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  const conf = cfg.data?.config;
  const s = status.data;
  if (!conf || !s) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Settings &amp; Permissions</h1>
      {msg && <div className="banner">{msg}</div>}
      <div className="grid">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Local language model (Ollama)</h2>
          <p><span className={`tag ${s.llm.available ? "ok" : "warn"}`}>{s.llm.available ? "ready" : "unavailable"}</span> {s.llm.message}</p>
          <label className="field">Model
            <select className="input" value={conf.llm.model} onChange={(e) => set("llm.model", e.target.value)}>
              {[...new Set([conf.llm.model, ...(models.data?.status.installedModels ?? []), ...(models.data?.recommended.map((m) => m.name) ?? [])])].map((m) => <option key={m}>{m}</option>)}
            </select></label>
          <table style={{ marginTop: 8 }}><tbody>{models.data?.recommended.map((m) => <tr key={m.name}><td className="mono">{m.name}</td><td>~{m.approxMemoryGB} GB</td><td className="muted">{m.role}</td></tr>)}</tbody></table>
          <p className="muted" style={{ fontSize: 12 }}>No cloud model is ever used as a fallback.</p>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Voice</h2>
          <p>Speech recognition: <span className={`tag ${s.stt.available ? "ok" : "warn"}`}>{s.stt.available ? "whisper.cpp ready" : "unavailable"}</span></p>
          <p className="muted">{s.stt.message}</p>
          <p>Speech output: <span className={`tag ${s.tts.available ? "ok" : "warn"}`}>{s.tts.engine}</span> <span className="muted">{s.tts.message}</span></p>
          <label className="field">Voice (macOS “say” voice name, empty = system default)
            <input className="input" defaultValue={conf.voice.tts_voice} onBlur={(e) => set("voice.tts_voice", e.target.value)} /></label>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Network &amp; research</h2>
          <label className="row"><input type="checkbox" checked={conf.research.local_only} onChange={(e) => set("research.local_only", e.target.checked)} /> Local-only mode (disable web research)</label>
          <p className="muted">LAN listener: {s.lan.enabled ? `on, port ${s.lan.port}` : "off (starts when you pair a phone)"}</p>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Permissions &amp; safety</h2>
          <label className="row"><input type="checkbox" checked={conf.security.allow_desktop_control} onChange={(e) => set("security.allow_desktop_control", e.target.checked)} /> Allow desktop control (apps, mouse, keyboard)</label>
          <label className="row"><input type="checkbox" checked={conf.security.remote_high_risk_approval} onChange={(e) => set("security.remote_high_risk_approval", e.target.checked)} /> Allow iPhone to approve high-risk actions</label>
          <p className="muted">macOS Accessibility: {s.desktop.accessibilityTrusted === true ? "granted" : s.desktop.accessibilityTrusted === false ? "NOT granted" : s.desktop.available ? "unknown until first use" : `unavailable (${s.desktop.platform})`}</p>
          <p className="muted">Hardware control: {s.hardware.enabled ? "enabled" : "disabled"} · Workspace: <span className="mono">{s.workspace}</span></p>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Image generation</h2>
          <p className="muted">{s.images.message}</p>
          <label className="field">Provider
            <select className="input" value={conf.images.provider} onChange={(e) => set("images.provider", e.target.value)}>
              <option value="none">none</option><option value="automatic1111">automatic1111-compatible (local)</option>
            </select></label>
          <label className="field">Server URL<input className="input" defaultValue={conf.images.base_url} placeholder="http://127.0.0.1:7860" onBlur={(e) => set("images.base_url", e.target.value)} /></label>
        </div>
      </div>
    </>
  );
}
