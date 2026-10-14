// Procedural, original Baymax-inspired healthcare robot (not an official Disney asset).
// Head composition: only the head and a small rounded neck/shoulder collar are modelled
// (design/baymax-visual-spec.json -> avatar). No torso, arms, legs or floor exist in the scene.
// Units: ~1 unit = 1 m, +Y up, facing +Z. The SceneKit port (ios/.../BaymaxNode.swift) reads the same numbers.
import * as THREE from "three";
import { VISUAL_SPEC } from "../shared/framing";
import type { Pose } from "./rig";

const AV = VISUAL_SPEC.avatar;

const C = VISUAL_SPEC.colors;
export const PALETTE = {
  background: C.background,
  crimson: C.accent,
  glow: C.highlight,
  shadow: C.surface,
  white: C.skin,
  face: C.face,
};

export interface BaymaxRig {
  root: THREE.Group;
  body: THREE.Group;
  torso: THREE.Mesh;
  headPivot: THREE.Group;
  eyes: THREE.Group;
  eyeL: THREE.Mesh;
  eyeR: THREE.Mesh;
  eyeLine: THREE.Mesh;
  armL: THREE.Group;
  armR: THREE.Group;
  materials: { skin: THREE.MeshPhysicalMaterial; face: THREE.MeshStandardMaterial };
  dispose: () => void;
}

/** Smooth profile through control points (Catmull-Rom), as Vector2(radius, y) for LatheGeometry. */
function smoothProfile(points: [number, number][], samples = 64): THREE.Vector2[] {
  const curve = new THREE.CatmullRomCurve3(points.map(([r, y]) => new THREE.Vector3(r, y, 0)), false, "centripetal");
  return curve.getPoints(samples).map((p) => new THREE.Vector2(Math.max(0, p.x), p.y));
}

// Small rounded neck/shoulder collar under the head (the figure ends here; nothing is hidden below it).
const TORSO_PROFILE = AV.torsoProfile as [number, number][];
// No arms in the head-only composition (empty profile): the arm pivots stay as empty groups so poses still apply.
const ARM_PROFILE = AV.armProfile as [number, number][];

