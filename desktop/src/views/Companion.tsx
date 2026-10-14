// Compact floating companion window (Mac). Simple by design; details live in the workspace.
import { useEffect, useRef, useState } from "react";
import { useStore } from "../api/store";
import BaymaxCharacter, { describeState } from "../character/BaymaxCharacter";
import { ApprovalBanners, ChatInput, useActiveTasks, useClient, useVoice } from "../components/common";
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
  const [typing, setTyping] = useState(false);
  return (
    // Desk-companion face mode: the face as a dome plus two small icons (settings, keyboard) top-right. Everything else is in the menu
    // and the menu-bar tray. Alerts (E-stop, approvals, errors) appear only while they need you.
    <div className={`stage companion-layout face-mode ${state === "SLEEPING" || state === "DISCONNECTED" ? "dim" : ""}`}
      onContextMenu={(e) => { e.preventDefault(); window.baymax?.showCompanionMenu?.(); }}>
      <div className="drag-strip drag" aria-hidden="true" />
      <div className="face-region">
        <BaymaxCharacter state={state} level={voice.listening ? voice.level : 0} look={cursor.look} cursorLive={cursor.live && !sleeping}
          point={gaze.point} taskKind={task?.kind} progress={task?.progress} onClick={() => sleeping && toggleSleep()} />
      </div>
      <span className="sr-only" role="status" aria-live="polite">BAYMAX is {describeState(state)}</span>
      <div className="face-overlay">
        <div className="face-icons">
          <button className="icon-btn no-drag" onClick={() => window.baymax?.showCompanionMenu?.()} title="Settings and more"
            aria-label="Settings and more">
            <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7">
              <circle cx="12" cy="12" r="3.2" />
              <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" strokeLinecap="round" />
              <circle cx="12" cy="12" r="7" />
            </svg>
          </button>
          <button className="icon-btn no-drag" onClick={() => setTyping((v) => !v)} title="Type to BAYMAX" aria-label="Type a message"
            aria-pressed={typing}>
            <svg width="20" height="14" viewBox="0 0 20 14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
              <rect x="0.7" y="0.7" width="18.6" height="12.6" rx="2.2" />
              <path d="M4 4.5h1M7 4.5h1M10 4.5h1M13 4.5h1M16 4.5h0M4 7.2h1M7 7.2h1M10 7.2h1M13 7.2h1M5.5 10h9" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {typing && (
          <ChatInput autoFocus onDone={() => setTyping(false)} placeholder={sleeping ? "Asleep — wake me first" : "Ask BAYMAX…"}
            disabled={sleeping || state === "DISCONNECTED"} />
        )}
        {estop.engaged && <div className="banner estop" role="alert">Emergency Stop is engaged — controlled actions are blocked.</div>}
        <ApprovalBanners />
        {voice.error && <div className="banner" role="alert">{voice.error}</div>}
        {showCaption && lastAssistant && state !== "SLEEPING" && (
          <div className="speech-caption" aria-hidden="true" title={lastAssistant.text}>{lastAssistant.text}</div>
        )}
      </div>
    </div>
  );
}
