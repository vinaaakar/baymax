// Push-to-talk microphone capture. The microphone is opened only when the user presses the
// mic control and is released (tracks stopped) as soon as capture ends — no background recording.
import { downsample, encodeWav, rms, Vad } from "./wav";

export interface RecorderCallbacks {
  onLevel: (level: number) => void;
  onState: (state: "listening" | "idle" | "unavailable") => void;
}

export interface Recording { wav: Blob; seconds: number; speech: boolean }

export class PushToTalkRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private resolve: ((r: Recording) => void) | null = null;
  private started = 0;
  private vad = new Vad();
  active = false;

  constructor(private cb: RecorderCallbacks, private maxSeconds = 30) {}

  async start(): Promise<Recording> {
    if (this.active) throw new Error("already recording");
    if (window.baymax?.requestMicrophone && !(await window.baymax.requestMicrophone())) {
      this.cb.onState("unavailable");
      throw new Error("Microphone permission was denied. Enable it in System Settings → Privacy & Security → Microphone.");
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (e) {
      this.cb.onState("unavailable");
      throw new Error(`Microphone unavailable: ${(e as Error).message}`);
    }
    this.ctx = new AudioContext();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(2048, 1, 1);
    this.chunks = [];
    this.vad = new Vad();
    this.started = performance.now();
    this.active = true;
    this.cb.onState("listening");
    const frameMs = (2048 / this.ctx.sampleRate) * 1000;
    this.node.onaudioprocess = (e) => {
      if (!this.active) return;
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      this.chunks.push(data);
      const level = rms(data);
      this.cb.onLevel(Math.min(1, level * 6));
      const elapsed = performance.now() - this.started;
      const verdict = this.vad.push(level, frameMs, elapsed);
      if (verdict !== "continue" || elapsed > this.maxSeconds * 1000) this.stop();
    };
    src.connect(this.node);
    this.node.connect(this.ctx.destination);
    return new Promise((res) => (this.resolve = res));
  }

  /** Sleep / emergency stop: release the microphone and discard the audio (nothing is transcribed). */
  cancel(): void {
    if (!this.active) return;
    this.chunks = [];
    this.vad = new Vad(); // heardSpeech=false -> the caller treats it as "no speech"
    this.stop();
  }

  stop(): void {
    if (!this.active) return;
    this.active = false;
    const rate = this.ctx?.sampleRate ?? 48000;
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop()); // release the microphone immediately
    this.ctx?.close().catch(() => undefined);
    this.cb.onLevel(0);
    this.cb.onState("idle");
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const all = new Float32Array(total);
    let off = 0;
    for (const c of this.chunks) {
      all.set(c, off);
      off += c.length;
    }
    const pcm = downsample(all, rate, 16000);
    this.resolve?.({ wav: encodeWav(pcm, 16000), seconds: pcm.length / 16000, speech: this.vad.heardSpeech });
    this.resolve = null;
    this.chunks = [];
  }
}
