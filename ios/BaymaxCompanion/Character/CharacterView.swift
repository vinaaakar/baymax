import BaymaxKit
import SceneKit
import SwiftUI

/// SceneKit view of the companion. Animation is driven only by the displayed (real) state.
struct CharacterView: UIViewRepresentable {
    var state: CharacterState
    var level: Double
    var look: CGPoint

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
        c.look = look
        c.lock.unlock()
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
        var look = CGPoint.zero
        private var pose = Pose()
        private var start: TimeInterval?
        private var last: TimeInterval = 0

        override init() {
            super.init()
            scene.background.contents = UIColor.clear
            let cam = SCNCamera()
            cam.fieldOfView = 30
            cam.wantsHDR = false
            camera.camera = cam
            camera.position = SCNVector3(0, 1.25, 6.8)
            camera.look(at: SCNVector3(0, 1.08, 0))
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

            let floor = SCNNode(geometry: SCNPlane(width: 4.4, height: 4.4))
            floor.eulerAngles.x = -.pi / 2
            let fm = SCNMaterial()
            fm.lightingModel = .shadowOnly
            floor.geometry!.materials = [fm]
            scene.rootNode.addChildNode(floor)
            scene.rootNode.addChildNode(baymax)
        }

        func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
            if start == nil { start = time; last = time }
            let dt = time - last
            last = time
            lock.lock()
            let s = state, lvl = level, lk = look
            lock.unlock()
            let reduced = UIAccessibility.isReduceMotionEnabled
            let target = CharacterRig.target(s, t: time - (start ?? time), input: RigInput(lookX: lk.x, lookY: lk.y, level: lvl, reducedMotion: reduced))
            pose = CharacterRig.blend(pose, target, dt: dt, rate: reduced ? 14 : 5)
            baymax.apply(pose)
            rim.light?.intensity = CGFloat(300 + pose.glow * 1100)
            key.light?.intensity = CGFloat(1100 * (1 - pose.dim * 0.55))
        }
    }
}
