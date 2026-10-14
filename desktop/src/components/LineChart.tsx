// Lightweight SVG line chart for telemetry (no chart library needed).
const COLORS = ["#FF334F", "#7cc4f0", "#f9d71c", "#9ccc3c"];

export default function LineChart({ x, series, units, label }: { x: number[]; series: Record<string, number[]>; units?: Record<string, string>; label?: string }) {
  const W = 720, H = 260, L = 50, R = 12, T = 24, B = 28;
  const ys = Object.values(series).flat().filter(Number.isFinite);
  if (!x.length || !ys.length) return <p className="muted">No data.</p>;
  const x0 = Math.min(...x), x1 = Math.max(...x) || 1;
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const px = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
  const py = (v: number) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label ?? "telemetry chart"} style={{ width: "100%", height: "auto" }}>
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const v = y0 + (y1 - y0) * f;
        return <g key={f}><line x1={L} x2={W - R} y1={py(v)} y2={py(v)} stroke="#3a1a21" /><text x={L - 6} y={py(v) + 4} fill="#c9a9ae" fontSize="10" textAnchor="end">{v.toPrecision(3)}</text></g>;
      })}
      {Object.entries(series).map(([name, s], i) => (
        <g key={name}>
          <path fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth={2} d={s.map((v, j) => `${j ? "L" : "M"}${px(x[j]).toFixed(1)},${py(v).toFixed(1)}`).join(" ")} />
          <text x={L + 8 + i * 140} y={14} fill={COLORS[i % COLORS.length]} fontSize="11">{name}{units?.[name] ? ` (${units[name]})` : ""}</text>
        </g>
      ))}
      <text x={W - R} y={H - 6} fill="#FF334F" fontSize="10" textAnchor="end">SIMULATED DATA — not sensor readings</text>
    </svg>
  );
}
