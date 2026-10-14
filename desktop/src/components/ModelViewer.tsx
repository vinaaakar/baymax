// GLB viewer for generated models: orbit controls, camera presets, lighting, and reload on change.
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export type ViewName = "front" | "back" | "side" | "left" | "right" | "top" | "bottom" | "iso";

export interface ViewerStats { meshes: number; triangles: number; size: [number, number, number] }

export default function ModelViewer({ url, view, onLoaded, onError }: {
  url: string | null; view?: { name: ViewName; nonce: number } | null;
  onLoaded?: (stats: ViewerStats) => void; onError?: (msg: string) => void;
}) {
  const mount = useRef<HTMLDivElement>(null);
  const api = useRef<{ load: (u: string) => void; setView: (v: ViewName) => void } | null>(null);
  const [wireframe, setWireframe] = useState(false);
  const wf = useRef(false);
  wf.current = wireframe;

  useEffect(() => {
    const el = mount.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      onError?.("WebGL is not available in this window");
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.shadowMap.enabled = true;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 5000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight("#ffffff", "#3a0a14", 1.1));
    const key = new THREE.DirectionalLight("#ffffff", 2.2);
    key.castShadow = true;
    scene.add(key);
    const rim = new THREE.DirectionalLight("#ff334f", 1.2);
    scene.add(rim);
    const grid = new THREE.GridHelper(10, 20, "#5a1a26", "#2a0d14");
    scene.add(grid);
    let model: THREE.Object3D | null = null;
    let radius = 1;
    const center = new THREE.Vector3();
    const loader = new GLTFLoader();

    const setView = (v: ViewName) => {
      const d = radius * 2.6;
      const dirs: Record<ViewName, [number, number, number]> = {
        front: [0, 0.15, 1], back: [0, 0.15, -1], side: [1, 0.15, 0], right: [1, 0.15, 0], left: [-1, 0.15, 0],
        top: [0.0001, 1, 0.0001], bottom: [0.0001, -1, 0.0001], iso: [1, 0.75, 1],
      };
      const dir = new THREE.Vector3(...dirs[v]).normalize();
      camera.position.copy(center).addScaledVector(dir, d);
      controls.target.copy(center);
      controls.update();
    };

    const load = (u: string) => {
      loader.load(u, (gltf) => {
        if (model) {
          scene.remove(model);
          model.traverse((o) => {
            const m = o as THREE.Mesh;
            m.geometry?.dispose();
            const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
            mats.forEach((mm) => mm.dispose());
          });
        }
        model = gltf.scene;
        let meshes = 0;
        let tris = 0;
        model.traverse((o) => {
          const m = o as THREE.Mesh;
          if (m.isMesh) {
            meshes++;
            m.castShadow = true;
            m.receiveShadow = true;
            const idx = m.geometry.index;
            tris += idx ? idx.count / 3 : m.geometry.attributes.position.count / 3;
          }
        });
        scene.add(model);
        const box = new THREE.Box3().setFromObject(model);
        const size = box.getSize(new THREE.Vector3());
        box.getCenter(center);
        radius = Math.max(size.x, size.y, size.z) * 0.6 || 1;
        grid.scale.setScalar(Math.max(0.05, radius / 2));
        grid.position.y = box.min.y;
        camera.near = radius / 100;
        camera.far = radius * 100;
        camera.updateProjectionMatrix();
        key.position.set(center.x + radius * 2, center.y + radius * 3, center.z + radius * 2);
        rim.position.set(center.x - radius * 2, center.y + radius, center.z - radius * 2);
        setView("iso");
        onLoaded?.({ meshes, triangles: Math.round(tris), size: [size.x, size.y, size.z] });
      }, undefined, (err) => onError?.(`Could not load model: ${(err as Error)?.message ?? err}`));
    };
    api.current = { load, setView };

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (document.hidden) return;
      controls.update();
      if (model) model.traverse((o) => {
        const m = o as THREE.Mesh;
        if (m.isMesh) (Array.isArray(m.material) ? m.material : [m.material]).forEach((mm) => ((mm as THREE.MeshStandardMaterial).wireframe = wf.current));
      });
      renderer.render(scene, camera);
    };
    tick();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  useEffect(() => {
    if (url) api.current?.load(url);
  }, [url]);
  useEffect(() => {
    if (view) api.current?.setView(view.name);
  }, [view?.nonce]);

  return (
    <div className="viewer" ref={mount}>
      <div className="overlay">
        {(["front", "side", "top", "iso"] as ViewName[]).map((v) => (
          <button key={v} className="btn small" onClick={() => api.current?.setView(v)}>{v}</button>
        ))}
        <button className="btn small" aria-pressed={wireframe} onClick={() => setWireframe(!wireframe)}>wireframe</button>
      </div>
    </div>
  );
}