export function buildBaymax(options: { segments?: number } = {}): BaymaxRig {
  const seg = options.segments ?? 64;
  const geometries: THREE.BufferGeometry[] = [];
  const track = <T extends THREE.BufferGeometry>(g: T): T => {
    geometries.push(g);
    return g;
  };

  const skin = new THREE.MeshPhysicalMaterial({
    color: PALETTE.white, roughness: 0.5, metalness: 0, clearcoat: 0.35, clearcoatRoughness: 0.45,
    sheen: 0.6, sheenColor: new THREE.Color("#ffffff"), sheenRoughness: 0.8,
  });
  const face = new THREE.MeshStandardMaterial({ color: PALETTE.face, roughness: 0.35, metalness: 0 });

  const root = new THREE.Group();
  root.name = "baymax";
  const body = new THREE.Group();
  body.name = "body";
  root.add(body);

  const torso = new THREE.Mesh(track(new THREE.LatheGeometry(smoothProfile(TORSO_PROFILE, 72), seg)), skin);
  torso.name = "torso";
  torso.scale.set(1, 1, AV.torsoDepthScale);
  torso.castShadow = true;
  torso.receiveShadow = true;
  body.add(torso);

  // Head pivot sits at the neck so head motion rotates naturally.
  const headPivot = new THREE.Group();
  headPivot.name = "headPivot";
  headPivot.position.set(AV.head.pivot[0], AV.head.pivot[1], AV.head.pivot[2]);
  body.add(headPivot);
  const HEAD = { rx: AV.head.radii[0], ry: AV.head.radii[1], rz: AV.head.radii[2], y: AV.head.center[1], z: AV.head.center[2] };
  const head = new THREE.Mesh(track(new THREE.SphereGeometry(1, seg, seg / 2)), skin);
  head.name = "head";
  head.scale.set(HEAD.rx, HEAD.ry, HEAD.rz);
  head.position.set(0, HEAD.y, HEAD.z);
  head.castShadow = true;
  headPivot.add(head);

  // Face: two eyes joined by a thin line that follows the curvature of the head.
  const eyes = new THREE.Group();
  eyes.name = "eyes";
  headPivot.add(eyes);
  const surfaceZ = (x: number, y: number) => HEAD.z + HEAD.rz * Math.sqrt(Math.max(0, 1 - (x / HEAD.rx) ** 2 - ((y - HEAD.y) / HEAD.ry) ** 2));
  const eyeY = HEAD.y + AV.eyes.yOffset;
  const eyeX = AV.eyes.x;
  const eyeGeo = track(new THREE.SphereGeometry(AV.eyes.radius, 24, 16));
  const eyeL = new THREE.Mesh(eyeGeo, face);
  const eyeR = new THREE.Mesh(eyeGeo, face);
  for (const [mesh, x] of [[eyeL, -eyeX], [eyeR, eyeX]] as const) {
    mesh.position.set(x, eyeY, surfaceZ(x, eyeY) - 0.006);
    mesh.scale.set(1, 1.05, 0.45);
    // orient flat against the surface
    mesh.lookAt(new THREE.Vector3(x * 2.2, eyeY, HEAD.z + 1));
    eyes.add(mesh);
  }
  const linePts: THREE.Vector3[] = [];
  for (let i = 0; i <= 24; i++) {
    const x = -eyeX + (2 * eyeX * i) / 24;
    linePts.push(new THREE.Vector3(x, eyeY, surfaceZ(x, eyeY) + 0.0015));
  }
  const eyeLine = new THREE.Mesh(track(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(linePts), 32, AV.eyes.lineRadius, 8, false)), face);
  eyeLine.name = "eyeLine";
  eyes.add(eyeLine);

  const makeArm = (side: -1 | 1) => {
    const pivot = new THREE.Group();
    pivot.name = side < 0 ? "armL" : "armR";
    pivot.position.set(side * AV.armPivot[0], AV.armPivot[1], AV.armPivot[2]);
    if (ARM_PROFILE.length > 1) {
      const arm = new THREE.Mesh(track(new THREE.LatheGeometry(smoothProfile(ARM_PROFILE, 40), Math.max(32, seg / 2))), skin);
      arm.scale.set(1, 1, AV.armDepthScale);
      arm.castShadow = true;
      pivot.add(arm);
    }
    body.add(pivot);
    return pivot;
  };
  const armL = makeArm(-1);
  const armR = makeArm(1);

  return {
    root, body, torso, headPivot, eyes, eyeL, eyeR, eyeLine, armL, armR, materials: { skin, face },
    dispose: () => {
      geometries.forEach((g) => g.dispose());
      skin.dispose();
      face.dispose();
    },
  };
}

/** Apply an animation pose to the rig. */
export function applyPose(rig: BaymaxRig, p: Pose): void {
  rig.body.position.y = p.bodyY;
  rig.body.rotation.x = p.lean;
  rig.body.rotation.z = p.sway;
  rig.torso.scale.y = p.breathe;
  rig.torso.scale.x = 1 + (1 - p.breathe) * -0.5; // volume-preserving-ish breathing
  rig.headPivot.position.y = AV.head.pivot[1] * p.breathe;
  rig.headPivot.rotation.set(p.headPitch, p.headYaw, p.headRoll, "YXZ");
  // Eyes visibly follow the gaze by rotating about the neck pivot, sliding along the curved face (not sinking into it).
  rig.eyes.rotation.set(-p.eyeY * AV.eyes.travel[1], p.eyeX * AV.eyes.travel[0], 0);
  const open = Math.max(0.04, Math.min(1, p.eyeOpen));
  rig.eyeL.scale.y = 1.05 * open;
  rig.eyeR.scale.y = 1.05 * open;
  rig.armL.rotation.set(p.armLFwd * -1, 0, -p.armL);
  rig.armR.rotation.set(p.armRFwd * -1, 0, p.armR);
}
