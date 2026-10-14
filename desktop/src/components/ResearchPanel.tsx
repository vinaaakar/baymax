// Slide-out research panel for the Mac companion: the question, live progress while a real research task runs,
// then the summary, key findings and clickable sources exactly as the backend fetched them. Nothing here is generated
// in the UI; if no research ran, the panel stays closed.
import { useEffect, useState } from "react";
import { useStore } from "../api/store";
import { useActiveTasks } from "./common";

export default function ResearchPanel() {
  const research = useStore((s) => s.research);
  const running = useActiveTasks().find((t) => t.kind === "research");
  const [dismissed, setDismissed] = useState<string | null>(null);
  const key = running?.taskId ?? research?.taskId ?? null;
  useEffect(() => { if (running) setDismissed(null); }, [running?.taskId]);
  if (!key || dismissed === key) return null;
  if (!running && !research) return null;

  return (
    <aside className="research-panel no-drag" role="complementary" aria-label="Research results">
      <header className="research-head">
        <strong>Research</strong>
        <span className="spacer" />
        <button className="btn small" onClick={() => setDismissed(key)} aria-label="Close research panel">✕</button>
      </header>
      {running ? (
        <section>
          <p className="research-q">{running.title.replace(/^Research:\s*/i, "")}</p>
          <p className="muted">{running.step || running.detail || "Working…"}</p>
          <div className="progress"><div style={{ width: `${Math.round((running.progress ?? 0.05) * 100)}%` }} /></div>
        </section>
      ) : research && (
        <section>
          <p className="research-q">{research.query}</p>
          <h4>Summary <span className="muted">({research.summaryKind || "summary"})</span></h4>
          <p className="research-summary">{research.summary || "No summary was produced."}</p>
          {research.findings.length > 0 && (
            <>
              <h4>Key findings</h4>
              <ul className="research-findings">
                {research.findings.map((f, i) => <li key={i}>{f.text} <span className="muted">[{f.source}]</span></li>)}
              </ul>
            </>
          )}
          <h4>Sources</h4>
          <ol className="research-sources">
            {research.sources.map((s, i) => (
              <li key={i}><a href={s.url} target="_blank" rel="noreferrer" title={s.url}>{s.title}</a></li>
            ))}
          </ol>
          {research.limitations.length > 0 && (
            <>
              <h4>Limitations</h4>
              <ul className="research-limits muted">{research.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </>
          )}
        </section>
      )}
    </aside>
  );
}
