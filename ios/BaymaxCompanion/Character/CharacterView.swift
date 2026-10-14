import BaymaxKit
import SceneKit
import SwiftUI

/// SceneKit view of the companion. Animation is driven only by the displayed (real) state.
struct CharacterView: UIViewRepresentable {
    var state: CharacterState
    var level: Double
    var gaze: GazeSource

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = .clear
        view.antialiasingMode = .multisampling4X
        view.isUserInteractionEnabled = false
        view.scene = context.coordinator.scene
        view.pointOfView = context.coordinator.camera
        view.delegate = context.coordinator
        view.isPlaying = true
        view.rendersContinuously = true
        view.isAccessibilityElement = false
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        let c = context.coordinator
        c.lock.lock()
        c.state = state
        c.level = level
        c.lock.unlock()
        c.gaze = gaze
        let reduced = UIAccessibility.isReduceMotionEnabled
        view.preferredFramesPerSecond = CharacterRig.frameRate(for: state, reducedMotion: reduced, lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled)
    }

    final class Coordinator: NSObject, SCNSceneRendererDelegate {
        let scene = SCNScene()
        let camera = SCNNode()
        let baymax = BaymaxNode(segments: 48)
        let rim = SCNNode()
        let key = SCNNode()
        let lock = NSLock()
        var state: CharacterState = .idle
        var level = 0.0
        var gaze: GazeSource?
        private var pose = Pose()
        private var start: TimeInterval?
        private var last: TimeInterval = 0

        override init() {
            super.init()
            scene.background.contents = UIColor.clear
            // Bust framing (head + shoulders only), identical to the Mac: BaymaxKit.BustFraming.
            let cam = SCNCamera()
            cam.projectionDirection = .vertical
            cam.wantsHDR = false
            camera.camera = cam
            frame(aspect: 0.46)
            scene.rootNode.addChildNode(camera)

            let ambient = SCNNode()
            ambient.light = SCNLight()
            ambient.light!.type = .ambient
            ambient.light!.color = UIColor(red: 0.55, green: 0.35, blue: 0.37, alpha: 1) // red room bounce
            ambient.light!.intensity = 350
            scene.rootNode.addChildNode(ambient)

            key.light = SCNLight()
            key.light!.type = .directional
            key.light!.intensity = 1100
            key.light!.castsShadow = true
            key.light!.shadowMode = .deferred
            key.light!.shadowRadius = 8
            key.light!.shadowSampleCount = 8
            key.light!.shadowColor = UIColor(white: 0, alpha: 0.4)
            key.position = SCNVector3(2.5, 4.5, 4)
            key.look(at: SCNVector3(0, 1, 0))
            scene.rootNode.addChildNode(key)

            rim.light = SCNLight()
            rim.light!.type = .omni
            rim.light!.color = UIColor(red: 1, green: 0.2, blue: 0.31, alpha: 1) // #FF334F
            rim.light!.intensity = 600
            rim.light!.attenuationEndDistance = 9
            rim.position = SCNVector3(0, 2.3, -1.6)
            scene.rootNode.addChildNode(rim)

            scene.rootNode.addChildNode(baymax)
        }

        private var framedAspect = 0.0

        /// Re-frames only when the view's shape changes (rotation, split view, first layout).
        func frame(aspect: Double) {
            guard abs(aspect - framedAspect) > 0.001 else { return }
            framedAspect = aspect
            let f = BustFraming.camera(aspect: aspect)
            camera.camera?.fieldOfView = CGFloat(f.fovDeg)
            camera.simdPosition = SIMD3<Float>(0, Float(f.centerY), Float(f.distance))
            camera.simdOrientation = simd_quatf(angle: 0, axis: [0, 1, 0]) // looks straight ahead (-Z)
        }

        func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
            let vp = renderer.currentViewport // render-thread safe (unlike UIView.bounds)
            if vp.width > 0, vp.height > 0 { frame(aspect: Double(vp.width / vp.height)) }
            if start == nil { start = time; last = time }
            let dt = time - last
            last = time
            lock.lock()
            let s = state, lvl = level
            lock.unlock()
            let reduced = UIAccessibility.isReduceMotionEnabled
            // Real cursor position from the Mac (or rest pose when no samples arrive).
            let g = gaze?.current() ?? (look: Look(x: 0, y: 0.3), point: nil, live: false)
            let input = RigInput(lookX: g.look.x, lookY: g.look.y, level: lvl, reducedMotion: reduced, point: g.point)
            let target = CharacterRig.target(s, t: time - (start ?? time), input: input)
            // Follow the cursor quickly (low lag) but keep body motion soft.
            pose = CharacterRig.blend(pose, target, dt: dt, rate: reduced ? 14 : (g.live || g.point != nil ? 12 : 5))
            baymax.apply(pose)
            rim.light?.intensity = CGFloat(300 + pose.glow * 1100)
            key.light?.intensity = CGFloat(1100 * (1 - pose.dim * 0.55))
        }
    }
}
