import * as THREE from "three";
import { applyPose, buildBaymax } from "../src/character/baymaxModel";
import { REST, targetPose } from "../src/character/rig";
import { bustCamera, VISUAL_SPEC } from "../src/shared/framing";

describe("bust composition (design/baymax-visual-spec.json)", () => {
  it("framing matches the shared test vectors used by the iPhone port", () => {
    for (const v of VISUAL_SPEC.framingTestVectors) {
      const f = bustCamera(v.aspect);
      expect(f.halfHeight).toBeCloseTo(v.halfHeight, 5);
      expect(f.centerY).toBeCloseTo(v.centerY, 5);
      expect(f.distance).toBeCloseTo(v.distance, 5);
    }
  });

  it("never cuts the head and never shows below the bust's base region on any window shape", () => {
    const F = VISUAL_SPEC.framing;
    for (const aspect of [0.4, 0.46, 0.56, 0.68, 0.75, 1, 1.33, 1.78, 2.4, 3]) {
      const f = bustCamera(aspect);
      expect(f.centerY + f.halfHeight).toBeGreaterThanOrEqual(F.headTop + F.topMargin - 1e-9);
      expect(f.halfHeight).toBeLessThanOrEqual(F.maxHalfHeight);
    }
  });

  it("models only the head and upper body: no legs, no feet, no abdomen below the bust base", () => {
    const rig = buildBaymax({ segments: 16 });
    const names: string[] = [];
    rig.root.traverse((o) => names.push(o.name));
    expect(names.some((n) => /leg|foot|feet|seam|floor/i.test(n))).toBe(false);
    for (const state of ["IDLE", "LISTENING", "SUCCESS", "CONCERNED", "SLEEPING"] as const) {
      applyPose(rig, state === "IDLE" ? REST : targetPose(state, 1.3, { lookX: 0, lookY: 0, level: 0.5, reducedMotion: false }));
      rig.root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(rig.root);
      expect(box.min.y).toBeGreaterThan(0.8); // the full figure reached y = 0 (feet)
      expect(box.max.y).toBeLessThan(2.15);
    }
    applyPose(rig, REST);
    rig.root.updateMatrixWorld(true);
    const head = new THREE.Box3().setFromObject(rig.headPivot);
    expect(head.max.y).toBeCloseTo(VISUAL_SPEC.framing.headTop, 1);
    rig.dispose();
  });

  it("the head is the visual focus: it fills a large share of the frame width on a phone-shaped view", () => {
    const f = bustCamera(0.46);
    const halfW = f.halfHeight * 0.46;
    expect((2 * VISUAL_SPEC.avatar.head.radii[0]) / (2 * halfW)).toBeGreaterThan(0.4);
  });
});
