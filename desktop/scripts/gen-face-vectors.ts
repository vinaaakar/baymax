// Regenerates the 2D face test vectors in design/baymax-visual-spec.json from the reference TS implementation.
// Run after changing face2d numbers: npx vite-node scripts/gen-face-vectors.ts (then run both TS and Swift tests).
import { faceGeometry, fitFace, expressionFor } from "../src/shared/face2d";
import { REST, targetPose } from "../src/character/rig";
import fs from "fs";
const p = new URL("../../design/baymax-visual-spec.json", import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(p, "utf8"));
const r6 = (v: number) => Math.round(v * 1e6) / 1e6;
const cases: [string, any, string, any, string?][] = [
  ["rest", REST, "IDLE", null],
  ["cursor top-left", targetPose("IDLE", 1.0, { lookX: -1, lookY: 1, level: 0, reducedMotion: true }), "TRACKING", null],
  ["cursor right", targetPose("IDLE", 1.0, { lookX: 1, lookY: -0.2, level: 0, reducedMotion: true }), "TRACKING", null],
  ["pointing left-up", targetPose("IDLE", 1.0, { lookX: 0, lookY: 0, level: 0, reducedMotion: true, point: { x: -0.8, y: 0.5 } }), "POINTING", { x: -0.8, y: 0.5 }],
  ["sleeping", targetPose("SLEEPING", 2.0, { lookX: 0, lookY: 0, level: 0, reducedMotion: true }), "SLEEPING", null],
  ["confused", targetPose("CONFUSED", 2.0, { lookX: 0, lookY: 0, level: 0, reducedMotion: true }), "CONFUSED", null],
  ["dome rest", REST, "IDLE", null, "dome"],
  ["dome cursor top-left", targetPose("IDLE", 1.0, { lookX: -1, lookY: 1, level: 0, reducedMotion: true }), "TRACKING", null, "dome"],
  ["dome cursor bottom-right", targetPose("IDLE", 1.0, { lookX: 1, lookY: -1, level: 0, reducedMotion: true }), "TRACKING", null, "dome"],
  ["dome pointing down-right", targetPose("IDLE", 1.0, { lookX: 0, lookY: 0, level: 0, reducedMotion: true, point: { x: 0.6, y: -0.8 } }), "POINTING", { x: 0.6, y: -0.8 }, "dome"],
];
spec.face2dTestVectors = cases.map(([name, pose, expression, point, composition = "full"]) => {
  const pr: any = {}; for (const k of Object.keys(pose)) pr[k] = r6(pose[k]);
  return { name, pose: pr, expression, point, composition, geometry: faceGeometry(pr, expression as any, point, composition as any) };
});
spec.fitTestVectors = [[390, 844], [844, 390], [932, 430], [430, 932], [520, 640], [1200, 800], [560, 320]].map(([w, h]) => ({ w, h, ...fitFace(w, h) }));
const ctxs = [
  ["IDLE", false, false, null], ["IDLE", true, false, null], ["IDLE", true, true, null], ["EXECUTING", false, false, "research"],
  ["EXECUTING", false, false, "apps"], ["THINKING", false, false, "research"], ["SLEEPING", true, true, null],
  ["LISTENING", true, false, null], ["SUCCESS", false, true, null], ["DISCONNECTED", false, false, null], ["ERROR", false, true, null],
] as const;
spec.expressionTestVectors = ctxs.map(([state, cursorLive, pointing, taskKind]) => ({ state, cursorLive, pointing, taskKind,
  expression: expressionFor(state as any, { cursorLive, pointing, taskKind }) }));
fs.writeFileSync(p, JSON.stringify(spec, null, 2) + "\n");
console.log(JSON.stringify(spec.face2dTestVectors[1].geometry), JSON.stringify(spec.fitTestVectors[1]));
