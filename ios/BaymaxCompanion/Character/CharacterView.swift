import BaymaxKit
import SwiftUI

/// The companion face: flat 2D vector art drawn with SwiftUI paths, gradients and layers (no SceneKit, no 3D model).
/// Same face, spec and math as the Mac (design/baymax-visual-spec.json "face2d", BaymaxKit.Face2D).
/// Animation is driven only by the displayed (real) state, real cursor samples and real tasks.
struct CharacterView: View {
    var state: CharacterState
    var level: Double
    var gaze: GazeSource
    var taskKind: String? = nil
    var progress: Double? = nil

    @State private var animator = FaceAnimator()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let fps = CharacterRig.frameRate(for: state, reducedMotion: reduceMotion, lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled)
        TimelineView(.animation(minimumInterval: 1.0 / Double(fps))) { timeline in
            Canvas { ctx, size in
                let fit = Face2D.fit(width: size.width, height: size.height)
                let g = animator.step(now: timeline.date.timeIntervalSinceReferenceDate, state: state, level: level, gaze: gaze,
                                      taskKind: taskKind, reducedMotion: reduceMotion, composition: fit.composition)
                FacePainter(g: g, expression: animator.expression, fit: fit,
                            size: size, level: level, progress: progress, t: animator.time).draw(&ctx)
            }
        }
        .accessibilityHidden(true)
    }
}

/// Draws one frame. Layers, back to front: stage glow, rings/arcs, head (gradient + soft shadow), eye line, eyes,
/// then indicators (pointing chevron and target lock, thinking dots, research magnifier, sleep "z").
private struct FacePainter {
    let g: FaceGeometry
    let expression: FaceExpression
    let fit: FaceFit
    let size: CGSize
    let level: Double
    let progress: Double?
    let t: Double

    private var s: Double { fit.scale }
    private var hx: Double { fit.cx + g.head.cx * s }
    private var hy: Double { fit.cy + g.head.cy * s }

    // Indicator anchors: beside the whole head (portrait), or above the dome (landscape, where the head fills the width).
    private var dome: Bool { fit.composition == .dome }
    private var top: Double { hy - g.head.ry * s }
    private func dot(_ i: Int) -> CGPoint {
        dome ? CGPoint(x: hx + Double(i - 1) * 0.13 * s, y: top - 0.1 * s)
             : CGPoint(x: hx + g.head.rx * s * 1.12 + Double(i) * 0.13 * s, y: hy - g.head.ry * s * (0.62 + Double(i) * 0.12))
    }
    private var magnifier: CGPoint {
        if dome {
            let a = Double.pi * 1.5 + sin(t * 0.9) * 0.9
            return CGPoint(x: hx + cos(a) * g.head.rx * s * 1.07, y: hy + sin(a) * g.head.ry * s * 1.12)
        }
        return CGPoint(x: hx + cos(t * 1.4) * g.head.rx * s * 1.18, y: hy + sin(t * 1.4) * g.head.ry * s * 0.85)
    }
    private var sleepZ: CGPoint {
        dome ? CGPoint(x: hx + g.head.rx * s * 0.45, y: hy - g.head.ry * s * (0.95 + 0.06 * sin(t)))
             : CGPoint(x: hx + g.head.rx * s * 1.05, y: hy - g.head.ry * s * (0.7 + 0.08 * sin(t)))
    }
    private var linkOff: CGPoint {
        dome ? CGPoint(x: hx, y: top - 0.12 * s) : CGPoint(x: hx + g.head.rx * s * 1.2, y: hy + g.head.ry * s * 0.6)
    }

    private func hex(_ h: String) -> Color { Color(hex: UInt32(h.dropFirst(), radix: 16) ?? 0xFFFFFF) }

    private func ring(_ k: Double) -> Path {
        Path(ellipseIn: CGRect(x: hx - g.head.rx * s * k, y: hy - g.head.ry * s * k, width: 2 * g.head.rx * s * k, height: 2 * g.head.ry * s * k))
    }

