import BaymaxKit
import Foundation

/// Thread-safe holder for live gaze data: written on the main actor when cursor/gaze messages arrive,
/// read by the SceneKit render thread every frame (no SwiftUI re-render per cursor sample).
final class GazeSource: @unchecked Sendable {
    private let lock = NSLock()
    private var tracker = GazeTracker()
    private(set) var samples = 0

    private static func nowMs() -> Double { ProcessInfo.processInfo.systemUptime * 1000 }

    func setPlacement(_ p: Placement) {
        lock.lock(); defer { lock.unlock() }
        tracker.cfg.placement = p
    }

    func cursor(vx: Double, vy: Double, seq: Int?) {
        lock.lock(); defer { lock.unlock() }
        if tracker.update(vx: vx, vy: vy, seq: seq, now: Self.nowMs()) { samples += 1 }
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
