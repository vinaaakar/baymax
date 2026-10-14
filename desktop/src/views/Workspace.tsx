// Engineering workspace: missions, creation tools, research, telemetry, artifacts, logs, settings.
import { useEffect, useState } from "react";
import { useStore } from "../api/store";
import { ConnectionPill, EStopButton, useActiveTasks, useClient } from "../components/common";
import Create3D from "./workspace/Create3D";
import { Code, Images, Research, Simulations } from "./workspace/Creative";
import Missions from "./workspace/Missions";
import { Artifacts, Devices, Logs, Memory, Settings } from "./workspace/System";

const SECTIONS = {
  missions: { label: "Missions", C: Missions },
  create3d: { label: "3D Creation", C: Create3D },
  images: { label: "Images", C: Images },
  research: { label: "Research", C: Research },
  simulations: { label: "Simulations & Telemetry", C: Simulations },
  code: { label: "Software", C: Code },
  artifacts: { label: "Artifacts", C: Artifacts },
  logs: { label: "Logs", C: Logs },
  memory: { label: "Memory", C: Memory },
  devices: { label: "iPhone Pairing", C: Devices },
  settings: { label: "Settings", C: Settings },
} as const;
type Section = keyof typeof SECTIONS;

export default function Workspace() {
  const client = useClient();
  const fromHash = (): Section => {
    const s = new URLSearchParams(location.hash.split("?")[1] ?? "").get("section") as Section;
    return s && SECTIONS[s] ? s : "missions";
  };
  const [section, setSection] = useState<Section>(fromHash());
  useEffect(() => {
    const on = () => setSection(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const active = useActiveTasks().length;
  const approvals = useStore((s) => Object.keys(s.approvals).length);
  const estop = useStore((s) => s.estop);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && /^[1-9]$/.test(e.key)) setSection(Object.keys(SECTIONS)[Number(e.key) - 1] as Section);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const { C } = SECTIONS[section];
  return (
    <div className="ws">
      <nav aria-label="Workspace sections">
        <div className="drag" style={{ padding: "8px 10px 14px", fontWeight: 700, letterSpacing: 2, color: "var(--glow)" }}>BAYMAX</div>
        {(Object.keys(SECTIONS) as Section[]).map((k, i) => (
          <button key={k} aria-current={section === k ? "page" : undefined} onClick={() => setSection(k)} title={i < 9 ? `⌘${i + 1}` : undefined}>
            <span>{SECTIONS[k].label}</span>
            {k === "missions" && (active > 0 || approvals > 0) && <span className="tag warn">{approvals ? `${approvals} approval` : active}</span>}
          </button>
        ))}
      </nav>
      <main>
        <div className="row" style={{ marginBottom: 12 }}>
          <ConnectionPill />
          <span className="spacer" />
          {estop.engaged && <span className="tag err">E-STOP ENGAGED</span>}
          <EStopButton />
        </div>
        {client ? <C /> : <p className="muted">Connecting to the BAYMAX backend…</p>}
      </main>
    </div>
  );
}
