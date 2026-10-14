import Foundation

/// Swift port of desktop/src/character/rig.ts: state + time -> target pose, with smooth blending.
public struct Pose: Equatable, Sendable {
    public var bodyY = 0.0, breathe = 1.0, lean = 0.0, sway = 0.0
    public var headYaw = 0.0, headPitch = 0.05, headRoll = 0.0
    public var eyeX = 0.0, eyeY = 0.0, eyeOpen = 1.0
    public var armL = 0.16, armR = 0.16, armLFwd = 0.0, armRFwd = 0.0
    public var glow = 0.0, dim = 0.0
    public init() {}

    static let keys: [WritableKeyPath<Pose, Double>] = [\.bodyY, \.breathe, \.lean, \.sway, \.headYaw, \.headPitch, \.headRoll,
                                                        \.eyeX, \.eyeY, \.eyeOpen, \.armL, \.armR, \.armLFwd, \.armRFwd, \.glow, \.dim]
}

public struct RigInput: Sendable {
    public var lookX = 0.0, lookY = 0.0, level = 0.0
    public var reducedMotion = false
    public var point: Look?  // screen target to look at and point to
    public init(lookX: Double = 0, lookY: Double = 0, level: Double = 0, reducedMotion: Bool = false, point: Look? = nil) {
        self.lookX = lookX; self.lookY = lookY; self.level = level; self.reducedMotion = reducedMotion; self.point = point
    }
}

public enum CharacterRig {
    static let tau = Double.pi * 2

    public static func blink(_ t: Double) -> Double {
        let period = 4.1
        let cycle = (t / period).rounded(.down)
        var jitter = (sin(cycle * 12.9898) * 43758.5453).truncatingRemainder(dividingBy: 1)
        if jitter < 0 { jitter += 1 }
        let start = cycle * period + jitter * (period - 0.4)
        let d = t - start
        if d < 0 || d > 0.16 { return 1 }
        return abs(d - 0.08) / 0.08
    }

