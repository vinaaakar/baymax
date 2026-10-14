// Compact floating companion window (Mac). Simple by design; details live in the workspace.
import { useEffect, useRef, useState } from "react";
import { useStore } from "../api/store";
import BaymaxCharacter, { describeState } from "../character/BaymaxCharacter";
import ResearchPanel from "../components/ResearchPanel";
import { ApprovalBanners, ChatInput, ConnectionPill, EStopButton, MicButton, ProgressBar, useActiveTasks, useClient, useVoice } from "../components/common";
import { DEFAULT_GAZE } from "../shared/gaze";
import type { CharacterState } from "../shared/protocol";

/** Real cursor direction plus whether samples are arriving right now (drives the TRACKING expression). */
export function useCursorLook(): { look: { x: number; y: number } | null; live: boolean } {
  const [look, setLook] = useState<{ x: number; y: number } | null>(null);
  const [live, setLive] = useState(false);
  const lastAt = useRef(0);
  useEffect(() => {
    const on = (v: { x: number; y: number }) => {
      lastAt.current = performance.now();
      setLook(v);
      setLive(true);
    };
    const check = setInterval(() => setLive(performance.now() - lastAt.current < DEFAULT_GAZE.staleMs), 500);
    let off: () => void;
    if (window.baymax?.onCursor) off = window.baymax.onCursor(on);
    else {
      const onMove = (e: MouseEvent) => on({ x: (e.clientX / innerWidth) * 2 - 1, y: -((e.clientY / innerHeight) * 2 - 1) });
      window.addEventListener("mousemove", onMove);
      off = () => window.removeEventListener("mousemove", onMove);
    }
    return () => {
      off();
      clearInterval(check);
    };
  }, []);
  return { look, live };
}

/** Screen targets found by "look at the … button": the Mac companion looks and points at them for a while. */
export function useGazeTarget(): { point: { x: number; y: number } | null; label: string } {
  const [g, setG] = useState<{ point: { x: number; y: number } | null; label: string }>({ point: null, label: "" });
  useEffect(() => {
    if (!window.baymax?.onGaze) return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const off = window.baymax.onGaze((v) => {
      setG({ point: { x: v.x, y: v.y }, label: v.label });
      if (t) clearTimeout(t);
      t = setTimeout(() => setG({ point: null, label: "" }), Math.max(500, Math.min(v.durationMs, 15000)));
    });
    return () => {
      off();
      if (t) clearTimeout(t);
    };
  }, []);
  return g;
}

export function useDisplayedState(): CharacterState {
  const conn = useStore((s) => s.connection);
  const character = useStore((s) => s.character);
  return conn === "connected" ? character : "DISCONNECTED";
}

export default function Companion() {
  const client = useClient();
  const state = useDisplayedState();
  const sleeping = useStore((s) => s.sleeping);
  const estop = useStore((s) => s.estop);
  const lastAssistant = useStore((s) => [...s.messages].reverse().find((m) => m.role === "assistant"));
  const voice = useVoice();
  const cursor = useCursorLook();
  const gaze = useGazeTarget();
  const active = useActiveTasks();
  const [showCaption, setShowCaption] = useState(false);

  useEffect(() => {
    if (!lastAssistant) return;
    setShowCaption(true);
    const t = setTimeout(() => setShowCaption(false), 9000);
    return () => clearTimeout(t);
  }, [lastAssistant?.id]);

  const toggleSleep = () => client?.post("/api/sleep", { sleeping: !sleeping });
  const task = active[0];
  return (
    // Like the earlier Mac companion: status pills and ⌘ on top, Baymax (2D head and body) filling the window behind,
    // mic / text / Send and Sleep / Conversation / STOP along the bottom.
    <div className={`stage companion-layout bust-mode ${state === "SLEEPING" || state === "DISCONNECTED" ? "dim" : ""}`}
      onContextMenu={(e) => { e.preventDefault(); window.baymax?.showCompanionMenu?.(); }}>
      <div className="topbar drag">
        <ConnectionPill compact />
        <span className="spacer" />
        <button className="btn small no-drag" onClick={() => window.baymax?.openWindow("workspace")} title="Open engineering workspace" aria-label="Open engineering workspace">⌘</button>
      </div>
      {/* Baymax fills the window; the bars sit over the lower body, as in the earlier Mac companion. */}
      <div className="face-region">
        <BaymaxCharacter state={state} level={voice.listening ? voice.level : 0} look={cursor.look} cursorLive={cursor.live && !sleeping}
          point={gaze.point} taskKind={task?.kind} progress={task?.progress} composition="bust" onClick={() => sleeping && toggleSleep()} />
      </div>
      <span className="sr-only" role="status" aria-live="polite">BAYMAX is {describeState(state)}</span>
      <ResearchPanel />
      <div className="controls">
        {showCaption && lastAssistant && state !== "SLEEPING" && (
          <div className="speech-caption" aria-hidden="true" title={lastAssistant.text}>{lastAssistant.text}</div>
        )}
        {estop.engaged && <div className="banner estop" role="alert">Emergency Stop is engaged — controlled actions are blocked.</div>}
        <ApprovalBanners />
        {task && (
          <button className="task-chip no-drag" onClick={() => window.baymax?.openWindow("workspace")} title="Inspect task">
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.title}{task.detail ? ` — ${task.detail}` : ""}</span>
            <ProgressBar value={task.progress} />
            <span className="btn small" role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); client?.post(`/api/tasks/${task.taskId}/cancel`); }}>Cancel</span>
          </button>
        )}
        {voice.error && <div className="banner" role="alert">{voice.error}</div>}
        <div className="row">
          <MicButton voice={voice} disabled={sleeping || state === "DISCONNECTED"} />
          <ChatInput placeholder={sleeping ? "Asleep — wake me first" : "Ask BAYMAX…"} disabled={sleeping || state === "DISCONNECTED"} />
        </div>
        <div className="row">
          <button className="btn small no-drag" onClick={toggleSleep}>{sleeping ? "Wake" : "Sleep"}</button>
          <button className="btn small no-drag" onClick={() => window.baymax?.openWindow("conversation")}>Conversation</button>
          <span className="spacer" />
          <EStopButton compact />
        </div>
      </div>
    </div>
  );
}
