// Images, research, simulation/telemetry and code tools.
import { useState } from "react";
import { currentClient } from "../../api/client";
import { useStore } from "../../api/store";
import LineChart from "../../components/LineChart";
import type { Artifact, Task } from "../../shared/protocol";
import { ArtifactLink, runTool, TaskOutcome, useApi } from "./shared";

function useRunner() {
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const run = async (name: string, args: Record<string, unknown>, title?: string) => {
    setBusy(true);
    try {
      setTask(await runTool(name, args, { wait: true, title }));
    } catch (e) {
      setTask({ title: title ?? name, status: "FAILED", error: (e as Error).message } as Task);
    } finally {
      setBusy(false);
    }
  };
  return { busy, task, run };
}

export function Images() {
  const status = useApi<{ images: { available: boolean; message: string; provider: string } }>("/api/status");
  const list = useApi<{ artifacts: Artifact[] }>("/api/artifacts?kind=image");
  const [prompt, setPrompt] = useState("");
  const r = useRunner();
  const img = status.data?.images;
  const c = currentClient();
  return (
    <>
      <h1>Images</h1>
      <div className="card">
        <div className="row"><strong>Generator</strong><span className="spacer" /><span className={`tag ${img?.available ? "ok" : "warn"}`}>{img?.available ? img.provider : "not available"}</span></div>
        <p className="muted">{img?.message}</p>
        <form className="row" onSubmit={(e) => { e.preventDefault(); r.run("image.generate", { prompt }, "Generate image").then(list.reload); }}>
          <input className="input" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe an image" aria-label="Image prompt" />
          <button className="btn primary" disabled={r.busy || !prompt.trim()}>Generate</button>
        </form>
        <p className="muted" style={{ fontSize: 12 }}>Each saved image is labelled generated, edited, reference or placeholder. Nothing is created when no generator is available.</p>
      </div>
      <TaskOutcome task={r.task} />
      <div className="grid" style={{ marginTop: 12 }}>
        {list.data?.artifacts.map((a) => (
          <div className="card" key={a.id}>
            {c && <img src={c.fileUrl(a.id)} alt={a.title} style={{ width: "100%", borderRadius: 10 }} />}
            <div className="row"><span>{a.title}</span><span className="spacer" /><span className="tag">{a.meta.kind}</span></div>
          </div>
        ))}
      </div>
    </>
  );
}

export function Research() {
  const status = useApi<{ research: { localOnly: boolean } }>("/api/status");
  const list = useApi<{ artifacts: Artifact[] }>("/api/artifacts?kind=research");
  const [q, setQ] = useState("");
  const r = useRunner();
  const md: string | undefined = r.task?.result?.data?.markdown;
  return (
    <>
      <h1>Research</h1>
      {status.data?.research.localOnly && <div className="banner">Local-only mode is on: web research is disabled. Change it in Settings.</div>}
      <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); r.run("research.web", { query: q }, `Research: ${q}`).then(list.reload); }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Research question" aria-label="Research question" />
        <button className="btn primary" disabled={r.busy || !q.trim()}>{r.busy ? "Researching…" : "Research"}</button>
      </form>
      <TaskOutcome task={r.task} />
      {md && <pre className="out" style={{ maxHeight: 480 }}>{md}</pre>}
      <h2>Saved reports</h2>
      <ul>{list.data?.artifacts.map((a) => <li key={a.id}><ArtifactLink a={a} /> <span className="muted">· {(a.meta.sources ?? []).length} sources</span></li>)}</ul>
    </>
  );
}

export function Simulations() {
  const telemetry = useStore((s) => s.telemetry);
  const list = useApi<{ artifacts: Artifact[] }>("/api/artifacts?kind=simulation", [telemetry?.artifactId]);
  const [model, setModel] = useState("projectile");
  const [request, setRequest] = useState("simulate a projectile at 30 m/s and 45 degrees");
  const r = useRunner();
  const t = telemetry;
  const keys = t ? Object.keys(t.series).filter((k) => k !== "t").slice(0, 3) : [];
  return (
    <>
      <h1>Simulations &amp; Telemetry</h1>
      <p className="muted">All values here come from software simulations. No physical device is connected; hardware control is disabled.</p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); r.run("simulation.run", { model, request }, `Simulation: ${model}`); }}>
        <select className="input" style={{ flex: "0 0 180px" }} value={model} onChange={(e) => setModel(e.target.value)} aria-label="Simulation model">
          {["projectile", "pendulum", "spring_mass", "dc_motor_pid", "drone_altitude"].map((m) => <option key={m}>{m}</option>)}
        </select>
        <input className="input" value={request} onChange={(e) => setRequest(e.target.value)} aria-label="Parameters in words" />
        <button className="btn primary" disabled={r.busy}>Run</button>
      </form>
      <TaskOutcome task={r.task} />
      {t && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="row"><strong>{t.title}</strong><span className="spacer" /><span className="tag warn">source: {t.source}</span></div>
          <LineChart x={t.series.t} series={Object.fromEntries(keys.map((k) => [k, t.series[k]]))} units={t.units} label={t.title} />
        </div>
      )}
      <h2>Saved runs</h2>
      <table><tbody>{list.data?.artifacts.map((a) => (
        <tr key={a.id}><td><ArtifactLink a={a} /></td><td className="mono">{JSON.stringify(a.meta.summary)}</td></tr>
      ))}</tbody></table>
    </>
  );
}

export function Code() {
  const [repo, setRepo] = useState(".");
  const [goal, setGoal] = useState("");
  const r = useRunner();
  const data = r.task?.result?.data;
  return (
    <>
      <h1>Software Engineering</h1>
      <p className="muted">Repositories are resolved inside the workspace. Paths outside it require approval. Edits are backed up, tests are really executed, and repair attempts are bounded.</p>
      <div className="card">
        <label className="field">Repository path<input className="input" value={repo} onChange={(e) => setRepo(e.target.value)} /></label>
        <label className="field" style={{ marginTop: 8 }}>Goal (optional — leave empty to just run the tests)
          <textarea className="input" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Fix the failing date parser test" /></label>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" disabled={r.busy} onClick={() => r.run("coding.inspect", { repo }, `Inspect ${repo}`)}>Inspect</button>
          <button className="btn primary" disabled={r.busy} onClick={() => r.run("coding.agent", { repo, goal }, goal ? `Coding: ${goal}` : `Run tests in ${repo}`)}>
            {r.busy ? "Running…" : goal ? "Run agent" : "Run tests"}</button>
        </div>
      </div>
      <TaskOutcome task={r.task} />
      {(data?.changes?.length ?? 0) > 0 && (
        <><h2>Changed files</h2><table><tbody>{data!.changes.map((c: any) => (
          <tr key={c.path}><td className="mono">{c.path}</td><td>{c.action}</td><td>+{c.added} / −{c.removed}</td><td className="muted mono">{c.backup ?? "new file"}</td></tr>
        ))}</tbody></table></>
      )}
      {data?.final && <><h2>Test output ({data.final.command} → exit {data.final.returncode})</h2><pre className="out">{data.final.outputTail}</pre></>}
    </>
  );
}
