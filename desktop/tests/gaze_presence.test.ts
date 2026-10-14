import { CursorGate, buildCursorMessage, lookFromWindow } from "../electron/presenceLogic";
import { targetPose } from "../src/character/rig";
import { cursorToLook, DEFAULT_GAZE, GazeTracker, restLook } from "../src/shared/gaze";

describe("cursor -> gaze mapping (same vectors as BaymaxKit GazeTests)", () => {
  it("maps screen positions to head/eye direction for a phone below the display", () => {
    expect(cursorToLook(0.5, 0.5)).toEqual({ x: 0, y: 0.3 });
    const tl = cursorToLook(0, 0);
    expect(tl.x).toBeCloseTo(-1);
    expect(tl.y).toBeCloseTo(0.72);
    const br = cursorToLook(1, 1);
    expect(br.x).toBeCloseTo(1);
    expect(br.y).toBeCloseTo(-0.12);
  });

  it("handles other monitors, edges and bad input", () => {
    expect(cursorToLook(3, 0.5).x).toBe(1);
    expect(cursorToLook(-2, 0.5).x).toBe(-1);
    expect(cursorToLook(Number.NaN, 0.5).x).toBe(0);
  });

  it("is monotonic left-to-right (moving the mouse right turns the head right)", () => {
    let prev = -Infinity;
    for (let vx = -0.5; vx <= 1.5; vx += 0.05) {
      const x = cursorToLook(vx, 0.5).x;
      expect(x).toBeGreaterThanOrEqual(prev);
      prev = x;
    }
  });

  it("phone placement shifts the gaze", () => {
    expect(cursorToLook(0.5, 0.5, { ...DEFAULT_GAZE, placement: "left" }).x).toBeGreaterThan(0);
    expect(cursorToLook(0.5, 0.5, { ...DEFAULT_GAZE, placement: "right" }).x).toBeLessThan(0);
  });

  it("tracker drops out-of-order samples, decays when stale, and points temporarily", () => {
    const t = new GazeTracker();
    expect(t.update({ vx: 1, vy: 0.5, seq: 5 }, 0)).toBe(true);
    expect(t.update({ vx: 0, vy: 0.5, seq: 4 }, 10)).toBe(false);
    expect(t.look(100).x).toBeCloseTo(1);
    expect(t.isLive(100)).toBe(true);
    expect(t.look(2500 + 800 + 1)).toEqual(restLook());
    t.pointAt(0, 0.5, "Run", 1000, 4000);
    expect(t.look(4100).x).toBeCloseTo(-1);
    expect(t.activePoint(5200)).toBeNull();
    t.reset();
    expect(t.update({ vx: 0.5, vy: 0.5, seq: 1 }, 6000)).toBe(true);
  });
});

describe("cursor gate (Electron main)", () => {
  it("streams only when awake, a phone is listening, the cursor moved, and within the rate limit", () => {
    const g = new CursorGate(30, 1);
    expect(g.offer({ x: 1, y: 1 }, 1, 0)).toBe(false); // nobody listening
    g.listeners = 1;
    expect(g.offer({ x: 1, y: 1 }, 1, 0)).toBe(true);
    expect(g.offer({ x: 50, y: 1 }, 1, 10)).toBe(false); // > 30 Hz
    expect(g.offer({ x: 50, y: 1 }, 1, 40)).toBe(true);
    expect(g.offer({ x: 50.4, y: 1 }, 1, 80)).toBe(false); // not moved
    expect(g.offer({ x: 50.4, y: 1 }, 2, 120)).toBe(true); // moved to another display
    g.sleeping = true;
    expect(g.offer({ x: 500, y: 500 }, 1, 200)).toBe(false); // asleep
    g.sleeping = false;
    g.reset();
    expect(g.offer({ x: 500, y: 500 }, 1, 240)).toBe(true);
  });

  it("builds a message with real display geometry", () => {
    const d = { id: 7, bounds: { x: 0, y: 0, width: 1512, height: 982 }, scaleFactor: 2 };
    const m = buildCursorMessage({ x: 10, y: 20 }, d, d, 5);
    expect(m).toEqual({ type: "cursor", x: 10, y: 20, display: { id: 7, x: 0, y: 0, width: 1512, height: 982, scaleFactor: 2 },
      primary: { id: 7, x: 0, y: 0, width: 1512, height: 982, scaleFactor: 2 }, t: 5 });
  });

  it("companion looks toward a screen target relative to its window", () => {
    const win = { x: 1000, y: 500, width: 640, height: 400 };
    expect(lookFromWindow({ x: 0, y: 0 }, win).x).toBe(-1);
    expect(lookFromWindow({ x: 0, y: 0 }, win).y).toBeGreaterThan(0);
    const c = lookFromWindow({ x: 1320, y: 648 }, win);
    expect(c.x).toBeCloseTo(0);
    expect(c.y).toBeCloseTo(0);
  });
});

describe("pointing gesture", () => {
  const base = { lookX: 0, lookY: 0, level: 0, reducedMotion: false };
  it("raises the arm on the target's side and looks at it", () => {
    const r = targetPose("IDLE", 1, { ...base, point: { x: 0.8, y: 0.2 } });
    expect(r.armRFwd).toBeGreaterThan(0.8);
    expect(r.headYaw).toBeGreaterThan(0.2);
    const l = targetPose("IDLE", 1, { ...base, point: { x: -0.8, y: 0.2 } });
    expect(l.armLFwd).toBeGreaterThan(0.8);
    expect(targetPose("SLEEPING", 1, { ...base, point: { x: 0.8, y: 0.2 } }).armRFwd).toBeLessThan(0.1);
  });
});
