// Compact floating companion window (Mac). Simple by design; details live in the workspace.
import { useEffect, useState } from "react";
import { useStore } from "../api/store";
import BaymaxCharacter, { describeState } from "../character/BaymaxCharacter";
import { ApprovalBanners, ChatInput, ConnectionPill, EStopButton, MicButton, ProgressBar, useActiveTasks, useClient, useVoice } from "../components/common";
import type { CharacterState } from "../shared/protocol";

export function useCursorLook(): { x: number; y: number } | null {
  const [look, setLook] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    if (window.baymax?.onCursor) return window.baymax.onCursor(setLook);
    const onMove = (e: MouseEvent) => setLook({ x: (e.clientX / innerWidth) * 2 - 1, y: -((e.clientY / innerHeight) * 2 - 1) });
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, []);
  return look;
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
  const look = useCursorLook();
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
    <div className={`stage ${state === "SLEEPING" || state === "DISCONNECTED" ? "dim" : ""}`}>
      <div className="topbar drag">
        <ConnectionPill compact />
        <span className="spacer" />
        <button className="btn small no-drag" onClick={() => window.baymax?.openWindow("workspace")} title="Open engineering workspace" aria-label="Open engineering workspace">⌘</button>
      </div>
      <BaymaxCharacter state={state} level={voice.listening ? voice.level : 0} look={look} onClick={() => sleeping && toggleSleep()} />
      <span className="sr-only" role="status" aria-live="polite">BAYMAX is {describeState(state)}</span>
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
