// three.js renderer for the companion character. Frame-rate capped, paused when hidden,
// honours prefers-reduced-motion, and animates only from the real application state.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { CharacterState } from "../shared/protocol";
import { applyPose, buildBaymax, PALETTE } from "./baymaxModel";
import { blend, frameRateFor, REST, targetPose, type Pose } from "./rig";

export interface CharacterProps {
  state: CharacterState;
  level?: number;              // live audio level 0..1
  look?: { x: number; y: number } | null;
  reducedMotion?: boolean;
  quality?: "low" | "high";
  onClick?: () => void;
  framing?: "full" | "portrait";
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function BaymaxCharacter(props: CharacterProps) {
  const mount = useRef<HTMLDivElement>(null);
  const live = useRef(props);
  live.current = props;

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    } catch {
      el.dataset.webgl = "unavailable";
      return;
    }
    const high = (live.current.quality ?? "high") === "high";
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, high ? 2 : 1.25));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
    const framing = live.current.framing ?? "full";
    camera.position.set(0, framing === "portrait" ? 1.3 : 1.2, framing === "portrait" ? 6.4 : 7.2);
    camera.lookAt(0, framing === "portrait" ? 1.12 : 1.05, 0);

    // Lighting: warm key, soft fill, crimson rim from behind, hemisphere tinted by the red room.
    const hemi = new THREE.HemisphereLight("#fff4f0", PALETTE.crimson, 0.75);
    scene.add(hemi);
    const key = new THREE.DirectionalLight("#fff7ee", 2.1);
    key.position.set(2.5, 4.5, 4);
    key.castShadow = true;
    key.shadow.mapSize.set(high ? 1024 : 512, high ? 1024 : 512);
    key.shadow.radius = 6;
    key.shadow.camera.near = 1;
    key.shadow.camera.far = 15;
    scene.add(key);
    const fill = new THREE.DirectionalLight("#ffe3e6", 0.55);
    fill.position.set(-3, 2, 3);
    scene.add(fill);
    const rim = new THREE.PointLight(PALETTE.glow, 9, 9, 1.6);
    rim.position.set(0, 2.3, -1.6);
    scene.add(rim);

    const floor = new THREE.Mesh(new THREE.CircleGeometry(2.2, 48), new THREE.ShadowMaterial({ opacity: 0.38 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);

    const rig = buildBaymax({ segments: high ? 64 : 32 });
    scene.add(rig.root);

    let pose: Pose = { ...REST };
    let raf = 0;
    let last = performance.now();
    let lastFrame = 0;
    const t0 = last;

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / h;
      // keep the whole character in frame on narrow screens
      camera.fov = w / h < 0.62 ? 34 : 28;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const p = live.current;
      const reduced = p.reducedMotion ?? prefersReducedMotion();
      const fps = frameRateFor(p.state, reduced);
      if (now - lastFrame < 1000 / fps - 1) return;
      lastFrame = now;
      const dt = (now - last) / 1000;
      last = now;
      const target = targetPose(p.state, (now - t0) / 1000, {
        lookX: p.look?.x ?? 0, lookY: p.look?.y ?? 0, level: p.level ?? 0, reducedMotion: reduced,
      });
      pose = reduced && p.state !== "IDLE" ? blend(pose, target, dt, 14) : blend(pose, target, dt, 5);
      applyPose(rig, pose);
      rim.intensity = 4 + pose.glow * 14;
      const dim = 1 - pose.dim * 0.55;
      key.intensity = 2.1 * dim;
      hemi.intensity = 0.75 * dim;
      renderer.render(scene, camera);
      el.dataset.renderedState = p.state;
    };
    raf = requestAnimationFrame(frame);
    el.dataset.webgl = "ok";

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      rig.dispose();
      floor.geometry.dispose();
      (floor.material as THREE.Material).dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  const label = describeState(props.state);
  return (
    <div
      ref={mount}
      className="character-canvas"
      role="img"
      aria-label={`BAYMAX companion, ${label}`}
      onClick={props.onClick}
      data-state={props.state}
    />
  );
}

export function describeState(s: CharacterState): string {
  return {
    IDLE: "ready", LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking", EXECUTING: "working on a task",
    SUCCESS: "task verified", CONFUSED: "needs clarification", CONCERNED: "needs your attention", SLEEPING: "sleeping",
    ERROR: "something failed", DISCONNECTED: "disconnected",
  }[s];
}
