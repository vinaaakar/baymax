// Full conversation window: history, task results, approvals, voice and text input.
import { useEffect, useRef } from "react";
import { useStore } from "../api/store";
import BaymaxCharacter from "../character/BaymaxCharacter";
import { ApprovalBanners, ChatInput, ConnectionPill, EStopButton, MicButton, useClient, useVoice } from "../components/common";
import { useDisplayedState } from "./Companion";

export default function Conversation() {
  useClient();
  const messages = useStore((s) => s.messages);
  const state = useDisplayedState();
  const sleeping = useStore((s) => s.sleeping);
  const voice = useVoice();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages.length]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", height: "100%" }}>
      <aside className="stage" style={{ borderRight: "1px solid var(--line)" }}>
        <BaymaxCharacter state={state} level={voice.listening ? voice.level : 0} quality="low" />
      </aside>
      <section style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0d0507" }}>
        <header className="row drag" style={{ padding: "10px 14px", borderBottom: "1px solid var(--line)" }}>
          <ConnectionPill />
          <span className="spacer" />
          <EStopButton compact />
        </header>
        <div className="chat" style={{ flex: 1, overflow: "auto", padding: 16 }} role="log" aria-live="polite" aria-label="Conversation">
          {messages.length === 0 && <p className="muted">No messages yet. Type below or press the microphone to talk.</p>}
          {messages.map((m) => (
            <div key={m.id} className={`bubble ${m.role === "user" ? "user" : m.role === "system" ? "system" : "assistant"}`}>
              {m.text}
              {m.source?.startsWith("device:") && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>from iPhone</div>}
            </div>
          ))}
          <div ref={end} />
        </div>
        <footer style={{ padding: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 8 }}>
          <ApprovalBanners />
          {voice.error && <div className="banner" role="alert">{voice.error}</div>}
          <div className="row">
            <MicButton voice={voice} disabled={sleeping || state === "DISCONNECTED"} />
            <ChatInput disabled={state === "DISCONNECTED"} />
          </div>
        </footer>
      </section>
    </div>
  );
}