    func draw(_ ctx: inout GraphicsContext) {
        // Stage glow behind the head: rises with real activity (pose glow), dims when asleep/disconnected.
        var glow = ctx
        glow.opacity = (0.25 + g.glow * 0.55) * (1 - g.dim * 0.6)
        let gr = max(g.head.rx * s * 1.9, 1)
        glow.fill(Path(ellipseIn: CGRect(x: hx - gr, y: hy - g.head.ry * s * 2.1, width: 2 * gr, height: 4.2 * g.head.ry * s)),
                  with: .radialGradient(Gradient(colors: [Theme.glow.opacity(0.55), Theme.background.opacity(0)]),
                                        center: CGPoint(x: hx, y: hy), startRadius: 0, endRadius: gr * 1.05))

        switch expression {
        case .listening:
            // Hold-to-talk: a soft red halo that breathes and swells with the real microphone level.
            let pulse = 0.5 + 0.5 * sin(t * 4)
            ctx.drawLayer { layer in
                layer.addFilter(.blur(radius: 16 + level * 24))
                layer.fill(ring(1.03 + level * 0.08 + pulse * 0.02), with: .color(Theme.glow.opacity(0.5 + level * 0.45)))
            }
            ctx.stroke(ring(1.06 + level * 0.06), with: .color(Theme.glow.opacity(0.35 + level * 0.6)), lineWidth: 2 + level * 4)
        case .error:
            ctx.stroke(ring(1.06), with: .color(Color(hex: 0xFF5D6C).opacity(0.4 + 0.4 * abs(sin(t * 3)))), lineWidth: 3)
        case .executing:
            drawProgressArc(&ctx)
        default:
            break
        }

        // Head.
        var head = ctx
        head.translateBy(x: hx, y: hy)
        head.rotate(by: .radians(g.headRotation))
        let headRect = CGRect(x: -g.head.rx * s, y: -g.head.ry * s, width: 2 * g.head.rx * s, height: 2 * g.head.ry * s)
        head.drawLayer { layer in
            layer.addFilter(.shadow(color: hex(FaceSpec.shadow).opacity(0.55), radius: FaceSpec.shadowBlur * s, x: 0, y: FaceSpec.shadowOffsetY * s))
            let hl = CGPoint(x: FaceSpec.highlightCenter.0 * 0.35 * headRect.width, y: FaceSpec.highlightCenter.1 * 0.35 * headRect.height)
            layer.fill(Path(ellipseIn: headRect), with: .radialGradient(
                Gradient(stops: [.init(color: .white, location: 0), .init(color: hex(FaceSpec.skin), location: 0.45),
                                 .init(color: hex(FaceSpec.skinShade), location: 0.85), .init(color: hex(FaceSpec.rimShade), location: 1)]),
                center: hl, startRadius: 0, endRadius: 0.85 * max(headRect.width, headRect.height)))
        }

        if expression == .listening {
            // The face itself blushes red toward its edge while listening, stronger as you speak.
            let r = max(headRect.width, headRect.height) / 2
            head.fill(Path(ellipseIn: headRect), with: .radialGradient(
                Gradient(colors: [Theme.glow.opacity(0), Theme.glow.opacity(0.18 + level * 0.4)]),
                center: .zero, startRadius: r * 0.45, endRadius: r))
        }

        // Eyes joined by a thin line (head-local coordinates, so they roll with the head).
        var line = Path()
        line.move(to: CGPoint(x: g.eyeL.cx * s, y: g.eyeL.cy * s))
        line.addLine(to: CGPoint(x: g.eyeR.cx * s, y: g.eyeR.cy * s))
        head.stroke(line, with: .color(Theme.face), style: StrokeStyle(lineWidth: max(1, g.lineThickness * s), lineCap: .round))
        for e in [g.eyeL, g.eyeR] {
            head.fill(Path(ellipseIn: CGRect(x: (e.cx - e.rx) * s, y: (e.cy - e.ry) * s, width: 2 * e.rx * s, height: 2 * e.ry * s)), with: .color(Theme.face))
            if e.ry > 0.05 {
                let w = e.rx * 0.44 * s, h = e.ry * 0.32 * s
                head.fill(Path(ellipseIn: CGRect(x: (e.cx - e.rx * 0.32) * s - w / 2, y: (e.cy - e.ry * 0.42) * s - h / 2, width: w, height: h)),
                          with: .color(hex(FaceSpec.eyeHighlight).opacity(0.8)))
            }
        }

        drawIndicators(&ctx)
    }

