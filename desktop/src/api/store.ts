// Minimal external store + pure event reducer (no UI framework dependencies, unit-tested).
import { useRef, useSyncExternalStore } from "react";
import type { Approval, BusEvent, CharacterState, ChatMessage, TaskStatus } from "../shared/protocol";

export interface TaskView {
  taskId: string; status: TaskStatus; title: string; kind?: string; step?: string; progress?: number; detail?: string;
  error?: string; steps?: { title: string; tool: string; status: string; summary: string }[]; verified?: boolean; summary?: string;
  updatedAt: number;
}

export interface TelemetryView { artifactId: string; title: string; model: string; source: string; series: Record<string, number[]>; units: Record<string, string> }

export interface ResearchView {
  taskId: string; query: string; summary: string; summaryKind: string;
  sources: { title: string; url: string }[]; findings: { source: number; text: string }[]; limitations: string[];
}

export interface AppState {
  connection: "connecting" | "connected" | "disconnected";
  character: CharacterState;
  characterReason: string;
  sleeping: boolean;
  estop: { engaged: boolean; reason: string };
  llm: { available: boolean; message: string; model: string } | null;
  voice: { state: string; source: string };
  tasks: Record<string, TaskView>;
  approvals: Record<string, Approval>;
  messages: ChatMessage[];
  devices: { count: number; list: { deviceId: string; remote: string }[] };
  modelVersion: Record<string, number>;
  modelView: { artifactId: string; view: string; nonce: number } | null;
  telemetry: TelemetryView | null;
  lastSeq: number;
  memoryRevision: number;
  research: ResearchView | null;
}

export const initialState: AppState = {
  connection: "connecting", character: "IDLE", characterReason: "", sleeping: false, estop: { engaged: false, reason: "" },
  llm: null, voice: { state: "idle", source: "" }, tasks: {}, approvals: {}, messages: [], devices: { count: 0, list: [] },
  modelVersion: {}, modelView: null, telemetry: null, lastSeq: 0, memoryRevision: 0, research: null,
};

const MAX_MESSAGES = 300;
const MAX_TASKS = 100;

