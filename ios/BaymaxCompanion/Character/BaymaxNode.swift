import BaymaxKit
import SceneKit
import UIKit

/// Original, procedural Baymax-inspired healthcare robot (not an official Disney asset).
/// Head composition: the head and a small rounded neck/shoulder collar only — no torso, arms, legs or floor.
/// Numbers come from BaymaxKit.BustSpec (= design/baymax-visual-spec.json), shared with desktop/src/character/baymaxModel.ts.
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
        torso.geometry = LatheGeometry.make(profile: CatmullRom.smooth(BustSpec.torsoProfile, samples: 72), segments: segments, material: skin)
        torso.scale = SCNVector3(1, 1, BustSpec.torsoDepthScale)
        torso.castsShadow = true
        body.addChildNode(torso)

        headPivot.simdPosition = BustSpec.headPivot
        body.addChildNode(headPivot)
        let head = SCNNode(geometry: { let s = SCNSphere(radius: 1); s.segmentCount = segments; s.materials = [skin]; return s }())
        let hr = BustSpec.headRadii
        let hy = BustSpec.headCenter.y, hz = BustSpec.headCenter.z
        head.scale = SCNVector3(hr.x, hr.y, hr.z)
        head.position = SCNVector3(0, hy, hz)
        headPivot.addChildNode(head)

        // Eyes joined by a thin line following the head's curvature.
        headPivot.addChildNode(eyes)
        func surfaceZ(_ x: Float, _ y: Float) -> Float {
            hz + hr.z * sqrt(max(0, 1 - pow(x / hr.x, 2) - pow((y - hy) / hr.y, 2)))
        }
        let eyeY = hy + BustSpec.eyeYOffset, eyeX = BustSpec.eyeX
        for (node, x) in [(eyeL, -eyeX), (eyeR, eyeX)] {
            let s = SCNSphere(radius: CGFloat(BustSpec.eyeRadius))
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
            let seg = SCNCylinder(radius: CGFloat(BustSpec.eyeLineRadius), height: CGFloat(simd_length(b - a)))
            seg.materials = [face]
            let n = SCNNode(geometry: seg)
            n.simdPosition = (a + b) / 2
            n.simdOrientation = simd_quatf(from: [0, 1, 0], to: simd_normalize(b - a))
            eyes.addChildNode(n)
        }

        for (pivot, side) in [(armL, Float(-1)), (armR, Float(1))] {
            pivot.simdPosition = SIMD3<Float>(side * BustSpec.armPivot.x, BustSpec.armPivot.y, BustSpec.armPivot.z)
            if BustSpec.armProfile.count > 1 {  // head-only design: no arm geometry; pivots stay so poses still apply
                let arm = SCNNode(geometry: LatheGeometry.make(profile: CatmullRom.smooth(BustSpec.armProfile, samples: 40), segments: max(32, segments / 2), material: skin))
                arm.scale = SCNVector3(1, 1, BustSpec.armDepthScale)
                pivot.addChildNode(arm)
            }
            body.addChildNode(pivot)
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    func apply(_ p: Pose) {
        body.position.y = Float(p.bodyY)
        body.eulerAngles = SCNVector3(Float(p.lean), 0, Float(p.sway))
        torso.scale = SCNVector3(Float(1 + (1 - p.breathe) * -0.5), Float(p.breathe), BustSpec.torsoDepthScale)
        headPivot.position.y = BustSpec.headPivot.y * Float(p.breathe)
        // YXZ order like the desktop rig: yaw, then pitch, then roll
        let yaw = simd_quatf(angle: Float(p.headYaw), axis: [0, 1, 0])
        let pitch = simd_quatf(angle: Float(p.headPitch), axis: [1, 0, 0])
        let roll = simd_quatf(angle: Float(p.headRoll), axis: [0, 0, 1])
        headPivot.simdOrientation = yaw * pitch * roll
        // Same as the Mac: rotate about the neck pivot so the eyes slide along the curved face.
        eyes.eulerAngles = SCNVector3(-Float(p.eyeY) * BustSpec.eyeTravel.y, Float(p.eyeX) * BustSpec.eyeTravel.x, 0)
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
