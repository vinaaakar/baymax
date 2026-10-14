// 3D creation studio: prompt -> validated GLB, viewer, conversational edits, material/transform edits, undo, export.
import { useEffect, useMemo, useState } from "react";
import { currentClient } from "../../api/client";
import { useStore } from "../../api/store";
import ModelViewer, { type ViewerStats, type ViewName } from "../../components/ModelViewer";
import type { Artifact, Task } from "../../shared/protocol";
import { runTool, TaskOutcome, useApi } from "./shared";

interface ModelInfo {
  artifact: Artifact; validation: { ok: boolean; errors: string[]; stats: any }; objectType: string; title: string; version: number;
  params: Record<string, any>; detail: number; approximation: boolean; notes: string[];
  parts: { name: string; role: string; material: string }[]; materials: Record<string, { color: string; metallic: number; roughness: number; opacity: number }>;
}

export default function Create3D() {
  const versions = useStore((s) => s.modelVersion);
  const modelView = useStore((s) => s.modelView);
  const models = useApi<{ artifacts: Artifact[] }>("/api/artifacts?kind=model3d", [JSON.stringify(versions)]);
  const catalog = useApi<{ types: string[] }>("/api/models3d/catalog");
  const [selected, setSelected] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("a red sports car");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<Task | null>(null);
  const [stats, setStats] = useState<ViewerStats | null>(null);
  const [viewErr, setViewErr] = useState<string | null>(null);
  const [view, setView] = useState<{ name: ViewName; nonce: number } | null>(null);
  const info = useApi<{ model: ModelInfo; versions: number[] }>(selected ? `/api/artifacts/${selected}` : null, [selected ? versions[selected] : 0]);

  useEffect(() => {
    if (!selected && models.data?.artifacts.length) setSelected(models.data.artifacts[0].id);
  }, [models.data]);
  useEffect(() => {
    if (modelView && modelView.artifactId === selected) setView({ name: modelView.view as ViewName, nonce: modelView.nonce });
  }, [modelView?.nonce]);

  const c = currentClient();
  const url = useMemo(() => (selected && c ? `${c.fileUrl(selected)}&v=${versions[selected] ?? info.data?.model.version ?? 0}` : null),
    [selected, versions[selected ?? ""], info.data?.model.version]);

  const exec = async (fn: () => Promise<Task>) => {
    setBusy(true);
    try {
      const t = await fn();
      setLast(t);
      const art = t.result?.artifacts?.find((a) => a.kind === "model3d");
      if (art) setSelected(art.id);
      const v = t.result?.data?.view;
      if (v) setView({ name: v, nonce: Date.now() });
      models.reload();
      info.reload();
    } catch (e) {
      setLast({ title: "Error", status: "FAILED", error: (e as Error).message } as Task);
    } finally {
      setBusy(false);
    }
  };

  const create = () => exec(() => runTool("creation3d.create", { prompt, allow_approximation: /approx/i.test(prompt) }, { wait: true, title: `Create ${prompt}` }));
  const modify = (ops?: unknown[]) => selected && exec(() => runTool("creation3d.modify", ops ? { artifact_id: selected, operations: ops } :
    { artifact_id: selected, instruction }, { wait: true, title: ops ? "Edit model" : instruction }));
  const undo = async () => {
    if (!selected || !c) return;
    try {
      await c.post(`/api/models3d/${selected}/undo`);
      info.reload();
    } catch (e) {
      setLast({ title: "Undo", status: "FAILED", error: (e as Error).message } as Task);
    }
  };
  const m = info.data?.model;
  const roles = m ? [...new Set(m.parts.map((p) => p.role))] : [];

  return (
    <>
      <h1>3D Creation</h1>
      <form className="row" onSubmit={(e) => { e.preventDefault(); create(); }}>
        <input className="input" value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Describe a 3D object" placeholder="e.g. a three-storey house with a garage" />
        <button className="btn primary" disabled={busy}>{busy ? "Working…" : "Create"}</button>
      </form>
      <p className="muted" style={{ fontSize: 12 }}>Supported objects: {catalog.data?.types.join(", ") ?? "…"}. Other objects are refused (or, if you ask for an “approximate” one and a local model is running, built as a labelled primitive approximation). Outputs are procedural blockouts, not photoreal assets.</p>
      <TaskOutcome task={last} />
      <div className="split" style={{ marginTop: 12 }}>
        <div>
          <ModelViewer url={url} view={view} onLoaded={(s) => { setStats(s); setViewErr(null); }} onError={setViewErr} />
          {viewErr && <p style={{ color: "var(--err)" }}>{viewErr}</p>}
          <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); modify(); setInstruction(""); }}>
            <input className="input" value={instruction} onChange={(e) => setInstruction(e.target.value)} aria-label="Modify the model"
              placeholder="Make the body red · Increase the wheel size · More detailed · View from the top · Export as GLB" />
            <button className="btn" disabled={busy || !selected || !instruction.trim()}>Apply</button>
            <button type="button" className="btn" onClick={undo} disabled={!selected || (info.data?.versions.length ?? 0) < 2}>Undo</button>
            <button type="button" className="btn" onClick={() => modify([{ op: "export", format: "glb" }])} disabled={!selected || busy}>Export GLB</button>
          </form>
        </div>
        <div className="card">
          {!m ? <p className="muted">No model selected.</p> : (
            <>
              <div className="row"><strong>{m.title}</strong><span className="spacer" />
                <span className={`tag ${m.validation.ok ? "ok" : "err"}`}>{m.validation.ok ? "GLB validated" : "invalid"}</span></div>
              <p className="muted" style={{ fontSize: 12 }}>{m.objectType} · v{m.version} · detail {m.detail} · {m.validation.stats.triangles} triangles · {m.parts.length} parts
                {stats && ` · viewer loaded ${stats.meshes} meshes`}{m.approximation && " · APPROXIMATION"}</p>
              {m.notes.map((n, i) => <p key={i} className="muted" style={{ fontSize: 12 }}>{n}</p>)}
              <h2>Materials</h2>
              {roles.map((role) => {
                const part = m.parts.find((p) => p.role === role)!;
                const mat = m.materials[part.material];
                return (
                  <div className="row" key={role} style={{ marginBottom: 6 }}>
                    <span style={{ width: 110 }}>{role}</span>
                    <input type="color" aria-label={`${role} colour`} value={mat?.color ?? "#ffffff"}
                      onChange={(e) => modify([{ op: "color", target: role, color: e.target.value }])} />
                    <button className="btn small" onClick={() => modify([{ op: "transform", target: role, scale: [1.15, 1.15, 1.15] }])} title="Scale up">+</button>
                    <button className="btn small" onClick={() => modify([{ op: "transform", target: role, scale: [0.87, 0.87, 0.87] }])} title="Scale down">−</button>
                    <button className="btn small" onClick={() => modify([{ op: "material", target: role, props: { metallic: 0.9, roughness: 0.2 } }])}>metal</button>
                  </div>
                );
              })}
              <h2>Parameters</h2>
              <table><tbody>{Object.entries(m.params).filter(([k]) => k !== "parts").map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td className="mono">{String(v)}</td></tr>)}</tbody></table>
              <h2>Models</h2>
              <select className="input" value={selected ?? ""} onChange={(e) => setSelected(e.target.value)} aria-label="Select model">
                {models.data?.artifacts.map((a) => <option key={a.id} value={a.id}>{a.title} (v{a.meta.version})</option>)}
              </select>
            </>
          )}
        </div>
      </div>
    </>
  );
}
