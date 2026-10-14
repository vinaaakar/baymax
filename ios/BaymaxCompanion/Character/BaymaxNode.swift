import BaymaxKit
import SceneKit
import UIKit

/// Original, procedural Baymax-inspired healthcare robot (not an official Disney asset).
/// Geometry matches desktop/src/character/baymaxModel.ts (same profiles and proportions).
final class BaymaxNode: SCNNode {
    let body = SCNNode()
    let torso = SCNNode()
    let headPivot = SCNNode()
    let eyes = SCNNode()
    let eyeL = SCNNode()
    let eyeR = SCNNode()
    let armL = SCNNode()
    let armR = SCNNode()
    private let skin = SCNMaterial()
    private let face = SCNMaterial()

    static let torsoProfile: [SIMD2<Float>] = [[0, 0.2], [0.34, 0.22], [0.58, 0.34], [0.72, 0.58], [0.76, 0.86], [0.72, 1.12], [0.62, 1.36],
                                               [0.48, 1.56], [0.3, 1.7], [0.16, 1.76], [0, 1.78]]
    static let armProfile: [SIMD2<Float>] = [[0, -0.98], [0.11, -0.96], [0.16, -0.86], [0.17, -0.6], [0.18, -0.3], [0.19, -0.08], [0.14, 0.04], [0, 0.07]]
    static let legProfile: [SIMD2<Float>] = [[0, 0], [0.2, 0], [0.25, 0.04], [0.25, 0.2], [0.22, 0.34], [0, 0.38]]

