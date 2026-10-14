import BaymaxKit
import Foundation

/// Thread-safe holder for live gaze data: written on the main actor when cursor/gaze messages arrive,
/// read by the 2D face renderer every frame (no SwiftUI state change per cursor sample).
final class GazeSource: @unchecked Sendable {
    /// What the debug overlay shows: the last real cursor sample exactly as received from the Mac.
    struct DebugSnapshot {
        var x = 0.0, y = 0.0, vx = 0.0, vy = 0.0
        var displayId = 0, displayWidth = 0, displayHeight = 0
        var scale = 1.0
        var seq: Int?
        var macTimestampMs = 0
        var receivedAt: Date?
        var samples = 0
        var dropped = 0
        var look = Look(x: 0, y: 0)
        var live = false
        var expression = "—"          // what the face is showing (FaceExpression)
        var pupil = (x: 0.0, y: 0.0)  // eye-group offset from rest, face units
        var pointLabel: String?        // verified screen target being pointed at
    }

    private let lock = NSLock()
    private var tracker = GazeTracker()
    private var debug = DebugSnapshot()
    private(set) var samples = 0
    // Pointing events from the Mac: newest seq wins; a clear names the point it withdraws.
    private var lastPointSeq: Int?
    private var activePointId: String?

    private static func nowMs() -> Double { ProcessInfo.processInfo.systemUptime * 1000 }

    func setPlacement(_ p: Placement) {
        lock.lock(); defer { lock.unlock() }
        tracker.cfg.placement = p
    }

    func cursor(vx: Double, vy: Double, seq: Int?) {
        lock.lock(); defer { lock.unlock() }
        if tracker.update(vx: vx, vy: vy, seq: seq, now: Self.nowMs()) { samples += 1 } else { debug.dropped += 1 }
    }

    /// Records the raw cursor.update payload for the debug overlay, then feeds the tracker.
    func cursor(_ env: Envelope) {
        guard let vx = env.double("vx") ?? env.double("x"), let vy = env.double("vy") ?? env.double("y") else { return }
        cursor(vx: vx, vy: vy, seq: env.int("seq"))
        lock.lock(); defer { lock.unlock() }
        debug.x = env.double("x") ?? vx
        debug.y = env.double("y") ?? vy
        debug.vx = vx
        debug.vy = vy
        debug.displayId = env.int("displayId") ?? 0
        debug.displayWidth = env.int("displayWidth") ?? 0
        debug.displayHeight = env.int("displayHeight") ?? 0
        debug.scale = env.double("scale") ?? 1
        debug.seq = env.int("seq")
        debug.macTimestampMs = env.ts
        debug.receivedAt = Date()
        debug.samples = samples
    }

    /// The renderer reports what it drew, so the diagnostics panel shows the real expression and pupil offsets.
    func recordFace(expression: FaceExpression, pupil: (x: Double, y: Double)) {
        lock.lock(); defer { lock.unlock() }
        debug.expression = expression.rawValue
        debug.pupil = pupil
    }

    func debugSnapshot() -> DebugSnapshot {
        lock.lock(); defer { lock.unlock() }
        var d = debug
        let now = Self.nowMs()
        d.look = tracker.look(now: now)
        d.live = tracker.isLive(now: now)
        d.pointLabel = tracker.activePoint(now: now)?.label
        return d
    }

    /// Shows a pointing event unless it is older than a point already shown (seq) or already expired when it arrived
    /// (sentMs = the Mac's envelope timestamp). The two clocks are only NTP-close, so 3 s of skew is tolerated: a point is
    /// dropped as stale only when it arrives more than durationMs + 3 s after the Mac sent it.
    /// Returns false when the event was dropped.
    @discardableResult
    func point(vx: Double, vy: Double, label: String, durationMs: Double, pointId: String? = nil, seq: Int? = nil,
               sentMs: Int? = nil) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if let seq, let last = lastPointSeq, seq <= last { return false }
        if let sentMs, sentMs > 0 {
            let age = Date().timeIntervalSince1970 * 1000 - Double(sentMs)
            if age > durationMs + 3000 { return false }
        }
        if let seq { lastPointSeq = seq }
        activePointId = pointId
        tracker.pointAt(vx: vx, vy: vy, label: label, durationMs: durationMs, now: Self.nowMs())
        return true
    }

    /// Withdraws the current point; with an id, only if that point is still the one shown.
    func clearPoint(pointId: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        if let pointId, let active = activePointId, pointId != active { return }
        activePointId = nil
        tracker.clearPoint()
    }

    /// Called on reconnect, wake and sleep: stale samples must not drive the gaze.
    func reset() {
        lock.lock(); defer { lock.unlock() }
        lastPointSeq = nil  // the Mac may have restarted: its sequence starts again
        activePointId = nil
        tracker.reset()
    }

    /// (look, point) for this frame.
    func current() -> (look: Look, point: Look?, live: Bool) {
        lock.lock(); defer { lock.unlock() }
        let now = Self.nowMs()
        return (tracker.look(now: now), tracker.activePoint(now: now)?.look, tracker.isLive(now: now))
    }
}
