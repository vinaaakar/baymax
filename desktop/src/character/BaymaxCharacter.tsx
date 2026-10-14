// 2D vector companion for the Mac: SVG shapes, gradients and composited layers, no 3D scene.
// The same face is drawn on the iPhone with SwiftUI shapes from the same spec and math (shared/face2d.ts <-> Face2D.swift).
// Frame-rate capped, paused when hidden, honours prefers-reduced-motion, and animates only from the real application state.
import { useEffect, useRef, useState } from "react";
import type { CharacterState } from "../shared/protocol";
import { expressionFor, FACE, faceGeometry, fitFace, VISUAL_SPEC, type Expression, type FaceGeometry } from "../shared/face2d";
import { blend, frameRateFor, REST, targetPose, type Pose } from "./rig";

export interface CharacterProps {
  state: CharacterState;
  level?: number;              // live audio level 0..1
  look?: { x: number; y: number } | null;
  cursorLive?: boolean;        // real cursor samples are arriving now
  point?: { x: number; y: number } | null; // look at and point to a verified screen target
  taskKind?: string | null;    // kind of the running task (research shows the researching expression)
  progress?: number | null;    // 0..1 when the running task reports progress
  reducedMotion?: boolean;
  quality?: "low" | "high";
  onClick?: () => void;
  onFrame?: (f: { expression: Expression; geometry: FaceGeometry }) => void; // diagnostics
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const C = VISUAL_SPEC.colors;
const S = FACE.shading;

interface Frame { pose: Pose; t: number; w: number; h: number }

export default function BaymaxCharacter(props: CharacterProps) {
  const mount = useRef<HTMLDivElement>(null);
  const live = useRef(props);
  live.current = props;
  const [frame, setFrame] = useState<Frame>({ pose: REST, t: 0, w: 1, h: 1 });

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let size = { w: el.clientWidth || 1, h: el.clientHeight || 1 };
    const ro = new ResizeObserver(() => { size = { w: el.clientWidth || 1, h: el.clientHeight || 1 }; });
    ro.observe(el);
    let pose: Pose = { ...REST };
    let raf = 0;
    let last = performance.now();
    let lastFrame = 0;
    const t0 = last;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (document.hidden) return;
      const p = live.current;
      const reduced = p.reducedMotion ?? prefersReducedMotion();
      if (now - lastFrame < 1000 / frameRateFor(p.state, reduced) - 1) return;
      lastFrame = now;
      const dt = (now - last) / 1000;
      last = now;
      const t = (now - t0) / 1000;
      const target = targetPose(p.state, t, {
        lookX: p.look?.x ?? 0, lookY: p.look?.y ?? 0, level: p.level ?? 0, reducedMotion: reduced, point: p.point ?? null,
      });
      // Follow the real cursor quickly (low lag); everything else stays soft.
      pose = blend(pose, target, dt, reduced ? 14 : p.cursorLive || p.point ? 12 : 5);
      setFrame({ pose, t, w: size.w, h: size.h });
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const expression = expressionFor(props.state, { cursorLive: !!props.cursorLive, pointing: !!props.point, taskKind: props.taskKind });
  const g = faceGeometry(frame.pose, expression, props.point ?? null);
  const fit = fitFace(frame.w, frame.h);
  useEffect(() => props.onFrame?.({ expression, geometry: g }));

  const { scale: s, cx, cy } = fit;
  const hx = cx + g.head.cx * s;
  const hy = cy + g.head.cy * s;
  const roll = (g.head.rotation * 180) / Math.PI;
  const level = props.level ?? 0;
  const t = frame.t;
  const ringR = (k: number) => ({ rx: g.head.rx * s * k, ry: g.head.ry * s * k });
  const deg = (a: number) => (a * 180) / Math.PI;

  return (
    <div ref={mount} className="character-canvas" role="img" aria-label={`BAYMAX companion, ${describeExpression(expression)}`}
      onClick={props.onClick} data-state={props.state} data-expression={expression} data-renderer="svg-2d"
      data-pupil={`${g.pupil.x.toFixed(3)},${g.pupil.y.toFixed(3)}`}>
      <svg width="100%" height="100%" viewBox={`0 0 ${frame.w} ${frame.h}`} aria-hidden="true" style={{ display: "block" }}>
        <defs>
          <radialGradient id="bx-skin" cx={0.5 + S.highlightCenter[0] * 0.35} cy={0.5 + S.highlightCenter[1] * 0.35} r="0.85">
            <stop offset="0" stopColor="#FFFFFF" />
            <stop offset="0.45" stopColor={S.skin} />
            <stop offset="0.85" stopColor={S.skinShade} />
            <stop offset="1" stopColor={S.rimShade} />
          </radialGradient>
          <radialGradient id="bx-stage" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0" stopColor={C.highlight} stopOpacity={0.55} />
            <stop offset="1" stopColor={C.background} stopOpacity={0} />
          </radialGradient>
          <filter id="bx-shadow" x="-30%" y="-30%" width="160%" height="170%">
            <feDropShadow dx="0" dy={S.shadowOffset[1] * s} stdDeviation={S.shadowBlur * s} floodColor={S.shadow} floodOpacity="0.55" />
          </filter>
        </defs>

        {/* Stage glow behind the head: rises with real activity (pose glow). */}
        <ellipse cx={hx} cy={hy} rx={g.head.rx * s * 1.9} ry={g.head.ry * s * 2.1} fill="url(#bx-stage)"
          opacity={(0.25 + g.glow * 0.55) * (1 - g.dim * 0.6)} />

        {/* Indicators behind/around the head (all derived from real state). */}
        {expression === "LISTENING" && (
          <ellipse cx={hx} cy={hy} {...ringR(1.06 + level * 0.06)} fill="none" stroke={C.highlight} strokeWidth={2 + level * 4}
            opacity={0.35 + level * 0.6} data-indicator="listening" />
        )}
        {expression === "ERROR" && (
          <ellipse cx={hx} cy={hy} {...ringR(1.06)} fill="none" stroke={C.error} strokeWidth={3} opacity={0.4 + 0.4 * Math.abs(Math.sin(t * 3))} data-indicator="error" />
        )}
        {expression === "EXECUTING" && (
          <ProgressArc cx={hx} cy={hy} rx={g.head.rx * s * 1.07} ry={g.head.ry * s * 1.07} progress={props.progress ?? null} t={t} />
        )}

        <g transform={`translate(${hx} ${hy}) rotate(${roll})`} filter="url(#bx-shadow)">
          <ellipse cx={0} cy={0} rx={g.head.rx * s} ry={g.head.ry * s} fill="url(#bx-skin)" data-part="head" />
        </g>
        <g transform={`translate(${hx} ${hy}) rotate(${roll}) scale(${s})`}>
          <line x1={g.line.x1} y1={g.line.y1} x2={g.line.x2} y2={g.line.y2} stroke={C.face} strokeWidth={g.line.thickness} strokeLinecap="round" />
          {[g.eyeL, g.eyeR].map((e, i) => (
            <g key={i}>
              <ellipse cx={e.cx} cy={e.cy} rx={e.rx} ry={e.ry} fill={C.face} data-part={i ? "eye-right" : "eye-left"} />
              {e.ry > 0.05 && <ellipse cx={e.cx - e.rx * 0.32} cy={e.cy - e.ry * 0.42} rx={e.rx * 0.22} ry={e.ry * 0.16} fill={S.eyeHighlight} opacity={0.8} />}
            </g>
          ))}
        </g>

        {expression === "POINTING" && g.pointer && (
          <g data-indicator="pointing">
            <g transform={`translate(${cx + g.pointer.x * s} ${cy + g.pointer.y * s}) rotate(${deg(g.pointer.angle)})`}>
              <path d={`M ${0.13 * s} 0 L ${-0.02 * s} ${-0.085 * s} Q ${0.02 * s} 0 ${-0.02 * s} ${0.085 * s} Z`} fill={C.highlight}
                stroke={C.highlight} strokeWidth={Math.max(1.5, 0.012 * s)} strokeLinejoin="round" />
            </g>
            <Reticle w={frame.w} h={frame.h} cx={hx} cy={hy} angle={g.pointer.angle} t={t} />
          </g>
        )}
        {(expression === "THINKING" || expression === "RESEARCHING") && (
          <g data-indicator="thinking">
            {[0, 1, 2].map((i) => (
              <circle key={i} cx={hx + g.head.rx * s * 1.12 + i * 0.13 * s} cy={hy - g.head.ry * s * (0.62 + i * 0.12)} r={0.035 * s} fill={C.text}
                opacity={0.25 + 0.6 * Math.max(0, Math.sin(t * 3 - i * 0.8))} />
            ))}
          </g>
        )}
        {expression === "RESEARCHING" && (
          <g data-indicator="researching" transform={`translate(${hx + Math.cos(t * 1.4) * g.head.rx * s * 1.18} ${hy + Math.sin(t * 1.4) * g.head.ry * s * 0.85})`}>
            <circle r={0.07 * s} fill="none" stroke={C.text} strokeWidth={Math.max(1.5, 0.022 * s)} opacity={0.85} />
            <line x1={0.05 * s} y1={0.05 * s} x2={0.12 * s} y2={0.12 * s} stroke={C.text} strokeWidth={Math.max(1.5, 0.026 * s)} strokeLinecap="round" />
          </g>
        )}
        {expression === "SLEEPING" && (
          <text x={hx + g.head.rx * s * 1.05} y={hy - g.head.ry * s * (0.7 + 0.08 * Math.sin(t))} fill={C.muted}
            fontSize={0.2 * s} fontWeight={600} opacity={0.7} data-indicator="sleeping">z</text>
        )}
        {expression === "DISCONNECTED" && (
          <g data-indicator="disconnected" transform={`translate(${hx + g.head.rx * s * 1.2} ${hy + g.head.ry * s * 0.6})`} stroke={C.muted} strokeWidth={2} opacity={0.8}>
            <line x1={-0.1 * s} y1={0} x2={-0.02 * s} y2={0} /><line x1={0.02 * s} y1={0} x2={0.1 * s} y2={0} />
            <line x1={-0.03 * s} y1={-0.05 * s} x2={0.03 * s} y2={0.05 * s} />
          </g>
        )}
      </svg>
    </div>
  );
}

function ProgressArc({ cx, cy, rx, ry, progress, t }: { cx: number; cy: number; rx: number; ry: number; progress: number | null; t: number }) {
  // Known progress: a filled arc. Unknown: a short arc that travels (never a fake percentage).
  const known = typeof progress === "number" && Number.isFinite(progress);
  const start = known ? -Math.PI / 2 : t * 2.4;
  const sweep = known ? Math.max(0.02, Math.min(1, progress!)) * Math.PI * 2 : Math.PI * 0.45;
  const pt = (a: number) => `${cx + Math.cos(a) * rx} ${cy + Math.sin(a) * ry}`;
  const end = start + Math.min(sweep, Math.PI * 2 - 0.001);
  return (
    <path data-indicator="executing" d={`M ${pt(start)} A ${rx} ${ry} 0 ${end - start > Math.PI ? 1 : 0} 1 ${pt(end)}`} fill="none"
      stroke={C.highlight} strokeWidth={3} strokeLinecap="round" opacity={0.85} />
  );
}

function Reticle({ w, h, cx, cy, angle, t }: { w: number; h: number; cx: number; cy: number; angle: number; t: number }) {
  // Target lock: placed toward the target, at the edge of the face region.
  const pad = 18;
  const dx = Math.cos(angle), dy = Math.sin(angle);
  const reach = Math.min(dx > 0 ? (w - pad - cx) / dx : dx < 0 ? (pad - cx) / dx : Infinity, dy > 0 ? (h - pad - cy) / dy : dy < 0 ? (pad - cy) / dy : Infinity);
  const x = cx + dx * reach, y = cy + dy * reach;
  const r = 9 + 2 * Math.sin(t * 5);
  return (
    <g transform={`translate(${x} ${y})`} stroke={C.highlight} strokeWidth={2} fill="none" data-indicator="target-lock">
      <circle r={r} />
      <line x1={-r - 5} y1={0} x2={-r + 3} y2={0} /><line x1={r - 3} y1={0} x2={r + 5} y2={0} />
      <line x1={0} y1={-r - 5} x2={0} y2={-r + 3} /><line x1={0} y1={r - 3} x2={0} y2={r + 5} />
    </g>
  );
}

export function describeExpression(e: Expression): string {
  return {
    IDLE: "ready", TRACKING: "following your cursor", LISTENING: "listening", THINKING: "thinking", RESEARCHING: "researching",
    EXECUTING: "working on a task", SPEAKING: "speaking", POINTING: "pointing at a target", SUCCESS: "task verified",
    CONFUSED: "needs clarification", CONCERNED: "needs your attention", SLEEPING: "sleeping", ERROR: "something failed",
    DISCONNECTED: "disconnected",
  }[e];
}

export function describeState(s: CharacterState): string {
  return {
    IDLE: "ready", LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking", EXECUTING: "working on a task",
    SUCCESS: "task verified", CONFUSED: "needs clarification", CONCERNED: "needs your attention", SLEEPING: "sleeping",
    ERROR: "something failed", DISCONNECTED: "disconnected",
  }[s];
}
