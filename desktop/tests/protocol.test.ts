import spec from "../../protocol/baymax-protocol.json";
import { CHARACTER_STATES, TASK_STATES } from "../src/shared/protocol";
import { CharacterModel, canTransition, nextState } from "../src/shared/stateMachine";
import type { CharacterEvent, CharacterState } from "../src/shared/protocol";

describe("shared protocol", () => {
  it("state lists match the spec", () => {
    expect([...CHARACTER_STATES]).toEqual(spec.characterStates);
    expect([...TASK_STATES]).toEqual(spec.taskStates);
  });

  it("passes every shared character test vector", () => {
    for (const v of spec.characterTestVectors) {
      const r = nextState(v.from as CharacterState, v.event as CharacterEvent, (v as { verified?: boolean }).verified ?? false);
      expect(r.state, JSON.stringify(v)).toBe(v.expect);
      expect(r.accepted, JSON.stringify(v)).toBe(!(v as { rejected?: boolean }).rejected);
    }
  });

  it("never shows success without verification", () => {
    expect(nextState("EXECUTING", "TASK_VERIFIED", false).accepted).toBe(false);
    expect(nextState("THINKING", "TASK_VERIFIED", true).accepted).toBe(false);
    expect(nextState("EXECUTING", "TASK_VERIFIED", true).state).toBe("SUCCESS");
  });

  it("sleeping only wakes or disconnects", () => {
    for (const e of Object.keys(spec.characterEvents) as CharacterEvent[]) {
      const r = nextState("SLEEPING", e, true);
      if (e === "WAKE") expect(r.state).toBe("IDLE");
      else if (e === "CONNECTION_LOST") expect(r.state).toBe("DISCONNECTED");
      else expect(r.state).toBe("SLEEPING");
    }
  });

  it("transition table is consistent", () => {
    for (const s of CHARACTER_STATES) {
      expect(canTransition(s, s)).toBe(true);
      expect(canTransition(s, "DISCONNECTED")).toBe(true);
    }
  });

  it("client model overlays its own connection state and rejects junk", () => {
    const m = new CharacterModel();
    expect(m.state).toBe("DISCONNECTED");
    m.setConnected(true);
    expect(m.applyServerState("EXECUTING")).toBe(true);
    expect(m.state).toBe("EXECUTING");
    expect(m.applyServerState("HACKED")).toBe(false);
    expect(m.state).toBe("EXECUTING");
    m.setConnected(false);
    expect(m.state).toBe("DISCONNECTED");
  });
});
