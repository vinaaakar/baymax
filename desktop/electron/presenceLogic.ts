// Pure presence logic shared by the Electron main process and unit tests (no Electron/Node imports).

export interface Rect { x: number; y: number; width: number; height: number }
export interface DisplayInfo { id: number; bounds: Rect; scaleFactor: number }
export interface Point { x: number; y: number }

export interface CursorMessage {
  type: "cursor";
  x: number;
  y: number;
  display: { id: number; x: number; y: number; width: number; height: number; scaleFactor: number };
  primary: { id: number; x: number; y: number; width: number; height: number; scaleFactor: number };
  t: number;
}

export function buildCursorMessage(p: Point, display: DisplayInfo, primary: DisplayInfo, t: number): CursorMessage {
  const d = (x: DisplayInfo) => ({ id: x.id, ...x.bounds, scaleFactor: x.scaleFactor });
  return { type: "cursor", x: p.x, y: p.y, display: d(display), primary: d(primary), t };
}

/** Decides whether a cursor sample should be streamed: awake, someone listening, moved, and not too soon. */
export class CursorGate {
  private last: Point | null = null;
  private lastDisplay = -1;
  private lastSentAt = -Infinity;
  sleeping = false;
  listeners = 0;

  constructor(public readonly maxHz = 30, public readonly minMovePx = 1) {}

  get active(): boolean {
    return !this.sleeping && this.listeners > 0;
  }

  offer(p: Point, displayId: number, now: number): boolean {
    if (!this.active) return false;
    if (now - this.lastSentAt < 1000 / this.maxHz) return false;
    if (this.last && displayId === this.lastDisplay && Math.abs(p.x - this.last.x) + Math.abs(p.y - this.last.y) < this.minMovePx) return false;
    this.last = { ...p };
    this.lastDisplay = displayId;
    this.lastSentAt = now;
    return true;
  }

  /** Forget the last position so the next sample is sent even if unchanged (e.g. after wake or a new phone). */
  reset(): void {
    this.last = null;
    this.lastSentAt = -Infinity;
  }
}

/** Direction from the companion window towards a global screen point, normalised to -1..1 for the rig. */
/** Look direction from the face (centred in the landscape companion window) toward a global point. */
export function lookFromWindow(target: Point, win: Rect, eyeHeightFraction = 0.5): Point {
  const cx = win.x + win.width / 2;
  const cy = win.y + win.height * eyeHeightFraction;
  const clamp = (v: number) => Math.max(-1, Math.min(1, v));
  return { x: clamp((target.x - cx) / 600), y: clamp(-(target.y - cy) / 400) };
}
