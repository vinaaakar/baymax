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
    /// Upper chest and shoulders with a soft rounded base. Nothing exists below it (no abdomen, legs, feet, floor).
    public static let torsoProfile: [SIMD2<Float>] = [[0.0, 1.0], [0.26, 1.005], [0.44, 1.03], [0.56, 1.09], [0.6, 1.18], [0.58, 1.3],
                                                      [0.5, 1.44], [0.38, 1.58], [0.24, 1.69], [0.12, 1.75], [0.0, 1.78]]
    public static let torsoDepthScale: Float = 0.82
    public static let armPivot = SIMD3<Float>(0.5, 1.36, 0)
    public static let armProfile: [SIMD2<Float>] = [[0.0, -0.36], [0.1, -0.345], [0.135, -0.29], [0.15, -0.18], [0.155, -0.06], [0.12, 0.03], [0.0, 0.06]]
    public static let armDepthScale: Float = 0.92
}

public struct FramingSpec: Equatable, Sendable {
    public var fovDeg = 28.0
    public var headTop = 2.03
    public var bustBottom = 1.0
    public var minHalfWidth = 0.5
    public var minHalfHeight = 0.6
    public var topMargin = 0.26
    public var anchorFromTop = 0.4
    public var maxHalfHeight = 1.0
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