    public static func target(_ state: CharacterState, t: Double, input: RigInput) -> Pose {
        let m = input.reducedMotion ? 0.15 : 1.0
        var p = Pose()
        let breath = sin(t * tau / 4.2)
        p.breathe = 1 + 0.012 * breath * (input.reducedMotion ? 0.5 : 1)
        p.bodyY = 0.006 * breath * m
        p.eyeOpen = input.reducedMotion ? 1 : blink(t)
        let lx = max(-1, min(1, input.lookX)), ly = max(-1, min(1, input.lookY))
        switch state {
        case .idle:
            p.headYaw = lx * 0.28 + sin(t * 0.37) * 0.04 * m
            p.headPitch = 0.05 - ly * 0.12 + sin(t * 0.23) * 0.02 * m
            p.headRoll = sin(t * 0.29) * 0.025 * m
            p.eyeX = lx * 0.5; p.eyeY = ly * 0.3
            p.sway = sin(t * 0.31) * 0.012 * m
        case .listening:
            p.lean = 0.06; p.headYaw = lx * 0.18; p.headPitch = -0.02 - ly * 0.08; p.headRoll = 0.09 * m
            p.eyeX = lx * 0.3; p.bodyY += input.level * 0.02 * m; p.glow = 0.35 + input.level * 0.65
        case .thinking:
            p.headYaw = 0.22 + sin(t * 0.8) * 0.08 * m; p.headPitch = -0.16; p.headRoll = -0.06
            p.eyeX = 0.55 + sin(t * 1.1) * 0.15 * m; p.eyeY = 0.55; p.armRFwd = 0.12
            p.glow = 0.25 + 0.15 * (0.5 + 0.5 * sin(t * 2.2)) * m
        case .speaking:
            let env = input.level > 0 ? input.level : 0.5 + 0.5 * sin(t * 7.5)
            p.headPitch = 0.02 - env * 0.05 * m; p.headYaw = lx * 0.2 + sin(t * 0.9) * 0.05 * m
            p.headRoll = sin(t * 1.3) * 0.03 * m; p.eyeX = lx * 0.4; p.bodyY += env * 0.006 * m
            p.armL = 0.2 + env * 0.03 * m; p.armR = 0.2 + env * 0.03 * m; p.glow = 0.2 + env * 0.3
        case .executing:
            p.headPitch = 0.16; p.headYaw = -0.08; p.eyeY = -0.5
            p.armRFwd = 0.55 + sin(t * 3.2) * 0.06 * m; p.armR = 0.28; p.armLFwd = 0.18
            p.glow = 0.45 + 0.2 * sin(t * 3.2) * m
        case .success:
            p.bodyY += abs(sin(t * 4.0)) * 0.025 * m; p.headRoll = 0.12; p.headPitch = -0.08
            p.armL = 0.55; p.armR = 0.55; p.armLFwd = 0.35; p.armRFwd = 0.35; p.eyeOpen = min(p.eyeOpen, 0.75); p.glow = 0.8
        case .confused:
            p.headRoll = -0.24; p.headYaw = 0.1; p.headPitch = -0.04; p.eyeY = 0.3; p.armRFwd = 0.45; p.armR = 0.35
        case .concerned:
            p.headPitch = 0.2; p.headRoll = 0.05; p.eyeY = -0.35; p.armL = 0.08; p.armR = 0.08
            p.armLFwd = 0.25; p.armRFwd = 0.25; p.lean = 0.04; p.glow = 0.5
        case .sleeping:
            p.breathe = 1 + 0.02 * sin(t * tau / 6.5); p.bodyY = -0.02; p.headPitch = 0.34; p.headRoll = 0.08
            p.eyeOpen = 0.04; p.armL = 0.1; p.armR = 0.1; p.lean = 0.05; p.dim = 0.45
        case .error:
            let decay = max(0, 1 - t.truncatingRemainder(dividingBy: 6) / 1.5)
            p.headYaw = sin(t * 9) * 0.12 * decay * m; p.headPitch = 0.14; p.eyeOpen = min(p.eyeOpen, 0.6)
            p.eyeY = -0.2; p.armL = 0.1; p.armR = 0.1; p.glow = 0.9
        case .disconnected:
            p.headPitch = 0.28; p.eyeOpen = 0.35; p.armL = 0.08; p.armR = 0.08; p.dim = 0.7; p.breathe = 1; p.bodyY = 0
        }
        if let pt = input.point, state != .sleeping, state != .disconnected { applyPoint(&p, pt, m: m) }
        return p
    }

    /// Look at a screen target and point at it with the arm on that side (matches rig.ts applyPoint).
    public static func applyPoint(_ p: inout Pose, _ target: Look, m: Double = 1) {
        let tx = max(-1, min(1, target.x)), ty = max(-1, min(1, target.y))
        p.headYaw = tx * 0.35
        p.headPitch = -ty * 0.18
        p.eyeX = tx
        p.eyeY = ty
        let lift = 0.9 + ty * 0.45
        let spread = 0.25 + abs(tx) * 0.5
        if tx >= 0 { p.armRFwd = lift; p.armR = spread } else { p.armLFwd = lift; p.armL = spread }
        p.lean = 0.03 * m
    }

    public static func blend(_ current: Pose, _ target: Pose, dt: Double, rate: Double = 6) -> Pose {
        let dtc = max(0, min(dt, 0.25))
        let k = 1 - exp(-rate * dtc)
        var out = current
        for key in Pose.keys {
            let kk: Double
            if key == \Pose.eyeOpen { kk = 1 - exp(-30 * dtc) } else if key == \Pose.breathe || key == \Pose.bodyY { kk = 1 - exp(-12 * dtc) } else { kk = k }
            out[keyPath: key] = current[keyPath: key] + (target[keyPath: key] - current[keyPath: key]) * kk
        }
        return out
    }

    /// Frames per second for SceneKit: low when nothing happens, to save battery.
    public static func frameRate(for state: CharacterState, reducedMotion: Bool, lowPower: Bool) -> Int {
        if state == .sleeping || state == .disconnected { return 12 }
        if lowPower { return 20 }
        if reducedMotion { return 20 }
        return state == .idle ? 30 : 45
    }
}
