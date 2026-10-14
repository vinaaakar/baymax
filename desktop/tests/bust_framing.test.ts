import * as THREE from "three";
import { applyPose, buildBaymax } from "../src/character/baymaxModel";
import { REST, targetPose } from "../src/character/rig";
import { bustCamera, VISUAL_SPEC } from "../src/shared/framing";

describe("head-only composition (design/baymax-visual-spec.json)", () => {
  it("framing matches the shared test vectors used by the iPhone port", () => {
    for (const v of VISUAL_SPEC.framingTestVectors) {
      const f = bustCamera(v.aspect);
      expect(f.halfHeight).toBeCloseTo(v.halfHeight, 5);
      expect(f.centerY).toBeCloseTo(v.centerY, 5);
      expect(f.distance).toBeCloseTo(v.distance, 5);
    }
  });

  it("never cuts the head on any window shape", () => {
    const F = VISUAL_SPEC.framing;
    for (const aspect of [0.4, 0.46, 0.56, 0.68, 0.75, 1, 1.33, 1.78, 2.4, 3]) {
      const f = bustCamera(aspect);
      expect(f.centerY + f.halfHeight).toBeGreaterThanOrEqual(F.headTop + F.topMargin - 1e-9);
      expect(f.halfHeight).toBeLessThanOrEqual(F.maxHalfHeight);
    }
  });

  it("models only the head and a small collar: no torso, arms, legs or floor", () => {
    const rig = buildBaymax({ segments: 16 });
    const names: string[] = [];
    rig.root.traverse((o) => names.push(o.name));
    expect(names.some((n) => /leg|foot|feet|seam|floor/i.test(n))).toBe(false);
    let armMeshes = 0;
    for (const pivot of [rig.armL, rig.armR]) pivot.traverse((o) => { if ((o as THREE.Mesh).isMesh) armMeshes++; });
    expect(armMeshes).toBe(0);
    for (const state of ["IDLE", "LISTENING", "SUCCESS", "CONCERNED", "SLEEPING"] as const) {
      applyPose(rig, state === "IDLE" ? REST : targetPose(state, 1.3, { lookX: 0, lookY: 0, level: 0.5, reducedMotion: false }));
      rig.root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(rig.root);
      expect(box.min.y).toBeGreaterThan(1.4); // only head + collar (the old full figure reached y = 0)
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
    const share = (2 * VISUAL_SPEC.avatar.head.radii[0]) / (2 * halfW);
    expect(share).toBeGreaterThan(0.4); // the face is the focus...
    expect(share).toBeLessThan(0.75); // ...with breathing room, not enormous
  });
});
