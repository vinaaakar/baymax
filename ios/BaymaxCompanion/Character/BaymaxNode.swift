import BaymaxKit
import Foundation

/// Animation state for the 2D face: blends the rig pose toward the real state every frame and turns it into
/// face geometry (BaymaxKit.Face2D, the same math as the Mac).
///
/// (This file used to hold the SceneKit model. The 3D renderer is gone; the file name is kept only so the existing
/// Xcode project picks this code up without running xcodegen again.)
final class FaceAnimator {
    private var pose = Pose()
    private var start: Double?
    private var last = 0.0
    private(set) var time = 0.0
    private(set) var expression: FaceExpression = .idle
    private(set) var geometry = Face2D.geometry(Pose(), expression: .idle)

    /// Advances to `now` (seconds) and returns this frame's face. Gaze comes only from real Mac cursor samples
    /// and verified screen targets (GazeSource); with no samples the face rests.
    func step(now: Double, state: CharacterState, level: Double, gaze: GazeSource, taskKind: String?, reducedMotion: Bool,
              composition: FaceComposition = .full) -> FaceGeometry {
        if start == nil { start = now; last = now }
        let dt = now - last
        last = now
        time = now - (start ?? now)
        let g = gaze.current()
        let input = RigInput(lookX: g.look.x, lookY: g.look.y, level: level, reducedMotion: reducedMotion, point: g.point)
        let target = CharacterRig.target(state, t: time, input: input)
        // Follow the cursor quickly (low lag) but keep everything else soft.
        pose = CharacterRig.blend(pose, target, dt: dt, rate: reducedMotion ? 14 : (g.live || g.point != nil ? 12 : 5))
        expression = FaceExpression.from(state, cursorLive: g.live, pointing: g.point != nil, taskKind: taskKind)
        geometry = Face2D.geometry(pose, expression: expression, point: g.point, composition: composition)
        gaze.recordFace(expression: expression, pupil: geometry.pupil)
        return geometry
    }
}
