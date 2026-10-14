import { CHARACTER_STATES } from "../src/shared/protocol";
import { blend, blink, frameRateFor, REST, targetPose } from "../src/character/rig";
import { downsample, encodeWav, rms, Vad } from "../src/voice/wav";

const input = { lookX: 0, lookY: 0, level: 0, reducedMotion: false };

describe("animation rig", () => {
  it("produces a finite pose for every state", () => {
    for (const s of CHARACTER_STATES) {
      for (const t of [0, 0.5, 3.3, 100]) {
        const p = targetPose(s, t, input);
        for (const [k, v] of Object.entries(p)) expect(Number.isFinite(v), `${s}.${k}`).toBe(true);
      }
    }
  });

  it("sleeping closes the eyes and dims; listening glows with voice level", () => {
    expect(targetPose("SLEEPING", 1, input).eyeOpen).toBeLessThan(0.1);
    expect(targetPose("SLEEPING", 1, input).dim).toBeGreaterThan(0);
    expect(targetPose("LISTENING", 1, { ...input, level: 1 }).glow).toBeGreaterThan(targetPose("LISTENING", 1, input).glow);
  });

  it("eye tracking follows the cursor in idle", () => {
    expect(targetPose("IDLE", 0.2, { ...input, lookX: 1 }).headYaw).toBeGreaterThan(targetPose("IDLE", 0.2, { ...input, lookX: -1 }).headYaw);
  });

  it("reduced motion shrinks movement amplitude", () => {
    const amp = (rm: boolean) => {
      let lo = Infinity, hi = -Infinity;
      for (let t = 0; t < 10; t += 0.05) {
        const y = targetPose("SUCCESS", t, { ...input, reducedMotion: rm }).bodyY;
        lo = Math.min(lo, y);
        hi = Math.max(hi, y);
      }
      return hi - lo;
    };
    expect(amp(true)).toBeLessThan(amp(false) * 0.3);
  });

  it("blending converges smoothly and is frame-rate independent", () => {
    const target = targetPose("SUCCESS", 0, input);
    let a = { ...REST };
    for (let i = 0; i < 60; i++) a = blend(a, target, 1 / 60);
    let b = { ...REST };
    for (let i = 0; i < 30; i++) b = blend(b, target, 1 / 30);
    expect(Math.abs(a.armL - b.armL)).toBeLessThan(0.01);
    expect(Math.abs(a.armL - target.armL)).toBeLessThan(0.02);
  });

  it("blinks are brief", () => {
    let closed = 0;
    for (let t = 0; t < 41; t += 0.01) if (blink(t) < 0.5) closed++;
    expect(closed).toBeGreaterThan(0);
    expect(closed / 4100).toBeLessThan(0.05);
  });

  it("caps frame rate lower when idle or asleep", () => {
    expect(frameRateFor("SLEEPING", false)).toBeLessThan(frameRateFor("IDLE", false));
    expect(frameRateFor("EXECUTING", true)).toBeLessThanOrEqual(20);
  });
});

describe("voice utilities", () => {
  it("encodes a valid 16 kHz mono WAV", async () => {
    const wav = encodeWav(new Float32Array([0, 0.5, -0.5, 1]), 16000);
    // jsdom's Blob has no arrayBuffer(); FileReader is supported
    const ab = await new Promise<ArrayBuffer>((res) => { const r = new FileReader(); r.onload = () => res(r.result as ArrayBuffer); r.readAsArrayBuffer(wav); });
    const buf = new DataView(ab);
    const tag = (o: number) => String.fromCharCode(...[0, 1, 2, 3].map((i) => buf.getUint8(o + i)));
    expect(tag(0)).toBe("RIFF");
    expect(tag(8)).toBe("WAVE");
    expect(buf.getUint32(24, true)).toBe(16000);
    expect(buf.getUint16(22, true)).toBe(1);
    expect(buf.getInt16(46, true)).toBe(16383);
    expect(buf.byteLength).toBe(44 + 8);
  });

  it("downsamples 48 kHz to 16 kHz", () => {
    expect(downsample(new Float32Array(48000), 48000, 16000).length).toBe(16000);
  });

  it("VAD stops after trailing silence and times out without speech", () => {
    const v = new Vad();
    let verdict = "continue";
    let t = 0;
    for (let i = 0; i < 20; i++) verdict = v.push(0.002, 40, (t += 40)); // background noise
    for (let i = 0; i < 15; i++) verdict = v.push(0.2, 40, (t += 40)); // speech
    expect(v.heardSpeech).toBe(true);
    for (let i = 0; i < 40 && verdict === "continue"; i++) verdict = v.push(0.002, 40, (t += 40));
    expect(verdict).toBe("stop");
    const quiet = new Vad();
    let q = "continue";
    for (let i = 0; i < 300 && q === "continue"; i++) q = quiet.push(0.001, 40, i * 40);
    expect(q).toBe("nospeech");
    expect(rms(new Float32Array([1, -1]))).toBe(1);
  });
});
