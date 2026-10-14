// Main-process presence: a persistent authenticated link to the backend that
// (1) streams the real cursor position + display geometry while a phone is connected and BAYMAX is awake,
// (2) receives sleep/devices/gaze events so the Mac companion can look/point at screen targets.
import { screen } from "electron";
import { buildCursorMessage, CursorGate, type DisplayInfo, type Point } from "./presenceLogic";

export interface PresenceEvents {
  onGazeTarget?: (target: { x: number; y: number; label: string; kind: string; durationMs: number }) => void;
  onSleep?: (sleeping: boolean) => void;
  log?: (m: string) => void;
}

const toInfo = (d: Electron.Display): DisplayInfo => ({ id: d.id, bounds: d.bounds, scaleFactor: d.scaleFactor });

export class PresenceLink {
  private ws: WebSocket | null = null;
  private timer: NodeJS.Timeout | null = null;
  private retry: NodeJS.Timeout | null = null;
  private backoff = 500;
  private stopped = false;
  readonly gate = new CursorGate(Number(process.env.BAYMAX_CURSOR_HZ ?? 30));
  sent = 0;

  constructor(private apiBase: string, private token: string, private ev: PresenceEvents = {}) {
    if (process.env.BAYMAX_CURSOR_ALWAYS === "1") this.gate.listeners = 1; // diagnostics: stream even without a phone
  }

  start(): void {
    this.stopped = false;
    this.connect();
    // Sampling at 60 Hz is cheap (one native call); the gate limits what is sent to ~30 Hz and only on movement.
    this.timer = setInterval(() => this.sample(), 16);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    if (this.retry) clearTimeout(this.retry);
    this.ws?.close();
  }

  private sample(): void {
    if (!this.gate.active || this.ws?.readyState !== WebSocket.OPEN) return;
    const p: Point = screen.getCursorScreenPoint();
    const display = screen.getDisplayNearestPoint(p);
    if (!this.gate.offer(p, display.id, Date.now())) return;
    const msg = buildCursorMessage(p, toInfo(display), toInfo(screen.getPrimaryDisplay()), Date.now());
    this.ws.send(JSON.stringify(msg));
    this.sent++;
  }

  private connect(): void {
    if (this.stopped) return;
    const ws = new WebSocket(this.apiBase.replace(/^http/, "ws") + "/api/ws");
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: this.token }));
    ws.onmessage = (m) => {
      let data: any;
      try {
        data = JSON.parse(String(m.data));
      } catch {
        return;
      }
      const p = data?.payload ?? {};
      switch (data?.type) {
        case "hello":
          this.backoff = 500;
          this.setSleeping(!!p.sleeping);
          this.setListeners(Number(p.devices ?? 0));
          break;
        case "sleep.state":
          this.setSleeping(!!p.sleeping);
          break;
        case "devices.changed":
          this.setListeners(Number(p.count ?? 0));
          break;
        case "gaze.target":
          if (p.kind !== "clear" && Number.isFinite(p.gx) && Number.isFinite(p.gy)) {
            this.ev.onGazeTarget?.({ x: p.gx, y: p.gy, label: String(p.label ?? ""), kind: p.kind, durationMs: Number(p.durationMs ?? 4000) });
          }
          break;
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws || this.stopped) return;
      this.ws = null;
      this.retry = setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 8000);
    };
    ws.onerror = () => ws.close();
  }

  private setSleeping(sleeping: boolean): void {
    if (this.gate.sleeping !== sleeping) {
      this.gate.sleeping = sleeping;
      this.ev.log?.(`presence: ${sleeping ? "sleeping — cursor streaming paused" : "awake — cursor streaming resumed"}`);
      this.ev.onSleep?.(sleeping);
      this.gate.reset();
    }
  }

  private setListeners(n: number): void {
    if (process.env.BAYMAX_CURSOR_ALWAYS === "1") return;
    if (n > 0 && this.gate.listeners === 0) this.gate.reset();
    this.gate.listeners = n;
  }
}
