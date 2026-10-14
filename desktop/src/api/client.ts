// REST + WebSocket client for the local BAYMAX backend (127.0.0.1 only).
import type { BusEvent } from "../shared/protocol";
import { store, type Store } from "./store";

export interface BackendConfig { apiBase: string; token: string }

declare global {
  interface Window {
    baymax?: {
      config: () => Promise<BackendConfig>;
      openWindow: (name: "companion" | "conversation" | "workspace") => void;
      onCursor: (cb: (p: { x: number; y: number }) => void) => () => void;
      onGaze?: (cb: (g: { x: number; y: number; label: string; durationMs: number }) => void) => () => void;
      requestMicrophone: () => Promise<boolean>;
      platform: string;
    };
  }
}

export async function loadConfig(): Promise<BackendConfig> {
  if (window.baymax) return window.baymax.config();
  // Browser fallback for development: ?api=http://127.0.0.1:8765&token=...
  const q = new URLSearchParams(location.search);
  return { apiBase: q.get("api") ?? "http://127.0.0.1:8765", token: q.get("token") ?? "" };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export class BaymaxClient {
  private ws: WebSocket | null = null;
  private backoff = 500;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(public cfg: BackendConfig, private store: Store, private WS: typeof WebSocket = WebSocket) {}

  async request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(this.cfg.apiBase + path, {
      method,
      headers: { Authorization: `Bearer ${this.cfg.token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    });
    const text = await res.text();
    let data: any = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: text };
    }
    if (!res.ok) throw new ApiError(res.status, data.error ?? data.message ?? `HTTP ${res.status}`);
    return data as T;
  }

  get = <T = any>(path: string) => this.request<T>("GET", path);
  post = <T = any>(path: string, body: unknown = {}) => this.request<T>("POST", path, body);
  patch = <T = any>(path: string, body: unknown) => this.request<T>("PATCH", path, body);
  del = <T = any>(path: string) => this.request<T>("DELETE", path);

  fileUrl(artifactId: string): string {
    return `${this.cfg.apiBase}/api/artifacts/${artifactId}/file?token=${encodeURIComponent(this.cfg.token)}`;
  }

  async transcribe(wav: Blob, submit = true): Promise<any> {
    const res = await fetch(`${this.cfg.apiBase}/api/voice/transcribe?submit=${submit ? 1 : 0}`, {
      method: "POST", headers: { Authorization: `Bearer ${this.cfg.token}`, "Content-Type": "audio/wav" }, body: wav,
    });
    return res.json();
  }

  connect(): void {
    this.stopped = false;
    this.store.set({ connection: "connecting" });
    const url = this.cfg.apiBase.replace(/^http/, "ws") + "/api/ws";
    const ws = new this.WS(url);
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: this.cfg.token }));
    ws.onmessage = (m) => {
      let data: any;
      try {
        data = JSON.parse(typeof m.data === "string" ? m.data : "");
      } catch {
        return; // ignore malformed frames
      }
      if (data?.type === "hello") {
        this.backoff = 500;
        const p = data.payload ?? {};
        this.store.set((s) => ({ ...s, connection: "connected", character: p.character?.state ?? s.character, sleeping: !!p.sleeping,
          estop: { engaged: !!p.estop, reason: s.estop.reason } }));
        for (const ev of (p.recent ?? []) as BusEvent[]) if (ev.seq > this.store.get().lastSeq) this.store.dispatch(ev);
        this.refreshSnapshot().catch(() => undefined);
        return;
      }
      if (data && typeof data.type === "string" && data.payload && typeof data.payload === "object") this.store.dispatch(data as BusEvent);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.store.set({ connection: "disconnected" });
      if (!this.stopped) this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, 8000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  nextBackoff(): number {
    return this.backoff;
  }

  close(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }

  sendMicState(state: "idle" | "listening" | "transcribing" | "unavailable"): void {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ type: "mic.state", state }));
  }

  async refreshSnapshot(): Promise<void> {
    const [status, history, approvals] = await Promise.all([
      this.get("/api/status"), this.get("/api/history?limit=100"), this.get("/api/approvals"),
    ]);
    this.store.set((s) => ({
      ...s,
      llm: { available: status.llm.available, message: status.llm.message, model: status.llm.model },
      sleeping: status.sleeping,
      estop: status.estop,
      devices: { count: status.devices.connected.length, list: status.devices.connected },
      messages: history.messages.map((m: any) => ({ ...m, ts: m.ts })),
      approvals: Object.fromEntries(approvals.approvals.map((a: any) => [a.approvalId, a])),
    }));
  }
}

let client: BaymaxClient | null = null;

export async function getClient(): Promise<BaymaxClient> {
  if (!client) {
    client = new BaymaxClient(await loadConfig(), store);
    client.connect();
  }
  return client;
}

export function currentClient(): BaymaxClient | null {
  return client;
}
