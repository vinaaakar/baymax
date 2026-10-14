import Foundation

/// Cursor -> gaze mapping. Exact port of desktop/src/shared/gaze.ts (keep both in sync; tests mirror each other).
public enum Placement: String, CaseIterable, Codable, Sendable { case below, left, right }

public struct GazeConfig: Equatable, Sendable {
    public var placement: Placement = .below
    public var gainX = 1.0
    public var gainY = 0.7
    public var staleMs = 2500.0
    public var recenterMs = 800.0
    public init(placement: Placement = .below) { self.placement = placement }
}

public struct Look: Equatable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

public enum GazeMath {
    static func clamp(_ v: Double, _ lo: Double = -1, _ hi: Double = 1) -> Double {
        guard v.isFinite else { return 0 }
        return min(hi, max(lo, v))
    }

    public static func rest(_ cfg: GazeConfig) -> Look {
        Look(x: cfg.placement == .left ? 0.35 : cfg.placement == .right ? -0.35 : 0, y: cfg.placement == .below ? 0.3 : 0.1)
    }

    public static func cursorToLook(vx: Double, vy: Double, cfg: GazeConfig = GazeConfig()) -> Look {
        // Invalid samples look straight ahead; off-screen positions push to the edge and a little beyond.
        let nx = vx.isFinite ? clamp(vx, -0.6, 1.6) : 0.5, ny = vy.isFinite ? clamp(vy, -0.6, 1.6) : 0.5
        var x = (nx - 0.5) * 2 * cfg.gainX
        var y = (0.5 - ny) * 2 * cfg.gainY
        let r = rest(cfg)
        switch cfg.placement {
        case .below: y = y * 0.6 + r.y
        case .left, .right: x = x * 0.7 + r.x
        }
        return Look(x: clamp(x), y: clamp(y))
    }
}

/// Latest cursor sample from the Mac; drifts back to rest when samples stop arriving.
public struct GazeTracker: Sendable {
    public var cfg: GazeConfig
    private var target: Look
    private var lastAt = -Double.infinity
    private var lastSeq = -1
    private var point: (look: Look, until: Double, label: String)?

    public init(cfg: GazeConfig = GazeConfig()) {
        self.cfg = cfg
        self.target = GazeMath.rest(cfg)
    }

    /// `now` is in milliseconds.
    @discardableResult
    public mutating func update(vx: Double, vy: Double, seq: Int?, now: Double) -> Bool {
        guard vx.isFinite, vy.isFinite else { return false }
        if let seq {
            if seq <= lastSeq && lastSeq - seq < 1000 { return false } // out of order
            lastSeq = seq
        }
        target = GazeMath.cursorToLook(vx: vx, vy: vy, cfg: cfg)
        lastAt = now
        return true
    }

    public mutating func pointAt(vx: Double, vy: Double, label: String, durationMs: Double, now: Double) {
        point = (GazeMath.cursorToLook(vx: vx, vy: vy, cfg: cfg), now + max(500, min(durationMs, 15000)), label)
    }

    public mutating func clearPoint() { point = nil }

    public mutating func reset() {
        lastSeq = -1
        lastAt = -Double.infinity
        point = nil
    }

    public func activePoint(now: Double) -> (look: Look, label: String)? {
        guard let p = point, now <= p.until else { return nil }
        return (p.look, p.label)
    }

    public func look(now: Double) -> Look {
        if let p = activePoint(now: now) { return p.look }
        let r = GazeMath.rest(cfg)
        let age = now - lastAt
        if age <= cfg.staleMs { return target }
        let k = min(1, (age - cfg.staleMs) / cfg.recenterMs)
        return Look(x: target.x + (r.x - target.x) * k, y: target.y + (r.y - target.y) * k)
    }

    public func isLive(now: Double) -> Bool { now - lastAt <= cfg.staleMs }
}
