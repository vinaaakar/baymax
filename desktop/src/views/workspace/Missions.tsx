// Missions: every task with its real lifecycle, plan steps, progress, approval and event log.
import { useState } from "react";
import { currentClient } from "../../api/client";
import { useStore } from "../../api/store";
import { ApprovalBanners, ProgressBar, statusTag } from "../../components/common";
import { isTerminal, type Task } from "../../shared/protocol";
import { fmtTime, useApi } from "./shared";

export default function Missions() {
  const live = useStore((s) => s.tasks);
  const revision = Object.values(live).reduce((n, t) => n + t.updatedAt, 0);
  const { data } = useApi<{ tasks: Task[] }>("/api/tasks?limit=100", [Math.round(revision)]);
  const [sel, setSel] = useState<string | null>(null);
  const detail = useApi<{ task: Task; events: { ts: number; kind: string; data: any }[] }>(sel ? `/api/tasks/${sel}` : null, [Math.round(revision)]);
  const tasks = data?.tasks ?? [];
  return (
    <>
      <h1>Missions</h1>
      <ApprovalBanners />
      <div className="split" style={{ marginTop: 10 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Task</th><th>Status</th><th>Progress</th><th>Source</th><th>Started</th><th /></tr></thead>
            <tbody>
              {tasks.length === 0 && <tr><td colSpan={6} className="muted">No tasks yet.</td></tr>}
              {tasks.map((t) => {
                const lv = live[t.id];
                const status = (lv?.status ?? t.status) as Task["status"];
                return (
                  <tr key={t.id} onClick={() => setSel(t.id)} style={{ cursor: "pointer", background: sel === t.id ? "#2a0f16" : undefined }}>
                    <td>{t.title}<div className="muted mono">{t.id}</div></td>
                    <td>{statusTag(status, lv?.verified ?? t.verified)}</td>
                    <td style={{ minWidth: 90 }}>{isTerminal(status) ? "—" : <ProgressBar value={lv?.progress ?? t.progress ?? undefined} />}</td>
                    <td className="muted">{t.source.startsWith("device:") ? "iPhone" : t.source}</td>
                    <td className="muted">{fmtTime(t.created_at)}</td>
                    <td>{!isTerminal(status) && <button className="btn small" onClick={(e) => { e.stopPropagation(); currentClient()?.post(`/api/tasks/${t.id}/cancel`); }}>Cancel</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="card">
          {!detail.data ? <p className="muted">Select a task to inspect its plan, tool calls and verification.</p> : (
            <>
              <h2 style={{ marginTop: 0 }}>{detail.data.task.title}</h2>
              <p className="mono muted">{detail.data.task.request}</p>
              <h2>Plan</h2>
              <ol>{detail.data.task.plan.map((s, i) => <li key={i}><span className="mono">{s.tool}</span> — {s.status}{s.summary ? `: ${s.summary}` : ""}</li>)}</ol>
              {detail.data.task.error && <><h2>Error</h2><p style={{ color: "var(--err)" }}>{detail.data.task.error}</p></>}
              {detail.data.task.result && <><h2>Result</h2><p>{detail.data.task.result.summary}</p>
                <p className="muted">Verification: {detail.data.task.result.verification || "none"} · verified: {String(detail.data.task.verified)}</p></>}
              <h2>Event log</h2>
              <table><tbody>{detail.data.events.map((e, i) => (
                <tr key={i}><td className="muted mono">{new Date(e.ts * 1000).toLocaleTimeString()}</td><td>{e.kind}</td><td className="mono">{JSON.stringify(e.data).slice(0, 160)}</td></tr>
              ))}</tbody></table>
            </>
          )}
        </div>
      </div>
    </>
  );
}
