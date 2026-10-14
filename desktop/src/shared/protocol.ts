// Shared protocol definitions, loaded from the single source of truth in
// ../../protocol/baymax-protocol.json (also used by the Python backend and Swift app).
import spec from "../../../protocol/baymax-protocol.json";

export const PROTOCOL = spec;
export const PROTOCOL_VERSION: number = spec.version;

export const CHARACTER_STATES = [
  "IDLE", "LISTENING", "THINKING", "SPEAKING", "EXECUTING", "SUCCESS",
  "CONFUSED", "CONCERNED", "SLEEPING", "ERROR", "DISCONNECTED",
] as const;
export type CharacterState = (typeof CHARACTER_STATES)[number];

export type CharacterEvent = keyof typeof spec.characterEvents;

export const TASK_STATES = ["REQUESTED", "PLANNED", "AWAITING_APPROVAL", "RUNNING", "VERIFYING", "COMPLETED", "FAILED", "CANCELLED"] as const;
export type TaskStatus = (typeof TASK_STATES)[number];

export function isTerminal(s: TaskStatus): boolean {
  return s === "COMPLETED" || s === "FAILED" || s === "CANCELLED";
}

export interface PlanStep { title: string; tool: string; args: Record<string, unknown>; status: string; summary: string }

export interface Task {
  id: string; kind: string; title: string; request: string; source: string; status: TaskStatus;
  plan: PlanStep[]; current_step: number; progress: number | null; approval: Record<string, unknown> | null;
  result: ToolResult | null; error: string | null; verified: boolean; created_at: number; updated_at: number;
}

export interface Artifact {
  id: string; kind: string; title: string; path: string; taskId: string | null; meta: Record<string, any>;
  validated: boolean; createdAt: number; updatedAt: number;
}

export interface ToolResult {
  ok: boolean; summary: string; data: Record<string, any>; artifacts: Artifact[]; verified: boolean; verification: string;
  untrusted: boolean; needsClarification: string;
}

export interface BusEvent<T = Record<string, any>> { type: string; payload: T; seq: number; ts: number }

export interface ChatMessage { id: string; role: string; text: string; source: string; taskId?: string | null; ts: number }

export interface Approval { approvalId: string; taskId: string; risk: string; summary: string; expiresAt: number; remoteAllowed?: boolean }
