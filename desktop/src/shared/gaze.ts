// Cursor -> gaze mapping. Mirrored exactly by ios/BaymaxKit/Sources/BaymaxKit/Gaze.swift.
//
// Input: the Mac cursor normalised to the PRIMARY display (vx, vy; 0..1 on that display, beyond it on
// other monitors). Output: a look direction for the rig (x right+, y up+, each -1..1).
// The phone is assumed to sit in front of the primary display; `placement` says where.

export type Placement = "below" | "left" | "right";

export interface GazeConfig {
  placement: Placement;
  gainX: number;      // how far the head turns at the screen edges
  gainY: number;
  staleMs: number;    // after this long without samples, drift back to rest
  recenterMs: number; // duration of that drift
}

export const DEFAULT_GAZE: GazeConfig = { placement: "below", gainX: 1, gainY: 0.7, staleMs: 2500, recenterMs: 800 };

export interface Look { x: number; y: number }

const clamp = (v: number, lo = -1, hi = 1) => (Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : 0);

/** Resting direction: a phone below the display looks slightly up at it. */
export function restLook(cfg: GazeConfig = DEFAULT_GAZE): Look {
  return { x: cfg.placement === "left" ? 0.35 : cfg.placement === "right" ? -0.35 : 0, y: cfg.placement === "below" ? 0.3 : 0.1 };
}

export function cursorToLook(vx: number, vy: number, cfg: GazeConfig = DEFAULT_GAZE): Look {
  // Invalid samples look straight ahead; off-screen positions push to the edge and a little beyond.
  const nx = Number.isFinite(vx) ? clamp(vx, -0.6, 1.6) : 0.5;
  const ny = Number.isFinite(vy) ? clamp(vy, -0.6, 1.6) : 0.5;
  let x = (nx - 0.5) * 2 * cfg.gainX;
  let y = (0.5 - ny) * 2 * cfg.gainY;
  const rest = restLook(cfg);
  if (cfg.placement === "below") y = y * 0.6 + rest.y;      // the display is above the phone
  if (cfg.placement === "left") x = x * 0.7 + rest.x;       // the display is to the phone's right
  if (cfg.placement === "right") x = x * 0.7 + rest.x;
  return { x: clamp(x), y: clamp(y) };
}

export interface CursorSample { vx: number; vy: number; seq?: number }

/** Tracks the latest cursor sample and decays to rest when samples stop (disconnect, sleep, other app). */
export class GazeTracker {
  private target: Look;
  private lastAt = -Infinity;
  private lastSeq = -1;
  private point: { look: Look; until: number; label: string } | null = null;

  constructor(public cfg: GazeConfig = DEFAULT_GAZE) {
    this.target = restLook(cfg);
  }

  update(s: CursorSample, now: number): boolean {
    if (!Number.isFinite(s.vx) || !Number.isFinite(s.vy)) return false;
    if (s.seq !== undefined && s.seq <= this.lastSeq && this.lastSeq - s.seq < 1000) return false; // out-of-order
    if (s.seq !== undefined) this.lastSeq = s.seq;
    this.target = cursorToLook(s.vx, s.vy, this.cfg);
    this.lastAt = now;
    return true;
  }

  pointAt(vx: number, vy: number, label: string, durationMs: number, now: number): void {
    this.point = { look: cursorToLook(vx, vy, this.cfg), until: now + Math.max(500, Math.min(durationMs, 15000)), label };
  }

  clearPoint(): void {
    this.point = null;
  }

  /** Reset after reconnect/wake so stale sequence numbers don't block fresh samples. */
  reset(): void {
    this.lastSeq = -1;
    this.lastAt = -Infinity;
    this.point = null;
  }

  activePoint(now: number): { look: Look; label: string } | null {
    if (this.point && now > this.point.until) this.point = null;
    return this.point;
  }

  look(now: number): Look {
    const p = this.activePoint(now);
    if (p) return p.look;
    const rest = restLook(this.cfg);
    const age = now - this.lastAt;
    if (age <= this.cfg.staleMs) return this.target;
    const k = Math.min(1, (age - this.cfg.staleMs) / this.cfg.recenterMs);
    return { x: this.target.x + (rest.x - this.target.x) * k, y: this.target.y + (rest.y - this.target.y) * k };
  }

  isLive(now: number): boolean {
    return now - this.lastAt <= this.cfg.staleMs;
  }
}
