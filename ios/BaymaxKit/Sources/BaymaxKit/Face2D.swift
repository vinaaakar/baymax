import Foundation

/// 2D vector face shared with the Mac. Exact port of desktop/src/shared/face2d.ts; numbers mirror
/// design/baymax-visual-spec.json "face2d" and BaymaxKitTests checks both against its test vectors.
///
/// Face units: origin at the head centre, x right, y DOWN (same as SwiftUI), head half-width = 1.
/// Nothing here is 3D: depth comes from the eye group sliding over the face, the eye nearest the edge narrowing,
/// and a small head parallax.
public enum FaceExpression: String, CaseIterable, Sendable {
    case idle = "IDLE", tracking = "TRACKING", listening = "LISTENING", thinking = "THINKING", researching = "RESEARCHING"
    case executing = "EXECUTING", speaking = "SPEAKING", pointing = "POINTING", success = "SUCCESS", confused = "CONFUSED"
    case concerned = "CONCERNED", sleeping = "SLEEPING", error = "ERROR", disconnected = "DISCONNECTED"

    /// What the face shows, derived only from real state: backend state, live cursor samples, real targets and tasks.
    public static func from(_ state: CharacterState, cursorLive: Bool, pointing: Bool, taskKind: String?) -> FaceExpression {
        switch state {
        case .sleeping: return .sleeping
        case .disconnected: return .disconnected
        case .error: return .error
        case .listening: return .listening
        case .speaking: return .speaking
        default: break
        }
        if pointing { return .pointing }
        if state == .executing || state == .thinking { return taskKind == "research" ? .researching : (state == .executing ? .executing : .thinking) }
        if state == .idle { return cursorLive ? .tracking : .idle }
        return FaceExpression(rawValue: state.rawValue) ?? .idle
    }
}

public enum FaceSpec {
    public static let headRx = 1.0, headRy = 0.74, breatheGain = 0.6
    public static let eyeX = 0.36, eyeY = 0.0, eyeRx = 0.105, eyeRy = 0.13, eyeMinOpen = 0.012, eyeMargin = 0.08
    public static let lineThickness = 0.024
    public static let travelX = 0.30, travelY = 0.2, yawFollow = 0.25, pitchFollow = 0.15
    public static let foreshortenWidth = 0.28, foreshortenSpacing = 0.1
    public static let parallaxYaw = 0.12, parallaxPitch = 0.1, parallaxBody = 1.5
    public static let pointerRadius = 1.1
    public static let fitWidthFrac = 0.7, fitHeightFrac = 0.56, fitCenterY = 0.42
    public static let fitLandscapeHeightFrac = 0.94, fitLandscapeCenterY = 0.5, fitParallaxRoom = 0.08
    /// Per-expression eye scale (left, right).
    public static let eyeScale: [FaceExpression: (Double, Double)] = [.confused: (1, 0.82), .concerned: (0.94, 0.94)]
    // Shading (hex): see design spec face2d.shading.
    public static let skin = "#F7F6F3", skinShade = "#D9D4D2", rimShade = "#B9AFAF", shadow = "#0A0204", eyeHighlight = "#3A3A40"
    public static let highlightCenter = (-0.35, -0.45), shadowOffsetY = 0.08, shadowBlur = 0.12
}

public struct FaceEllipse: Equatable, Sendable {
    public var cx, cy, rx, ry: Double
}

public struct FaceGeometry: Equatable, Sendable {
    public var head: FaceEllipse
    public var headRotation: Double  // radians (roll) about the head centre
    public var eyeL: FaceEllipse     // head-local face units (rotate with the head)
    public var eyeR: FaceEllipse
    public var lineThickness: Double
    public var pupil: (x: Double, y: Double)
    public var pointer: (angle: Double, x: Double, y: Double)?
    public var glow: Double
    public var dim: Double

    public static func == (a: FaceGeometry, b: FaceGeometry) -> Bool {
        a.head == b.head && a.headRotation == b.headRotation && a.eyeL == b.eyeL && a.eyeR == b.eyeR && a.pupil == b.pupil
            && a.pointer?.angle == b.pointer?.angle && a.pointer?.x == b.pointer?.x && a.pointer?.y == b.pointer?.y
            && a.glow == b.glow && a.dim == b.dim
    }
}

