// TypeScript mirror of the character state machine (tables come from the shared protocol JSON).
import { PROTOCOL, type CharacterEvent, type CharacterState } from "./protocol";

const TRANSITIONS = PROTOCOL.characterTransitions as Record<CharacterState, CharacterState[]>;
const EVENT_TARGETS = PROTOCOL.characterEvents as Record<CharacterEvent, CharacterState>;
const SLEEP_EXIT = new Set(PROTOCOL.characterRules.sleepExitEvents);
const DISCONNECTED_EXIT = new Set(PROTOCOL.characterRules.disconnectedExitEvents);

export interface TransitionResult { accepted: boolean; previous: CharacterState; state: CharacterState; reason: string }

export function canTransition(from: CharacterState, to: CharacterState): boolean {
  return from === to || TRANSITIONS[from].includes(to);
}

export function nextState(from: CharacterState, event: CharacterEvent, verified = false): TransitionResult {
  const to = EVENT_TARGETS[event];
  const reject = (reason: string): TransitionResult => ({ accepted: false, previous: from, state: from, reason });
  if (from === "SLEEPING" && !SLEEP_EXIT.has(event)) return reject("sleeping");
  if (from === "DISCONNECTED" && !DISCONNECTED_EXIT.has(event)) return reject("disconnected");
  if (event === "TASK_VERIFIED" && !verified) return reject("success requires verified outcome");
  if (to === "SUCCESS" && from !== "EXECUTING") return reject("success only follows an executing task");
  if (!canTransition(from, to)) return reject(`invalid transition ${from} -> ${to}`);
  return { accepted: true, previous: from, state: to, reason: "" };
}

/**
 * Client-side view of the character: the backend is authoritative for most
 * states, but the client overlays what only it knows (its own connection).
 */
export class CharacterModel {
  private serverState: CharacterState = "IDLE";
  connected = false;

  get state(): CharacterState {
    return this.connected ? this.serverState : "DISCONNECTED";
  }

  setConnected(connected: boolean): void {
    this.connected = connected;
  }

  applyServerState(state: string): boolean {
    if (!(state in TRANSITIONS)) return false; // ignore unknown/malformed states
    this.serverState = state as CharacterState;
    return true;
  }
}
