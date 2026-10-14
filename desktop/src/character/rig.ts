// Pure animation rig: state + time -> target pose, and smooth blending between poses.
// Kept free of three.js so it can be unit-tested and mirrored by the iOS app.
import type { CharacterState } from "../shared/protocol";

export interface Pose {
  bodyY: number;        // vertical offset of the whole body
  breathe: number;      // vertical scale factor of the torso (1 = rest)
  lean: number;         // forward lean of the body (radians, +forward)
  sway: number;         // body roll (radians)
  headYaw: number;
  headPitch: number;    // +down
  headRoll: number;
  eyeX: number;         // eye offset (normalised -1..1)
  eyeY: number;
  eyeOpen: number;      // 0 closed .. 1 open
  armL: number;         // arm outward swing (radians, +away from body)
  armR: number;
  armLFwd: number;      // arm forward raise (radians)
  armRFwd: number;
  glow: number;         // 0..1 accent glow intensity (UI ring, rim light)
  dim: number;          // 0..1 how much to dim/desaturate (disconnected/sleeping)
}

export interface RigInput {
  lookX: number;        // -1..1 where the user/cursor is
  lookY: number;
  level: number;        // 0..1 audio level (mic while listening, speech envelope while speaking)
  reducedMotion: boolean;
  point?: { x: number; y: number } | null; // screen target to look at and point to (-1..1, x right+, y up+)
}

export const REST: Pose = {
  bodyY: 0, breathe: 1, lean: 0, sway: 0, headYaw: 0, headPitch: 0.05, headRoll: 0, eyeX: 0, eyeY: 0, eyeOpen: 1,
  armL: 0.16, armR: 0.16, armLFwd: 0, armRFwd: 0, glow: 0, dim: 0,
};

const TAU = Math.PI * 2;

/** Natural blink: ~150 ms closures, pseudo-random intervals of 2.8–5.5 s (deterministic in t). */
export function blink(t: number): number {
  const period = 4.1;
  const cycle = Math.floor(t / period);
  const jitter = ((Math.sin(cycle * 12.9898) * 43758.5453) % 1 + 1) % 1; // 0..1
  const start = cycle * period + jitter * (period - 0.4);
  const d = t - start;
  if (d < 0 || d > 0.16) return 1;
  return Math.abs(d - 0.08) / 0.08; // 1 -> 0 -> 1
}

