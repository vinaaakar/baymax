import { useCallback, useEffect, useState } from "react";
import { currentClient } from "../../api/client";
import type { Artifact, Task } from "../../shared/protocol";

export function useApi<T>(path: string | null, deps: unknown[] = []): { data: T | null; error: string | null; reload: () => void; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [n, setN] = useState(0);
  useEffect(() => {
    const c = currentClient();
    if (!c || !path) return;
    let alive = true;
    setLoading(true);
    c.get<T>(path).then((d) => alive && (setData(d), setError(null))).catch((e) => alive && setError(e.message)).finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, n, ...deps]);
  return { data, error, loading, reload: useCallback(() => setN((x) => x + 1), []) };
}

/** Run a tool through the same task lifecycle/policy as chat. */
export async function runTool(name: string, args: Record<string, unknown>, opts: { wait?: boolean; title?: string } = {}): Promise<Task> {
  const c = currentClient();
  if (!c) throw new Error("not connected");
  const r = await c.post<{ task: Task }>(`/api/tools/${name}`, { args, wait: opts.wait ?? false, title: opts.title, timeout: 300 });
  return r.task;
}

export function ArtifactLink({ a }: { a: Artifact }) {
  const c = currentClient();
  if (!c) return null;
  return <a href={c.fileUrl(a.id)} target="_blank" rel="noreferrer" style={{ color: "var(--glow)" }}>{a.title}</a>;
}

export function TaskOutcome({ task }: { task: Task | null }) {
  if (!task) return null;
  const r = task.result;
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="row"><strong>{task.title}</strong><span className="spacer" />
        <span className={`tag ${task.status === "COMPLETED" ? (task.verified ? "ok" : "warn") : task.status === "FAILED" ? "err" : ""}`}>
          {task.status === "COMPLETED" ? (task.verified ? "verified" : "completed, unverified") : task.status.toLowerCase()}</span></div>
      <p style={{ whiteSpace: "pre-wrap" }}>{r?.summary || task.error}</p>
      {r?.verification && <p className="muted" style={{ fontSize: 12 }}>Verification: {r.verification}</p>}
      {r?.untrusted && <p className="muted" style={{ fontSize: 12 }}>Contains external/untrusted content.</p>}
    </div>
  );
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}