export function applyEvent(state: AppState, ev: BusEvent): AppState {
  const p = ev.payload as Record<string, any>;
  const next: AppState = { ...state, lastSeq: Math.max(state.lastSeq, ev.seq || 0) };
  switch (ev.type) {
    case "character.state":
      return { ...next, character: p.state, characterReason: p.reason ?? "" };
    case "sleep.state":
      return { ...next, sleeping: !!p.sleeping };
    case "research.result": {
      // Only URLs the backend actually fetched (http/https) are kept as links.
      const sources = (Array.isArray(p.sources) ? p.sources : []).filter((x: any) => typeof x?.url === "string" && /^https?:\/\//.test(x.url))
        .map((x: any) => ({ title: String(x.title || x.url).slice(0, 200), url: x.url }));
      return { ...next, research: { taskId: p.taskId ?? "", query: String(p.query ?? ""), summary: String(p.summary ?? ""),
        summaryKind: String(p.summaryKind ?? ""), sources, findings: Array.isArray(p.findings) ? p.findings.slice(0, 8) : [],
        limitations: Array.isArray(p.limitations) ? p.limitations.slice(0, 6).map(String) : [] } };
    }
    case "estop.state":
      return { ...next, estop: { engaged: !!p.engaged, reason: p.reason ?? "" } };
    case "llm.status":
      return { ...next, llm: { available: !!p.available, message: p.message ?? "", model: p.model ?? "" } };
    case "voice.state":
      return { ...next, voice: { state: p.state, source: p.source ?? "" } };
    case "task.update": {
      const prev = state.tasks[p.taskId];
      const view: TaskView = {
        ...prev, taskId: p.taskId, status: p.status, title: p.title, kind: p.kind, step: p.step, detail: p.detail ?? prev?.detail,
        progress: typeof p.progress === "number" ? p.progress : prev?.progress, error: p.error, steps: p.steps ?? prev?.steps,
        updatedAt: ev.ts,
      };
      return { ...next, tasks: trimTasks({ ...state.tasks, [p.taskId]: view }) };
    }
    case "task.result": {
      const prev = state.tasks[p.taskId];
      const view: TaskView = { ...(prev ?? { taskId: p.taskId, title: p.title ?? "", updatedAt: ev.ts }), status: p.status,
        verified: !!p.verified, summary: p.summary, updatedAt: ev.ts };
      const approvals = Object.fromEntries(Object.entries(state.approvals).filter(([, a]) => a.taskId !== p.taskId));
      return { ...next, tasks: trimTasks({ ...state.tasks, [p.taskId]: view }), approvals };
    }
    case "approval.request":
      return { ...next, approvals: { ...state.approvals, [p.approvalId]: p as Approval } };
    case "approval.resolved": {
      const approvals = { ...state.approvals };
      delete approvals[p.approvalId];
      return { ...next, approvals };
    }
    case "assistant.message":
    case "user.message": {
      const msg: ChatMessage = {
        id: p.messageId ?? `u-${ev.seq}`, role: ev.type === "user.message" ? "user" : (p.role ?? "assistant"),
        text: p.text, source: p.source ?? "", taskId: p.taskId || null, ts: ev.ts,
      };
      if (state.messages.some((m) => m.id === msg.id && msg.id !== "sleep-notice")) return next;
      return { ...next, messages: [...state.messages, msg].slice(-MAX_MESSAGES) };
    }
    case "devices.changed":
      return { ...next, devices: { count: p.count ?? 0, list: p.devices ?? [] } };
    case "model3d.updated":
      return {
        ...next, modelVersion: { ...state.modelVersion, [p.artifactId]: p.version ?? (state.modelVersion[p.artifactId] ?? 0) + 1 },
        modelView: p.view ? { artifactId: p.artifactId, view: p.view, nonce: ev.seq } : state.modelView,
      };
    case "telemetry":
      return { ...next, telemetry: p as TelemetryView };
    case "memory.changed":
      return { ...next, memoryRevision: state.memoryRevision + 1 };
    default:
      return next;
  }
}

function trimTasks(tasks: Record<string, TaskView>): Record<string, TaskView> {
  const entries = Object.entries(tasks);
  if (entries.length <= MAX_TASKS) return tasks;
  return Object.fromEntries(entries.sort((a, b) => b[1].updatedAt - a[1].updatedAt).slice(0, MAX_TASKS));
}

type Listener = () => void;

export class Store {
  private state: AppState;
  private listeners = new Set<Listener>();

  constructor(initial: AppState = initialState) {
    this.state = initial;
  }

  get = (): AppState => this.state;

  set = (update: Partial<AppState> | ((s: AppState) => AppState)): void => {
    this.state = typeof update === "function" ? update(this.state) : { ...this.state, ...update };
    this.listeners.forEach((l) => l());
  };

  dispatch = (ev: BusEvent): void => this.set((s) => applyEvent(s, ev));

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
}

export const store = new Store();

export function shallowEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a as object);
  const kb = Object.keys(b as object);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => Object.is((a as any)[k], (b as any)[k]));
}

/** Subscribe to a slice of the store. Derived arrays/objects are cached with shallow equality so a
 * selector like `s => Object.values(s.approvals)` does not cause re-render loops. */
export function useStore<T>(selector: (s: AppState) => T, target: Store = store): T {
  const cache = useRef<{ value: T } | null>(null);
  const getSnapshot = () => {
    const next = selector(target.get());
    if (cache.current && shallowEqual(cache.current.value, next)) return cache.current.value;
    cache.current = { value: next };
    return next;
  };
  return useSyncExternalStore(target.subscribe, getSnapshot);
}
