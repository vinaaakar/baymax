import BaymaxKit
import Foundation

/// Thread-safe holder for live gaze data: written on the main actor when cursor/gaze messages arrive,
/// read by the SceneKit render thread every frame (no SwiftUI re-render per cursor sample).
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
    }

    private let lock = NSLock()
    private var tracker = GazeTracker()
    private var debug = DebugSnapshot()
    private(set) var samples = 0

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

    func debugSnapshot() -> DebugSnapshot {
        lock.lock(); defer { lock.unlock() }
        var d = debug
        let now = Self.nowMs()
        d.look = tracker.look(now: now)
        d.live = tracker.isLive(now: now)
        return d
    }

    func point(vx: Double, vy: Double, label: String, durationMs: Double) {
        lock.lock(); defer { lock.unlock() }
        tracker.pointAt(vx: vx, vy: vy, label: label, durationMs: durationMs, now: Self.nowMs())
    }

    func clearPoint() {
        lock.lock(); defer { lock.unlock() }
        tracker.clearPoint()
    }

    /// Called on reconnect, wake and sleep: stale samples must not drive the gaze.
    func reset() {
        lock.lock(); defer { lock.unlock() }
        tracker.reset()
    }

    /// (look, point) for this frame.
    func current() -> (look: Look, point: Look?, live: Bool) {
        lock.lock(); defer { lock.unlock() }
        let now = Self.nowMs()
        return (tracker.look(now: now), tracker.activePoint(now: now)?.look, tracker.isLive(now: now))
    }
}
