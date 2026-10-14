// Procedural, original Baymax-inspired healthcare robot (not an official Disney asset).
// Units: ~1 unit = 1 m; the character is ~2.1 units tall, standing on y = 0, facing +Z.
import * as THREE from "three";
import type { Pose } from "./rig";

export const PALETTE = {
  background: "#180509",
  crimson: "#B5122A",
  glow: "#FF334F",
  shadow: "#080305",
  white: "#F5F5F2",
  face: "#121214",
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

// Torso: wide, pear-shaped, slightly squashed front-to-back (marshmallow / inflatable look).
const TORSO_PROFILE: [number, number][] = [
  [0.0, 0.2], [0.34, 0.22], [0.58, 0.34], [0.72, 0.58], [0.76, 0.86], [0.72, 1.12], [0.62, 1.36], [0.48, 1.56], [0.3, 1.7], [0.16, 1.76], [0.0, 1.78],
];
// Arms: long, soft, slightly tapering "balloon" segments that hang from the shoulder pivot (y = 0) down to -0.95.
const ARM_PROFILE: [number, number][] = [
  [0.0, -0.98], [0.11, -0.96], [0.16, -0.86], [0.17, -0.6], [0.18, -0.3], [0.19, -0.08], [0.14, 0.04], [0.0, 0.07],
];
const LEG_PROFILE: [number, number][] = [
  [0.0, 0.0], [0.2, 0.0], [0.25, 0.04], [0.25, 0.2], [0.22, 0.34], [0.0, 0.38],
];

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
  torso.scale.set(1, 1, 0.82);
  torso.castShadow = true;
  torso.receiveShadow = true;
  body.add(torso);

  // A soft belly seam ring adds the inflatable "panel" feel without clutter.
  const seam = new THREE.Mesh(track(new THREE.TorusGeometry(0.735, 0.0045, 8, seg)), new THREE.MeshStandardMaterial({ color: "#e6e6e0", roughness: 0.6 }));
  seam.rotation.x = Math.PI / 2;
  seam.position.y = 1.02;
  seam.scale.set(1, 0.82, 1);
  body.add(seam);

  for (const side of [-1, 1]) {
    const leg = new THREE.Mesh(track(new THREE.LatheGeometry(smoothProfile(LEG_PROFILE, 24), Math.max(24, seg / 2))), skin);
    leg.name = side < 0 ? "legL" : "legR";
    leg.position.set(side * 0.27, 0, 0.02);
    leg.scale.set(1, 1, 1.15);
    leg.castShadow = true;
    root.add(leg);
  }

  // Head pivot sits at the neck so head motion rotates naturally.
  const headPivot = new THREE.Group();
  headPivot.name = "headPivot";
  headPivot.position.set(0, 1.71, 0.03);
  body.add(headPivot);
  const HEAD = { rx: 0.26, ry: 0.185, rz: 0.22, y: 0.135, z: 0.04 };
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
  const eyeY = HEAD.y + 0.012;
  const eyeX = 0.085;
  const eyeGeo = track(new THREE.SphereGeometry(0.03, 24, 16));
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
  const eyeLine = new THREE.Mesh(track(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(linePts), 32, 0.0045, 8, false)), face);
  eyeLine.name = "eyeLine";
  eyes.add(eyeLine);

  const makeArm = (side: -1 | 1) => {
    const pivot = new THREE.Group();
    pivot.name = side < 0 ? "armL" : "armR";
    pivot.position.set(side * 0.56, 1.4, 0.0);
    const arm = new THREE.Mesh(track(new THREE.LatheGeometry(smoothProfile(ARM_PROFILE, 40), Math.max(32, seg / 2))), skin);
    arm.scale.set(1, 1, 0.92);
    arm.castShadow = true;
    pivot.add(arm);
    const hand = new THREE.Mesh(track(new THREE.SphereGeometry(1, 32, 20)), skin);
    hand.scale.set(0.12, 0.15, 0.1);
    hand.position.set(side * 0.005, -0.98, 0.02);
    hand.castShadow = true;
    pivot.add(hand);
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
  rig.headPivot.position.y = 1.71 * p.breathe;
  rig.headPivot.rotation.set(p.headPitch, p.headYaw, p.headRoll, "YXZ");
  rig.eyes.position.set(p.eyeX * 0.012, p.eyeY * 0.008, 0);
  const open = Math.max(0.04, Math.min(1, p.eyeOpen));
  rig.eyeL.scale.y = 1.05 * open;
  rig.eyeR.scale.y = 1.05 * open;
  rig.armL.rotation.set(p.armLFwd * -1, 0, -p.armL);
  rig.armR.rotation.set(p.armRFwd * -1, 0, p.armR);
}