    private func drawProgressArc(_ ctx: inout GraphicsContext) {
        // Known progress: a filled arc. Unknown: a short arc that travels (never a fake percentage).
        let known = progress.map { $0.isFinite } ?? false
        let start = known ? -Double.pi / 2 : t * 2.4
        let sweep = known ? max(0.02, min(1, progress!)) * 2 * .pi : .pi * 0.45
        let rx = g.head.rx * s * 1.07, ry = g.head.ry * s * 1.07
        var arc = Path()
        let steps = 48
        for i in 0...steps {
            let a = start + sweep * Double(i) / Double(steps)
            let p = CGPoint(x: hx + cos(a) * rx, y: hy + sin(a) * ry)
            if i == 0 { arc.move(to: p) } else { arc.addLine(to: p) }
        }
        ctx.stroke(arc, with: .color(Theme.glow.opacity(0.85)), style: StrokeStyle(lineWidth: 3, lineCap: .round))
    }

    private func drawIndicators(_ ctx: inout GraphicsContext) {
        switch expression {
        case .pointing:
            guard let p = g.pointer else { return }
            var arrow = ctx
            arrow.translateBy(x: fit.cx + p.x * s, y: fit.cy + p.y * s)
            arrow.rotate(by: .radians(p.angle))
            var head = Path()
            head.move(to: CGPoint(x: 0.13 * s, y: 0))
            head.addLine(to: CGPoint(x: -0.02 * s, y: -0.085 * s))
            head.addQuadCurve(to: CGPoint(x: -0.02 * s, y: 0.085 * s), control: CGPoint(x: 0.02 * s, y: 0))
            head.closeSubpath()
            arrow.fill(head, with: .color(Theme.glow))
            drawReticle(&ctx, angle: p.angle)
        case .thinking, .researching:
            for i in 0..<3 {
                let r = 0.035 * s
                let c = dot(i)
                ctx.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)),
                         with: .color(Theme.white.opacity(0.25 + 0.6 * max(0, sin(t * 3 - Double(i) * 0.8)))))
            }
            if expression == .researching {
                let c = magnifier
                let r = 0.07 * s
                ctx.stroke(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)), with: .color(Theme.white.opacity(0.85)),
                           lineWidth: max(1.5, 0.022 * s))
                var handle = Path()
                handle.move(to: CGPoint(x: c.x + 0.05 * s, y: c.y + 0.05 * s))
                handle.addLine(to: CGPoint(x: c.x + 0.12 * s, y: c.y + 0.12 * s))
                ctx.stroke(handle, with: .color(Theme.white.opacity(0.85)), style: StrokeStyle(lineWidth: max(1.5, 0.026 * s), lineCap: .round))
            }
        case .sleeping:
            ctx.draw(Text("z").font(.system(size: max(10, 0.2 * s), weight: .semibold)).foregroundColor(Theme.muted.opacity(0.7)),
                     at: sleepZ)
        case .disconnected:
            var link = Path()
            let x = linkOff.x, y = linkOff.y
            link.move(to: CGPoint(x: x - 0.1 * s, y: y)); link.addLine(to: CGPoint(x: x - 0.02 * s, y: y))
            link.move(to: CGPoint(x: x + 0.02 * s, y: y)); link.addLine(to: CGPoint(x: x + 0.1 * s, y: y))
            link.move(to: CGPoint(x: x - 0.03 * s, y: y - 0.05 * s)); link.addLine(to: CGPoint(x: x + 0.03 * s, y: y + 0.05 * s))
            ctx.stroke(link, with: .color(Theme.muted.opacity(0.8)), lineWidth: 2)
        default:
            break
        }
    }

    /// Target lock toward the target, at the edge of the face region.
    private func drawReticle(_ ctx: inout GraphicsContext, angle: Double) {
        let pad = 18.0, dx = cos(angle), dy = sin(angle)
        let rx = dx > 0 ? (size.width - pad - hx) / dx : dx < 0 ? (pad - hx) / dx : .infinity
        let ry = dy > 0 ? (size.height - pad - hy) / dy : dy < 0 ? (pad - hy) / dy : .infinity
        let reach = min(rx, ry)
        let c = CGPoint(x: hx + dx * reach, y: hy + dy * reach)
        let r = 9 + 2 * sin(t * 5)
        var p = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        for (ax, ay) in [(-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)] {
            p.move(to: CGPoint(x: c.x + ax * (r - 3), y: c.y + ay * (r - 3)))
            p.addLine(to: CGPoint(x: c.x + ax * (r + 5), y: c.y + ay * (r + 5)))
        }
        ctx.stroke(p, with: .color(Theme.glow), lineWidth: 2)
    }
}
