// PCM utilities: downsampling, 16-bit WAV encoding and a simple energy VAD (unit-tested).

export function downsample(input: Float32Array, fromRate: number, toRate = 16000): Float32Array {
  if (fromRate === toRate) return input;
  if (toRate > fromRate) throw new Error("upsampling not supported");
  const ratio = fromRate / toRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    // average over the source window (cheap anti-aliasing)
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

export function encodeWav(samples: Float32Array, sampleRate = 16000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buffer);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function rms(frame: Float32Array): number {
  let s = 0;
  for (let i = 0; i < frame.length; i++) s += frame[i] * frame[i];
  return Math.sqrt(s / Math.max(1, frame.length));
}

/** Energy-based voice activity detector with an adaptive noise floor and hangover. */
export class Vad {
  noise = 0.01;
  speechMs = 0;
  silenceMs = 0;
  heardSpeech = false;

  constructor(private readonly opts = { silenceStopMs: 1200, minSpeechMs: 200, noSpeechTimeoutMs: 7000 }) {}

  /** Feed one frame; returns "continue" | "stop" (end of utterance) | "nospeech". */
  push(level: number, frameMs: number, elapsedMs: number): "continue" | "stop" | "nospeech" {
    const threshold = Math.max(0.015, this.noise * 3);
    if (level > threshold) {
      this.speechMs += frameMs;
      this.silenceMs = 0;
      if (this.speechMs >= this.opts.minSpeechMs) this.heardSpeech = true;
    } else {
      this.noise = this.noise * 0.95 + level * 0.05;
      this.silenceMs += frameMs;
    }
    if (this.heardSpeech && this.silenceMs >= this.opts.silenceStopMs) return "stop";
    if (!this.heardSpeech && elapsedMs >= this.opts.noSpeechTimeoutMs) return "nospeech";
    return "continue";
  }
}
