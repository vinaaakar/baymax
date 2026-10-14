// 2D vector face shared with the iPhone (ios/BaymaxKit/Sources/BaymaxKit/Face2D.swift).
// Numbers come from design/baymax-visual-spec.json "face2d"; both ports are checked against its test vectors.
//
// Coordinates are "face units": origin at the centre of the head, x right, y DOWN (same as SVG and SwiftUI),
// head half-width = 1. Renderers map face units to pixels with fitFace(). Nothing here is 3D: the sense of depth
// comes from moving the eye group across the face, narrowing the eye nearest the edge and a small head parallax.
import spec from "../../../design/baymax-visual-spec.json";
import type { CharacterState } from "./protocol";
import type { Pose } from "../character/rig";

export const VISUAL_SPEC = spec;
export const FACE = spec.face2d;

export type Expression =
  | "IDLE" | "TRACKING" | "LISTENING" | "THINKING" | "RESEARCHING" | "EXECUTING" | "SPEAKING" | "POINTING"
  | "SUCCESS" | "CONFUSED" | "CONCERNED" | "SLEEPING" | "ERROR" | "DISCONNECTED";

export interface ExpressionContext {
  cursorLive: boolean;      // real cursor samples are arriving from the Mac right now
  pointing: boolean;        // a verified screen target is being pointed at
  taskKind?: string | null; // kind of the running task ("research", "apps", ...)
}

/** What the face shows. Derived only from real state: the backend state, live cursor samples, real targets and tasks. */
export function expressionFor(state: CharacterState, ctx: ExpressionContext): Expression {
  switch (state) {
    case "SLEEPING": case "DISCONNECTED": case "ERROR": case "LISTENING": case "SPEAKING":
      return state;
    default:
      break;
  }
  if (ctx.pointing) return "POINTING";
  if (state === "EXECUTING" || state === "THINKING") return ctx.taskKind === "research" ? "RESEARCHING" : state;
  if (state === "IDLE") return ctx.cursorLive ? "TRACKING" : "IDLE";
  return state; // SUCCESS, CONFUSED, CONCERNED
}

export interface Ellipse { cx: number; cy: number; rx: number; ry: number }

export interface FaceGeometry {
  head: Ellipse & { rotation: number }; // rotation in radians (head roll), about the head centre
  eyeL: Ellipse;                         // in head-local face units (rotate with the head)
  eyeR: Ellipse;
  line: { x1: number; y1: number; x2: number; y2: number; thickness: number };
  pupil: { x: number; y: number };       // eye-group offset from rest, face units (diagnostics)
  pointer: { angle: number; x: number; y: number } | null; // 2D pointing gesture: direction + anchor at the head edge
  glow: number;
  dim: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : 0));
const r6 = (v: number) => Math.round(v * 1e6) / 1e6;

/** Pose (from rig.ts) -> 2D face geometry. `point` is the look target (-1..1, x right+, y up+) while pointing. */
export function faceGeometry(pose: Pose, expression: Expression, point: { x: number; y: number } | null = null): FaceGeometry {
  const F = FACE;
  // Head: slight parallax with yaw/pitch, breathing, roll.
  const hx = clamp(pose.headYaw, -0.6, 0.6) * F.parallax.yaw;
  const hy = clamp(pose.headPitch, -0.6, 0.6) * F.parallax.pitch - pose.bodyY * F.parallax.body;
  const breathe = 1 + (pose.breathe - 1) * F.head.breatheGain;
  const head = { cx: r6(hx), cy: r6(hy), rx: r6(F.head.rx), ry: r6(F.head.ry * breathe), rotation: r6(clamp(pose.headRoll, -0.5, 0.5)) };

  // Eye group: the eyes lead the gaze and slide over the face; the head adds a little of its own turn.
  const maxX = F.head.rx - F.eye.x - F.eye.rx - F.eye.margin;
  const maxY = F.head.ry - Math.abs(F.eye.y) - F.eye.ry - F.eye.margin;
  const gx = clamp(clamp(pose.eyeX, -1, 1) * F.travel.x + clamp(pose.headYaw, -0.6, 0.6) * F.travel.yawFollow, -maxX, maxX);
  const gy = clamp(-clamp(pose.eyeY, -1, 1) * F.travel.y + clamp(pose.headPitch, -0.6, 0.6) * F.travel.pitchFollow, -maxY, maxY);
  const turn = maxX > 0 ? gx / maxX : 0; // -1..1
  const spacing = F.eye.x * (1 - F.foreshorten.spacing * Math.abs(turn));
  const open = clamp(pose.eyeOpen, 0, 1);
  const ry = Math.max(F.eye.minOpen, F.eye.ry * open);
  const asym = (F.expressions as Record<string, { eyeScaleL?: number; eyeScaleR?: number }>)[expression] ?? {};
  // The eye nearest the head's edge is seen more from the side, so it narrows.
  const narrowL = 1 - F.foreshorten.width * Math.max(0, -turn);
  const narrowR = 1 - F.foreshorten.width * Math.max(0, turn);
  const sl = asym.eyeScaleL ?? 1;
  const sr = asym.eyeScaleR ?? 1;
  const eyeL = { cx: r6(gx - spacing), cy: r6(F.eye.y + gy), rx: r6(F.eye.rx * narrowL * sl), ry: r6(ry * sl) };
  const eyeR = { cx: r6(gx + spacing), cy: r6(F.eye.y + gy), rx: r6(F.eye.rx * narrowR * sr), ry: r6(ry * sr) };
  const line = { x1: eyeL.cx, y1: eyeL.cy, x2: eyeR.cx, y2: eyeR.cy, thickness: F.line.thickness };

  let pointer: FaceGeometry["pointer"] = null;
  if (expression === "POINTING" && point) {
    const tx = clamp(point.x, -1, 1);
    const ty = clamp(point.y, -1, 1);
    const angle = Math.atan2(-ty, tx === 0 && ty === 0 ? 1 : tx); // screen angle, y down
    pointer = { angle: r6(angle), x: r6(head.cx + Math.cos(angle) * F.head.rx * F.pointer.radius),
                y: r6(head.cy + Math.sin(angle) * F.head.ry * F.pointer.radius) };
  }
  return { head, eyeL, eyeR, line, pupil: { x: r6(gx), y: r6(gy) }, pointer, glow: clamp(pose.glow, 0, 1), dim: clamp(pose.dim, 0, 1) };
}

export interface FaceFit { scale: number; cx: number; cy: number }

/** Pixels per face unit and the head's resting centre for a view of w x h. Landscape shows a larger face. */
export function fitFace(w: number, h: number): FaceFit {
  const W = w > 0 && Number.isFinite(w) ? w : 1;
  const H = h > 0 && Number.isFinite(h) ? h : 1;
  const f = FACE.fit;
  const landscape = W / H >= 1.2;
  const heightFrac = landscape ? f.landscapeHeightFrac : f.heightFrac;
  const scale = Math.min((W * f.widthFrac) / (2 * FACE.head.rx), (H * heightFrac) / (2 * FACE.head.ry * (1 + f.parallaxRoom)));
  return { scale: r6(scale), cx: r6(W / 2), cy: r6(H * (landscape ? f.landscapeCenterY : f.centerY)) };
}