    init(segments: Int = 48) {
        super.init()
        name = "baymax"
        skin.lightingModel = .physicallyBased
        skin.diffuse.contents = UIColor(red: 0xF5 / 255, green: 0xF5 / 255, blue: 0xF2 / 255, alpha: 1)
        skin.roughness.contents = 0.5
        skin.metalness.contents = 0.0
        skin.clearCoat.contents = 0.35
        skin.clearCoatRoughness.contents = 0.45
        face.lightingModel = .physicallyBased
        face.diffuse.contents = UIColor(red: 0x12 / 255, green: 0x12 / 255, blue: 0x14 / 255, alpha: 1)
        face.roughness.contents = 0.35

        addChildNode(body)
        torso.geometry = LatheGeometry.make(profile: CatmullRom.smooth(Self.torsoProfile, samples: 72), segments: segments, material: skin)
        torso.scale = SCNVector3(1, 1, 0.82)
        torso.castsShadow = true
        body.addChildNode(torso)

        for side: Float in [-1, 1] {
            let leg = SCNNode(geometry: LatheGeometry.make(profile: CatmullRom.smooth(Self.legProfile, samples: 24), segments: max(24, segments / 2), material: skin))
            leg.position = SCNVector3(side * 0.27, 0, 0.02)
            leg.scale = SCNVector3(1, 1, 1.15)
            addChildNode(leg)
        }

        headPivot.position = SCNVector3(0, 1.71, 0.03)
        body.addChildNode(headPivot)
        let head = SCNNode(geometry: { let s = SCNSphere(radius: 1); s.segmentCount = segments; s.materials = [skin]; return s }())
        let hr = SIMD3<Float>(0.26, 0.185, 0.22)
        let hy: Float = 0.135, hz: Float = 0.04
        head.scale = SCNVector3(hr.x, hr.y, hr.z)
        head.position = SCNVector3(0, hy, hz)
        headPivot.addChildNode(head)

        // Eyes joined by a thin line following the head's curvature.
        headPivot.addChildNode(eyes)
        func surfaceZ(_ x: Float, _ y: Float) -> Float {
            hz + hr.z * sqrt(max(0, 1 - pow(x / hr.x, 2) - pow((y - hy) / hr.y, 2)))
        }
        let eyeY = hy + 0.012, eyeX: Float = 0.085
        for (node, x) in [(eyeL, -eyeX), (eyeR, eyeX)] {
            let s = SCNSphere(radius: 0.03)
            s.materials = [face]
            node.geometry = s
            node.position = SCNVector3(x, eyeY, surfaceZ(x, eyeY) - 0.006)
            node.scale = SCNVector3(1, 1.05, 0.45)
            eyes.addChildNode(node)
        }
        let steps = 14
        for i in 0..<steps {
            let x0 = -eyeX + 2 * eyeX * Float(i) / Float(steps)
            let x1 = -eyeX + 2 * eyeX * Float(i + 1) / Float(steps)
            let a = SIMD3<Float>(x0, eyeY, surfaceZ(x0, eyeY) + 0.0015)
            let b = SIMD3<Float>(x1, eyeY, surfaceZ(x1, eyeY) + 0.0015)
            let seg = SCNCylinder(radius: 0.0045, height: CGFloat(simd_length(b - a)))
            seg.materials = [face]
            let n = SCNNode(geometry: seg)
            n.simdPosition = (a + b) / 2
            n.simdOrientation = simd_quatf(from: [0, 1, 0], to: simd_normalize(b - a))
            eyes.addChildNode(n)
        }

        for (pivot, side) in [(armL, Float(-1)), (armR, Float(1))] {
            pivot.position = SCNVector3(side * 0.56, 1.4, 0)
            let arm = SCNNode(geometry: LatheGeometry.make(profile: CatmullRom.smooth(Self.armProfile, samples: 40), segments: max(32, segments / 2), material: skin))
            arm.scale = SCNVector3(1, 1, 0.92)
            pivot.addChildNode(arm)
            let hand = SCNNode(geometry: { let s = SCNSphere(radius: 1); s.segmentCount = 32; s.materials = [skin]; return s }())
            hand.scale = SCNVector3(0.12, 0.15, 0.1)
            hand.position = SCNVector3(side * 0.005, -0.98, 0.02)
            pivot.addChildNode(hand)
            body.addChildNode(pivot)
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    func apply(_ p: Pose) {
        body.position.y = Float(p.bodyY)
        body.eulerAngles = SCNVector3(Float(p.lean), 0, Float(p.sway))
        torso.scale = SCNVector3(Float(1 + (1 - p.breathe) * -0.5), Float(p.breathe), 0.82)
        headPivot.position.y = Float(1.71 * p.breathe)
        // YXZ order like the desktop rig: yaw, then pitch, then roll
        let yaw = simd_quatf(angle: Float(p.headYaw), axis: [0, 1, 0])
        let pitch = simd_quatf(angle: Float(p.headPitch), axis: [1, 0, 0])
        let roll = simd_quatf(angle: Float(p.headRoll), axis: [0, 0, 1])
        headPivot.simdOrientation = yaw * pitch * roll
        eyes.position = SCNVector3(Float(p.eyeX * 0.012), Float(p.eyeY * 0.008), 0)
        let open = Float(max(0.04, min(1, p.eyeOpen)))
        eyeL.scale.y = 1.05 * open
        eyeR.scale.y = 1.05 * open
        armL.eulerAngles = SCNVector3(Float(-p.armLFwd), 0, Float(-p.armL))
        armR.eulerAngles = SCNVector3(Float(-p.armRFwd), 0, Float(p.armR))
    }
}

enum CatmullRom {
    static func smooth(_ pts: [SIMD2<Float>], samples: Int) -> [SIMD2<Float>] {
        guard pts.count > 2 else { return pts }
        var out: [SIMD2<Float>] = []
        let perSeg = max(1, samples / (pts.count - 1))
        for i in 0..<(pts.count - 1) {
            let p0 = pts[max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[min(pts.count - 1, i + 2)]
            for s in 0..<perSeg {
                let t = Float(s) / Float(perSeg), t2 = t * t, t3 = t2 * t
                var v = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
                v.x = max(0, v.x)
                out.append(v)
            }
        }
        out.append(pts[pts.count - 1])
        return out
    }
}

enum LatheGeometry {
    /// Revolve a (radius, y) profile around the Y axis with smooth normals.
    static func make(profile: [SIMD2<Float>], segments: Int, material: SCNMaterial) -> SCNGeometry {
        var positions: [SCNVector3] = []
        var normals: [SCNVector3] = []
        var indices: [UInt32] = []
        let rows = profile.count
        // per-profile-point 2D normals (perpendicular to the profile tangent)
        let n2: [SIMD2<Float>] = (0..<rows).map { i in
            let a = profile[max(0, i - 1)], b = profile[min(rows - 1, i + 1)]
            let t = simd_normalize(b - a + SIMD2<Float>(0, 1e-6))
            return SIMD2<Float>(t.y, -t.x)
        }
        for s in 0...segments {
            let ang = Float(s) / Float(segments) * 2 * .pi
            let c = cos(ang), sn = sin(ang)
            for i in 0..<rows {
                let p = profile[i]
                positions.append(SCNVector3(p.x * c, p.y, p.x * sn))
                normals.append(SCNVector3(n2[i].x * c, n2[i].y, n2[i].x * sn))
            }
        }
        for s in 0..<segments {
            for i in 0..<(rows - 1) {
                let a = UInt32(s * rows + i), b = UInt32((s + 1) * rows + i)
                indices += [a, b, a + 1, b, b + 1, a + 1]
            }
        }
        let geo = SCNGeometry(sources: [SCNGeometrySource(vertices: positions), SCNGeometrySource(normals: normals)],
                              elements: [SCNGeometryElement(indices: indices, primitiveType: .triangles)])
        geo.materials = [material]
        material.isDoubleSided = true
        return geo
    }
}
