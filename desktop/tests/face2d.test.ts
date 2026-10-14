import fs from "fs";
import path from "path";
import { expressionFor, FACE, faceGeometry, fitFace, VISUAL_SPEC, type Expression } from "../src/shared/face2d";
import { REST, targetPose } from "../src/character/rig";
import { cursorToLook } from "../src/shared/gaze";

const inside = (x: number, y: number, h: { cx: number; cy: number; rx: number; ry: number }) =>
  ((x - h.cx) / h.rx) ** 2 + ((y - h.cy) / h.ry) ** 2 <= 1;

describe("2D face geometry (design/baymax-visual-spec.json face2d)", () => {
  it("matches the shared vectors used by the iPhone port", () => {
    for (const v of VISUAL_SPEC.face2dTestVectors) {
      const g = faceGeometry(v.pose as typeof REST, v.expression as Expression, v.point, v.composition as "full" | "dome");
      expect(g).toEqual(v.geometry);
    }
    for (const v of VISUAL_SPEC.fitTestVectors) expect(fitFace(v.w, v.h)).toEqual({ scale: v.scale, cx: v.cx, cy: v.cy, composition: v.composition });
    for (const v of VISUAL_SPEC.expressionTestVectors) {
      expect(expressionFor(v.state as never, { cursorLive: v.cursorLive, pointing: v.pointing, taskKind: v.taskKind })).toBe(v.expression);
    }
  });

  it("real cursor positions move the pupils the same way, and they never leave the head", () => {
    const at = (vx: number, vy: number) => {
      const look = cursorToLook(vx, vy);
      return faceGeometry(targetPose("IDLE", 1, { lookX: look.x, lookY: look.y, level: 0, reducedMotion: true }), "TRACKING");
    };
    const left = at(0, 0.5), right = at(1, 0.5), top = at(0.5, 0), bottom = at(0.5, 1);
    expect(right.pupil.x - left.pupil.x).toBeGreaterThan(0.5); // clearly visible travel, not a 1 px twitch
    expect(top.pupil.y).toBeLessThan(bottom.pupil.y);           // y down: looking up = smaller y
    for (let vx = -0.5; vx <= 1.5; vx += 0.25) {
      for (let vy = -0.5; vy <= 1.5; vy += 0.25) {
        const g = at(vx, vy);
        for (const e of [g.eyeL, g.eyeR]) {
          for (const [dx, dy] of [[e.rx, 0], [-e.rx, 0], [0, e.ry], [0, -e.ry]]) expect(inside(e.cx + dx, e.cy + dy, { ...g.head, cx: 0, cy: 0 })).toBe(true);
        }
      }
    }
  });

  it("looking sideways narrows the eye nearest the edge (2D depth cue)", () => {
    const g = faceGeometry({ ...REST, eyeX: 1 }, "TRACKING");
    expect(g.eyeR.rx).toBeLessThan(g.eyeL.rx);
    expect(g.line.x1).toBe(g.eyeL.cx);
    expect(g.line.x2).toBe(g.eyeR.cx);
  });

  it("pointing produces a 2D gesture aimed at the target", () => {
    const right = faceGeometry(REST, "POINTING", { x: 1, y: 0 });
    expect(right.pointer!.angle).toBeCloseTo(0, 6);
    expect(right.pointer!.x).toBeGreaterThan(FACE.head.rx);
    const upLeft = faceGeometry(REST, "POINTING", { x: -0.7, y: 0.7 });
    expect(upLeft.pointer!.x).toBeLessThan(0);
    expect(upLeft.pointer!.y).toBeLessThan(0);
    expect(faceGeometry(REST, "IDLE", { x: 1, y: 0 }).pointer).toBeNull();
  });

  it("sleeping closes the eyes to thin lines; portrait shows the whole head", () => {
    const g = faceGeometry(targetPose("SLEEPING", 1, { lookX: 0, lookY: 0, level: 0, reducedMotion: true }), "SLEEPING");
    expect(g.eyeL.ry).toBeLessThan(0.02);
    for (const [w, h] of [[390, 844], [300, 300], [430, 932]]) {
      const f = fitFace(w, h);
      expect(f.composition).toBe("full");
      expect(f.cx + f.scale * FACE.head.rx).toBeLessThanOrEqual(w); // never cropped
      expect(f.cy - f.scale * FACE.head.ry).toBeGreaterThanOrEqual(0);
      expect(f.cy + f.scale * FACE.head.ry).toBeLessThanOrEqual(h);
    }
  });

  it("the Mac companion keeps the whole head, centred, never cropped", () => {
    for (const [w, h] of [[560, 320], [1440, 900], [2000, 1250]]) {
      const f = fitFace(w, h, false);
      expect(f.composition).toBe("full");
      expect(f.cx + f.scale * FACE.head.rx).toBeLessThanOrEqual(w);
      expect(f.cy - f.scale * FACE.head.ry).toBeGreaterThanOrEqual(0);
      expect(f.cy + f.scale * FACE.head.ry).toBeLessThanOrEqual(h);
    }
  });

  it("iPhone landscape is a dome rising from the bottom edge, with the eyes always visible inside it", () => {
    for (const [w, h] of [[844, 390], [560, 320], [932, 430], [1600, 500]]) {
      const f = fitFace(w, h);
      expect(f.composition).toBe("dome");
      expect(f.cy).toBeGreaterThanOrEqual(h);                              // centre at/below the bottom edge: only the top shows
      expect(f.cy - f.scale * FACE.head.ry).toBeGreaterThan(0);            // top of the dome is on screen
      expect(f.cx - f.scale * FACE.head.rx).toBeGreaterThanOrEqual(0);     // sides on screen
      for (let lx = -1; lx <= 1; lx += 0.25) {
        for (let ly = -1; ly <= 1; ly += 0.25) {
          const g = faceGeometry(targetPose("IDLE", 1, { lookX: lx, lookY: ly, level: 0, reducedMotion: true }), "TRACKING", null, "dome");
          for (const e of [g.eyeL, g.eyeR]) {
            const bottom = f.cy + (g.head.cy + e.cy + e.ry) * f.scale;
            expect(bottom).toBeLessThan(h);                                 // eyes never slide off the bottom
            expect(inside(e.cx, e.cy - e.ry, { cx: 0, cy: 0, rx: g.head.rx, ry: g.head.ry })).toBe(true); // nor out of the dome
          }
        }
      }
    }
  });

  it("the active renderer is 2D: no three.js, no 3D model module", () => {
    const src = fs.readFileSync(path.join(__dirname, "../src/character/BaymaxCharacter.tsx"), "utf8");
    expect(src).not.toMatch(/from "three"|WebGL|PerspectiveCamera/);
    expect(fs.existsSync(path.join(__dirname, "../src/character/baymaxModel.ts"))).toBe(false);
    expect(fs.existsSync(path.join(__dirname, "../src/shared/framing.ts"))).toBe(false);
    // iPhone: the active renderer is a SwiftUI Canvas; SceneKit is not imported anywhere in the app.
    const ios = path.join(__dirname, "../../ios/BaymaxCompanion");
    const swift = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? swift(path.join(dir, e.name)) : e.name.endsWith(".swift") ? [path.join(dir, e.name)] : []);
    for (const f of swift(ios)) expect(fs.readFileSync(f, "utf8"), f).not.toMatch(/import SceneKit|SCNView|SCNNode/);
    expect(fs.readFileSync(path.join(ios, "Character/CharacterView.swift"), "utf8")).toMatch(/Canvas \{/);
  });
});