public struct FaceFit: Equatable, Sendable {
    public var scale: Double  // points per face unit
    public var cx: Double     // resting head centre
    public var cy: Double
}

public enum Face2D {
    static func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double { v.isFinite ? min(hi, max(lo, v)) : 0 }
    static func r6(_ v: Double) -> Double { (v * 1e6).rounded() / 1e6 }

    /// Pose (CharacterRig) -> 2D face geometry. `point` is the look target (-1..1, x right+, y up+) while pointing.
    public static func geometry(_ pose: Pose, expression: FaceExpression, point: Look? = nil) -> FaceGeometry {
        typealias F = FaceSpec
        let hx = clamp(pose.headYaw, -0.6, 0.6) * F.parallaxYaw
        let hy = clamp(pose.headPitch, -0.6, 0.6) * F.parallaxPitch - pose.bodyY * F.parallaxBody
        let breathe = 1 + (pose.breathe - 1) * F.breatheGain
        let head = FaceEllipse(cx: r6(hx), cy: r6(hy), rx: r6(F.headRx), ry: r6(F.headRy * breathe))

        let maxX = F.headRx - F.eyeX - F.eyeRx - F.eyeMargin
        let maxY = F.headRy - abs(F.eyeY) - F.eyeRy - F.eyeMargin
        let gx = clamp(clamp(pose.eyeX, -1, 1) * F.travelX + clamp(pose.headYaw, -0.6, 0.6) * F.yawFollow, -maxX, maxX)
        let gy = clamp(-clamp(pose.eyeY, -1, 1) * F.travelY + clamp(pose.headPitch, -0.6, 0.6) * F.pitchFollow, -maxY, maxY)
        let turn = maxX > 0 ? gx / maxX : 0
        let spacing = F.eyeX * (1 - F.foreshortenSpacing * abs(turn))
        let open = clamp(pose.eyeOpen, 0, 1)
        let ry = max(F.eyeMinOpen, F.eyeRy * open)
        let (sl, sr) = F.eyeScale[expression] ?? (1, 1)
        let narrowL = 1 - F.foreshortenWidth * max(0, -turn)
        let narrowR = 1 - F.foreshortenWidth * max(0, turn)
        let eyeL = FaceEllipse(cx: r6(gx - spacing), cy: r6(F.eyeY + gy), rx: r6(F.eyeRx * narrowL * sl), ry: r6(ry * sl))
        let eyeR = FaceEllipse(cx: r6(gx + spacing), cy: r6(F.eyeY + gy), rx: r6(F.eyeRx * narrowR * sr), ry: r6(ry * sr))

        var pointer: (angle: Double, x: Double, y: Double)?
        if expression == .pointing, let p = point {
            let tx = clamp(p.x, -1, 1), ty = clamp(p.y, -1, 1)
            let angle = atan2(-ty, tx == 0 && ty == 0 ? 1 : tx)
            pointer = (r6(angle), r6(head.cx + cos(angle) * F.headRx * F.pointerRadius), r6(head.cy + sin(angle) * F.headRy * F.pointerRadius))
        }
        return FaceGeometry(head: head, headRotation: r6(clamp(pose.headRoll, -0.5, 0.5)), eyeL: eyeL, eyeR: eyeR,
                            lineThickness: F.lineThickness, pupil: (r6(gx), r6(gy)), pointer: pointer,
                            glow: clamp(pose.glow, 0, 1), dim: clamp(pose.dim, 0, 1))
    }

    /// Points per face unit and the head's resting centre for a view of w x h. Landscape shows a larger face.
    public static func fit(width w: Double, height h: Double) -> FaceFit {
        typealias F = FaceSpec
        let W = w > 0 && w.isFinite ? w : 1, H = h > 0 && h.isFinite ? h : 1
        let landscape = W / H >= 1.2
        let heightFrac = landscape ? F.fitLandscapeHeightFrac : F.fitHeightFrac
        let scale = min(W * F.fitWidthFrac / (2 * F.headRx), H * heightFrac / (2 * F.headRy * (1 + F.fitParallaxRoom)))
        return FaceFit(scale: r6(scale), cx: r6(W / 2), cy: r6(H * (landscape ? F.fitLandscapeCenterY : F.fitCenterY)))
    }
}
