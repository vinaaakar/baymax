import BaymaxKit
import SwiftUI

/// Diagnostics overlay (Settings → Cursor debug): proves the face is driven by real Mac cursor samples.
/// Shows the raw received coordinates, the normalised values, the computed gaze, the expression and pupil offsets
/// actually drawn, the connection and the age of the last event.
struct CursorDebugPanel: View {
    let gaze: GazeSource
    let connection: String
    let sleeping: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.2)) { ctx in
            let d = gaze.debugSnapshot()
            VStack(alignment: .leading, spacing: 3) {
                Text("DIAGNOSTICS").font(.caption2.weight(.semibold)).foregroundStyle(Theme.glow)
                row("connection", connection + (sleeping ? " · sleeping (stream paused)" : ""))
                row("display", d.displayWidth > 0 ? "#\(d.displayId) \(d.displayWidth)×\(d.displayHeight) @\(fmt(d.scale, 1))x" : "—")
                row("on display", "x \(fmt(d.x)) y \(fmt(d.y))")
                row("vs primary", "vx \(fmt(d.vx)) vy \(fmt(d.vy))")
                row("gaze", "x \(fmt(d.look.x)) y \(fmt(d.look.y))" + (d.live ? " · live" : " · resting"))
                row("samples", "\(d.samples) used, \(d.dropped) dropped, seq \(d.seq.map(String.init) ?? "—")")
                row("expression", d.expression + (d.pointLabel.map { " → \($0)" } ?? ""))
                row("pupils", "dx \(fmt(d.pupil.x)) dy \(fmt(d.pupil.y)) (face units)")
                row("last event", age(d.receivedAt, now: ctx.date) + (d.macTimestampMs > 0 ? " · mac ts \(d.macTimestampMs)" : ""))
            }
            .font(.caption.monospaced())
            .foregroundStyle(Theme.white)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Cursor debug panel")
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(k).foregroundStyle(Theme.muted).frame(width: 78, alignment: .leading)
            Text(v).lineLimit(1).minimumScaleFactor(0.7)
        }
    }

    private func fmt(_ v: Double, _ digits: Int = 3) -> String { String(format: "%.\(digits)f", v) }

    private func age(_ t: Date?, now: Date) -> String {
        guard let t else { return "no cursor.update received yet" }
        let s = now.timeIntervalSince(t)
        return s < 1 ? "\(Int(s * 1000)) ms ago" : String(format: "%.1f s ago", s)
    }
}
