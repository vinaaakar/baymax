import { applyEvent, initialState, shallowEqual, Store } from "../src/api/store";
import { BaymaxClient } from "../src/api/client";

const ev = (type: string, payload: Record<string, unknown>, seq = 1) => ({ type, payload, seq, ts: seq });

describe("event reducer", () => {
  it("tracks character, sleep and estop", () => {
    let s = applyEvent(initialState, ev("character.state", { state: "THINKING", reason: "x" }));
    expect(s.character).toBe("THINKING");
    s = applyEvent(s, ev("sleep.state", { sleeping: true }, 2));
    s = applyEvent(s, ev("estop.state", { engaged: true, reason: "test" }, 3));
    expect(s.sleeping).toBe(true);
    expect(s.estop).toEqual({ engaged: true, reason: "test" });
    expect(s.lastSeq).toBe(3);
  });

  it("progress only comes from task updates and is preserved across updates", () => {
    let s = applyEvent(initialState, ev("task.update", { taskId: "t1", status: "RUNNING", title: "T", progress: 0.4 }));
    s = applyEvent(s, ev("task.update", { taskId: "t1", status: "RUNNING", title: "T", detail: "step 2" }, 2));
    expect(s.tasks.t1.progress).toBe(0.4);
    expect(s.tasks.t1.detail).toBe("step 2");
    s = applyEvent(s, ev("task.result", { taskId: "t1", status: "COMPLETED", summary: "ok", verified: true }, 3));
    expect(s.tasks.t1.status).toBe("COMPLETED");
    expect(s.tasks.t1.verified).toBe(true);
  });

  it("approvals are added and removed", () => {
    let s = applyEvent(initialState, ev("approval.request", { approvalId: "a1", taskId: "t1", summary: "send", risk: "high", expiresAt: 0 }));
    expect(Object.keys(s.approvals)).toEqual(["a1"]);
    s = applyEvent(s, ev("task.result", { taskId: "t1", status: "CANCELLED", summary: "denied" }, 2));
    expect(Object.keys(s.approvals)).toEqual([]);
  });

  it("deduplicates messages and bounds history", () => {
    let s = initialState;
    for (let i = 0; i < 320; i++) s = applyEvent(s, ev("assistant.message", { text: `m${i}`, messageId: `id${i}` }, i + 1));
    s = applyEvent(s, ev("assistant.message", { text: "dup", messageId: "id319" }, 999));
    expect(s.messages.length).toBe(300);
    expect(s.messages.at(-1)!.text).toBe("m319");
  });

  it("ignores unknown events safely", () => {
    const s = applyEvent(initialState, ev("weird.event", { a: 1 }, 5));
    expect(s.character).toBe("IDLE");
    expect(s.lastSeq).toBe(5);
  });

  it("shallowEqual caches derived arrays", () => {
    const a = { x: 1 };
    expect(shallowEqual([a], [a])).toBe(true);
    expect(shallowEqual([a], [{ x: 1 }])).toBe(false);
    expect(shallowEqual({ a }, { a })).toBe(true);
  });
});

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("client reconnection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWS.instances = [];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({}), { status: 500 })));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("authenticates first, marks disconnected on loss, and reconnects with backoff", () => {
    const store = new Store();
    const c = new BaymaxClient({ apiBase: "http://127.0.0.1:8765", token: "tok" }, store, FakeWS as unknown as typeof WebSocket);
    c.connect();
    const ws = FakeWS.instances[0];
    expect(ws.url).toBe("ws://127.0.0.1:8765/api/ws");
    ws.readyState = 1;
    ws.onopen!();
    expect(JSON.parse(ws.sent[0])).toEqual({ type: "auth", token: "tok" });
    ws.onmessage!({ data: JSON.stringify({ type: "hello", payload: { character: { state: "IDLE" }, recent: [] } }) });
    expect(store.get().connection).toBe("connected");
    ws.onmessage!({ data: "{not json" }); // malformed frames are ignored
    ws.onmessage!({ data: JSON.stringify({ type: "character.state", payload: { state: "LISTENING" }, seq: 2, ts: 2 }) });
    expect(store.get().character).toBe("LISTENING");
    ws.close();
    expect(store.get().connection).toBe("disconnected");
    expect(FakeWS.instances.length).toBe(1);
    vi.advanceTimersByTime(500);
    expect(FakeWS.instances.length).toBe(2);
    FakeWS.instances[1].close();
    vi.advanceTimersByTime(999);
    expect(FakeWS.instances.length).toBe(2); // backoff doubled to 1 s
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances.length).toBe(3);
    c.close();
    vi.advanceTimersByTime(20000);
    expect(FakeWS.instances.length).toBe(3); // no reconnect after an explicit close
  });
});
