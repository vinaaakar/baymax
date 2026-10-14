// Bust framing shared with the iPhone (ios/BaymaxKit/Sources/BaymaxKit/Framing.swift).
// Numbers come from design/baymax-visual-spec.json; both ports are checked against its test vectors.
import spec from "../../../design/baymax-visual-spec.json";

export const VISUAL_SPEC = spec;

export interface FramingSpec {
  fovDeg: number;
  headTop: number;
  bustBottom: number;
  minHalfWidth: number;
  minHalfHeight: number;
  topMargin: number;
  anchorFromTop: number;
  maxHalfHeight: number;
}

export interface BustCamera {
  halfHeight: number; // visible half-height at the character plane (z = 0)
  centerY: number;    // camera height; it looks straight ahead
  distance: number;   // camera z
  fovDeg: number;     // vertical field of view
}

export function bustCamera(aspect: number, f: FramingSpec = spec.framing): BustCamera {
  const a = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const halfV = Math.min(Math.max(f.minHalfHeight, f.minHalfWidth / a), f.maxHalfHeight);
  const bustCenter = (f.headTop + f.bustBottom) / 2;
  let centerY = bustCenter - halfV * (1 - 2 * f.anchorFromTop);
  centerY = Math.max(centerY, f.headTop + f.topMargin - halfV); // never cut the head
  const distance = halfV / Math.tan(((f.fovDeg / 2) * Math.PI) / 180);
  return { halfHeight: halfV, centerY, distance, fovDeg: f.fovDeg };
}