export function targetPose(state: CharacterState, t: number, input: RigInput): Pose {
  const m = input.reducedMotion ? 0.15 : 1; // motion amplitude
  const p: Pose = { ...REST };
  const breath = Math.sin((t * TAU) / 4.2); // slow, calm breathing
  p.breathe = 1 + 0.012 * breath * (input.reducedMotion ? 0.5 : 1);
  p.bodyY = 0.006 * breath * m;
  p.eyeOpen = input.reducedMotion ? 1 : blink(t);
  const lookX = clamp(input.lookX, -1, 1);
  const lookY = clamp(input.lookY, -1, 1);

  switch (state) {
    case "IDLE":
      p.headYaw = lookX * 0.28 + Math.sin(t * 0.37) * 0.04 * m;
      p.headPitch = 0.05 - lookY * 0.12 + Math.sin(t * 0.23) * 0.02 * m;
      p.headRoll = Math.sin(t * 0.29) * 0.025 * m;
      p.eyeX = lookX * 0.9; // the eyes lead, the head follows a little (same in CharacterRig.swift)
      p.eyeY = lookY * 0.7;
      p.sway = Math.sin(t * 0.31) * 0.012 * m;
      break;
    case "LISTENING":
      p.lean = 0.06;
      p.headYaw = lookX * 0.18;
      p.headPitch = -0.02 - lookY * 0.08;
      p.headRoll = 0.09 * m; // attentive head tilt
      p.eyeX = lookX * 0.3;
      p.bodyY += input.level * 0.02 * m;
      p.glow = 0.35 + input.level * 0.65;
      break;
    case "THINKING":
      p.headYaw = 0.22 + Math.sin(t * 0.8) * 0.08 * m;
      p.headPitch = -0.16;
      p.headRoll = -0.06;
      p.eyeX = 0.55 + Math.sin(t * 1.1) * 0.15 * m;
      p.eyeY = 0.55;
      p.armRFwd = 0.12;
      p.glow = 0.25 + 0.15 * (0.5 + 0.5 * Math.sin(t * 2.2)) * m;
      break;
    case "SPEAKING": {
      const env = input.level > 0 ? input.level : 0.5 + 0.5 * Math.sin(t * 7.5);
      p.headPitch = 0.02 - env * 0.05 * m;
      p.headYaw = lookX * 0.2 + Math.sin(t * 0.9) * 0.05 * m;
      p.headRoll = Math.sin(t * 1.3) * 0.03 * m;
      p.eyeX = lookX * 0.4;
      p.bodyY += env * 0.006 * m;
      p.armL = 0.2 + env * 0.03 * m;
      p.armR = 0.2 + env * 0.03 * m;
      p.glow = 0.2 + env * 0.3;
      break;
    }
    case "EXECUTING":
      p.headPitch = 0.16;
      p.headYaw = -0.08;
      p.eyeY = -0.5;
      p.armRFwd = 0.55 + Math.sin(t * 3.2) * 0.06 * m; // working gesture
      p.armR = 0.28;
      p.armLFwd = 0.18;
      p.glow = 0.45 + 0.2 * Math.sin(t * 3.2) * m;
      break;
    case "SUCCESS":
      p.bodyY += Math.abs(Math.sin(t * 4.0)) * 0.025 * m; // restrained happy bounce
      p.headRoll = 0.12;
      p.headPitch = -0.08;
      p.armL = 0.55;
      p.armR = 0.55;
      p.armLFwd = 0.35;
      p.armRFwd = 0.35;
      p.eyeOpen = Math.min(p.eyeOpen, 0.75); // gentle "smile" squint
      p.glow = 0.8;
      break;
    case "CONFUSED":
      p.headRoll = -0.24;
      p.headYaw = 0.1;
      p.headPitch = -0.04;
      p.eyeY = 0.3;
      p.armRFwd = 0.45;
      p.armR = 0.35;
      break;
    case "CONCERNED":
      p.headPitch = 0.2;
      p.headRoll = 0.05;
      p.eyeY = -0.35;
      p.armL = 0.08;
      p.armR = 0.08;
      p.armLFwd = 0.25;
      p.armRFwd = 0.25;
      p.lean = 0.04;
      p.glow = 0.5;
      break;
    case "SLEEPING":
      p.breathe = 1 + 0.02 * Math.sin((t * TAU) / 6.5);
      p.bodyY = -0.02;
      p.headPitch = 0.34;
      p.headRoll = 0.08;
      p.eyeOpen = 0.04;
      p.armL = 0.1;
      p.armR = 0.1;
      p.lean = 0.05;
      p.dim = 0.45;
      break;
    case "ERROR": {
      const decay = Math.max(0, 1 - (t % 6) / 1.5); // a short head shake that settles
      p.headYaw = Math.sin(t * 9) * 0.12 * decay * m;
      p.headPitch = 0.14;
      p.eyeOpen = Math.min(p.eyeOpen, 0.6);
      p.eyeY = -0.2;
      p.armL = 0.1;
      p.armR = 0.1;
      p.glow = 0.9;
      break;
    }
    case "DISCONNECTED":
      p.headPitch = 0.28;
      p.eyeOpen = 0.35;
      p.armL = 0.08;
      p.armR = 0.08;
      p.dim = 0.7;
      p.breathe = 1;
      p.bodyY = 0;
      break;
  }
  if (input.point && state !== "SLEEPING" && state !== "DISCONNECTED") applyPoint(p, input.point, m);
  return p;
}

/** Look at a screen target and point at it with the arm on that side. */
export function applyPoint(p: Pose, target: { x: number; y: number }, m = 1): void {
  const tx = clamp(target.x, -1, 1);
  const ty = clamp(target.y, -1, 1);
  p.headYaw = tx * 0.35;
  p.headPitch = -ty * 0.18;
  p.eyeX = tx;
  p.eyeY = ty;
  const lift = 0.9 + ty * 0.45; // raise the arm forward towards the target's height
  const spread = 0.25 + Math.abs(tx) * 0.5;
  if (tx >= 0) {
    p.armRFwd = lift;
    p.armR = spread;
  } else {
    p.armLFwd = lift;
    p.armL = spread;
  }
  p.lean = 0.03 * m;
}

/** Frame-rate independent exponential smoothing toward the target pose. */
export function blend(current: Pose, target: Pose, dt: number, rate = 6): Pose {
  const k = 1 - Math.exp(-rate * Math.max(0, Math.min(dt, 0.25)));
  const out = { ...current };
  for (const key of Object.keys(target) as (keyof Pose)[]) {
    // Eyelids and breathing follow quickly so blinks stay crisp.
    const kk = key === "eyeOpen" ? 1 - Math.exp(-30 * dt) : key === "breathe" || key === "bodyY" ? 1 - Math.exp(-12 * dt) : k;
    out[key] = current[key] + (target[key] - current[key]) * kk;
  }
  return out;
}

/** Target frame rate: lower when nothing is happening, to save battery. */
export function frameRateFor(state: CharacterState, reducedMotion: boolean): number {
  if (state === "SLEEPING" || state === "DISCONNECTED") return 12;
  if (reducedMotion) return 20;
  return state === "IDLE" ? 30 : 45;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : 0));
}
