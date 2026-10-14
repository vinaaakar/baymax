import Foundation

/// Bust composition shared with the Mac app. Mirrors design/baymax-visual-spec.json (`avatar`, `framing`);
/// BaymaxKitTests checks every number against that file and its framing test vectors.
public enum BustSpec {
    public static let headPivot = SIMD3<Float>(0, 1.71, 0.03)
    public static let headCenter = SIMD3<Float>(0, 0.135, 0.04)
    public static let headRadii = SIMD3<Float>(0.26, 0.185, 0.22)
    public static let eyeX: Float = 0.085
    public static let eyeYOffset: Float = 0.012
    public static let eyeRadius: Float = 0.03
    public static let eyeLineRadius: Float = 0.0045
    /// Small rounded neck/shoulder collar under the head. Nothing exists below it (no torso, arms, legs, floor).
    public static let torsoProfile: [SIMD2<Float>] = [[0.0, 1.5], [0.14, 1.505], [0.24, 1.525], [0.3, 1.56], [0.32, 1.6], [0.31, 1.65],
                                                      [0.27, 1.7], [0.18, 1.75], [0.08, 1.77], [0.0, 1.775]]
    public static let torsoDepthScale: Float = 0.82
    public static let armPivot = SIMD3<Float>(0.5, 1.36, 0)
    /// Empty: the head-only design has no arms (the pivots remain so pointing poses still move the head).
    public static let armProfile: [SIMD2<Float>] = []
    public static let armDepthScale: Float = 0.92
}

public struct FramingSpec: Equatable, Sendable {
    public var fovDeg = 28.0
    public var headTop = 2.03
    public var bustBottom = 1.5
    public var minHalfWidth = 0.44
    public var minHalfHeight = 0.5
    public var topMargin = 0.16
    public var anchorFromTop = 0.42
    public var maxHalfHeight = 1.2
    public init() {}
}

public struct BustCamera: Equatable, Sendable {
    public var halfHeight: Double
    public var centerY: Double
    public var distance: Double
    public var fovDeg: Double
}

public enum BustFraming {
    /// Same algorithm as desktop/src/shared/framing.ts: fixed vertical FOV, head never cut, bust anchored high in tall views.
    public static func camera(aspect: Double, spec f: FramingSpec = FramingSpec()) -> BustCamera {
        let a = aspect.isFinite && aspect > 0 ? aspect : 1
        let halfV = min(max(f.minHalfHeight, f.minHalfWidth / a), f.maxHalfHeight)
        let bustCenter = (f.headTop + f.bustBottom) / 2
        var centerY = bustCenter - halfV * (1 - 2 * f.anchorFromTop)
        centerY = max(centerY, f.headTop + f.topMargin - halfV)
        let distance = halfV / tan(f.fovDeg / 2 * .pi / 180)
        return BustCamera(halfHeight: halfV, centerY: centerY, distance: distance, fovDeg: f.fovDeg)
    }
}
