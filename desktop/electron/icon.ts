// Generates the menu-bar (tray) icon at runtime: two eyes joined by a line on a transparent
// background, as a macOS "template image" (black + alpha) so it adapts to light/dark menu bars.
import zlib from "zlib";

function crc32(buf: Buffer): number {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

export function eyesIconPng(size = 22, scale = 2): Buffer {
  const S = size * scale;
  const rgba = Buffer.alloc(S * S * 4);
  const cy = S * 0.5;
  const ex = S * 0.27;
  const r = S * 0.13;
  for (let y = 0; y < S; y++) {
    for (let x = 0; x < S; x++) {
      const dl = Math.hypot(x + 0.5 - (S / 2 - ex), y + 0.5 - cy);
      const dr = Math.hypot(x + 0.5 - (S / 2 + ex), y + 0.5 - cy);
      const onEye = Math.max(0, Math.min(1, r - Math.min(dl, dr) + 0.5));
      const onLine = x > S / 2 - ex && x < S / 2 + ex ? Math.max(0, Math.min(1, S * 0.035 - Math.abs(y + 0.5 - cy) + 0.5)) : 0;
      const a = Math.max(onEye, onLine);
      const i = (y * S + x) * 4;
      rgba[i + 3] = Math.round(a * 255);
    }
  }
  const raw = Buffer.alloc((S * 4 + 1) * S);
  for (let y = 0; y < S; y++) {
    raw[y * (S * 4 + 1)] = 0;
    rgba.copy(raw, y * (S * 4 + 1) + 1, y * S * 4, (y + 1) * S * 4);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(S, 0);
  ihdr.writeUInt32BE(S, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
