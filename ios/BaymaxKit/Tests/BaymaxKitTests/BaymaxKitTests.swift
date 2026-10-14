import XCTest
@testable import BaymaxKit

/// Loads the shared protocol file from the repository so Swift, Python and TypeScript stay in sync.
private func loadSpec() throws -> [String: Any] {
    var url = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { url.deleteLastPathComponent() } // Tests/BaymaxKitTests/<file> -> repo root
    let data = try Data(contentsOf: url.appendingPathComponent("protocol/baymax-protocol.json"))
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

final class ProtocolParityTests: XCTestCase {
    func testCharacterTablesMatchSpec() throws {
        let spec = try loadSpec()
        XCTAssertEqual(CharacterState.allCases.map(\.rawValue), spec["characterStates"] as? [String])
        let transitions = spec["characterTransitions"] as! [String: [String]]
        for (from, to) in transitions {
            let mine = CharacterRules.transitions[CharacterState(rawValue: from)!]!.map(\.rawValue)
            XCTAssertEqual(Set(mine), Set(to), from)
        }
        let events = spec["characterEvents"] as! [String: String]
        XCTAssertEqual(events.count, CharacterEvent.allCases.count)
        for (e, target) in events { XCTAssertEqual(CharacterRules.eventTargets[CharacterEvent(rawValue: e)!]?.rawValue, target) }
        let rules = spec["characterRules"] as! [String: Any]
        XCTAssertEqual(Set(CharacterRules.sleepExitEvents.map(\.rawValue)), Set(rules["sleepExitEvents"] as! [String]))
        XCTAssertEqual(Set(CharacterRules.disconnectedExitEvents.map(\.rawValue)), Set(rules["disconnectedExitEvents"] as! [String]))
    }

    func testSharedVectors() throws {
        let spec = try loadSpec()
        for v in spec["characterTestVectors"] as! [[String: Any]] {
            let r = CharacterRules.next(from: CharacterState(rawValue: v["from"] as! String)!, event: CharacterEvent(rawValue: v["event"] as! String)!,
                                        verified: v["verified"] as? Bool ?? false)
            XCTAssertEqual(r.state.rawValue, v["expect"] as? String, "\(v)")
            XCTAssertEqual(r.accepted, !((v["rejected"] as? Bool) ?? false), "\(v)")
        }
    }

    func testMessageSchemasMatchSpec() throws {
        let spec = try loadSpec()
        let messages = (spec["messages"] as! [String: [String: [String: Any]]]).mapValues { $0.mapValues { m in
            m.compactMapValues { $0 as? [String: String] } } }
        for (dir, table) in [("clientToServer", BaymaxProtocol.clientToServer), ("serverToClient", BaymaxProtocol.serverToClient)] {
            XCTAssertEqual(Set(messages[dir]!.keys), Set(table.keys), dir)
            for (name, s) in messages[dir]! {
                XCTAssertEqual(s["required"]!, table[name]!.required.mapValues(\.rawValue), name)
                XCTAssertEqual(s["optional"] ?? [:], table[name]!.optional.mapValues(\.rawValue), name)
            }
            let raw = (spec["messages"] as! [String: [String: [String: Any]]])[dir]!
            for (name, s) in raw { XCTAssertEqual(s["since"] as? Int ?? 1, BaymaxProtocol.since[name] ?? 1, name) }
        }
        XCTAssertEqual(spec["version"] as? Int, BaymaxProtocol.version)
        XCTAssertEqual(spec["maxMessageBytes"] as? Int, BaymaxProtocol.maxMessageBytes)
    }
}

final class MessageCodecTests: XCTestCase {
    func testDecodesValidMessage() throws {
        let env = try MessageCodec.decode(#"{"v":1,"type":"character.state","id":"a1","ts":5,"payload":{"state":"LISTENING","extra":1}}"#)
        XCTAssertEqual(env.type, "character.state")
        XCTAssertEqual(env.string("state"), "LISTENING")
        XCTAssertNil(env.payload["extra"])
    }

    func testRejectsMalformed() {
        let cases: [(String, String)] = [
            ("{nope", "bad_json"),
            (#"{"v":3,"type":"pong","id":"a","payload":{}}"#, "version_unsupported"),
            (#"{"v":1,"type":"shell.exec","id":"a","payload":{}}"#, "unknown_type"),
            (#"{"v":1,"type":"estop.state","id":"a","payload":{"engaged":"yes"}}"#, "bad_payload"),
            (#"{"v":1,"type":"estop.state","id":"a","payload":{"engaged":1}}"#, "bad_payload"),
            (#"{"v":1,"type":"hello.ack","id":"a","payload":{"protocol":true,"server":"x"}}"#, "bad_payload"),
            (#"{"v":true,"type":"pong","id":"a","payload":{}}"#, "bad_envelope"),
        ]
        for (raw, code) in cases {
            XCTAssertThrowsError(try MessageCodec.decode(raw), raw) { XCTAssertEqual(($0 as? ProtocolError)?.code, code, raw) }
        }
        let big = #"{"v":1,"type":"pong","id":"a","payload":{"pad":""# + String(repeating: "x", count: 70_000) + #""}}"#
        XCTAssertThrowsError(try MessageCodec.decode(big)) { XCTAssertEqual(($0 as? ProtocolError)?.code, "too_large") }
    }

    func testEncodeValidatesOutbound() throws {
        let s = try Envelope(type: "user.text", payload: ["text": "hi", "speakReply": true]).encoded()
        XCTAssertTrue(s.contains("\"user.text\""))
        XCTAssertThrowsError(try Envelope(type: "user.text", payload: [:]).encoded())
        XCTAssertThrowsError(try Envelope(type: "voice.state", payload: ["state": "secretly-recording"]).encoded())
        XCTAssertThrowsError(try Envelope(type: "character.state", payload: ["state": "IDLE"]).encoded()) // server->client type
    }
}

final class PairingAndBackoffTests: XCTestCase {
    func testParsesQR() throws {
        let fp = String(repeating: "ab", count: 32)
        let p = try PairingPayload.parse(#"{"v":1,"host":"192.168.1.20","port":8766,"fp":"\#(fp)","code":"123456"}"#)
        XCTAssertEqual(p.url?.absoluteString, "wss://192.168.1.20:8766/v1/ws")
        XCTAssertThrowsError(try PairingPayload.parse(#"{"v":1,"host":"8.8.8.8","port":8766,"fp":"\#(fp)","code":"123456"}"#))
        XCTAssertThrowsError(try PairingPayload.parse(#"{"v":1,"host":"10.0.0.2","port":8766,"fp":"short","code":"123456"}"#))
        XCTAssertThrowsError(try PairingPayload.parse(#"{"v":1,"host":"10.0.0.2","port":8766,"fp":"\#(fp)","code":"12a456"}"#))
        XCTAssertThrowsError(try PairingPayload.parse("not json"))
        XCTAssertTrue(isPrivateLANHost("mac-mini.local"))
        XCTAssertTrue(isPrivateLANHost("172.20.1.1"))
        XCTAssertFalse(isPrivateLANHost("172.32.1.1"))
    }

    func testBackoffGrowsAndCaps() {
        var b = Backoff()
        let delays = (0..<8).map { _ in b.nextDelay(random: 1) }
        XCTAssertEqual(delays.prefix(4), [0.5, 1, 2, 4])
        XCTAssertEqual(delays.last, 16)
        b.reset()
        XCTAssertEqual(b.nextDelay(random: 1), 0.5)
    }

    func testAuthInputFormat() {
        XCTAssertEqual(String(decoding: AuthMessage.input(nonce: "n1", deviceId: "dev-1"), as: UTF8.self), "baymax-auth-v1|n1|dev-1")
        XCTAssertEqual(AuthMessage.hex([0, 255, 16]), "00ff10")
    }
}

final class DisplayAndRigTests: XCTestCase {
    func testOverlayPriorities() {
        var d = DisplayedCharacter()
        XCTAssertEqual(d.state, .disconnected)
        d.connected = true
        d.applyServer("EXECUTING")
        XCTAssertEqual(d.state, .executing)
        d.localListening = true
        XCTAssertEqual(d.state, .listening)
        d.applyServer("SLEEPING")
        XCTAssertEqual(d.state, .sleeping)
        XCTAssertFalse(d.applyServer("HACKED"))
    }

    func testRigFiniteAndReducedMotion() {
        for s in CharacterState.allCases {
            let p = CharacterRig.target(s, t: 3.3, input: RigInput())
            for k in Pose.keys { XCTAssertTrue(p[keyPath: k].isFinite, "\(s)") }
        }
        XCTAssertLessThan(CharacterRig.target(.sleeping, t: 1, input: RigInput()).eyeOpen, 0.1)
        XCTAssertLessThan(CharacterRig.frameRate(for: .sleeping, reducedMotion: false, lowPower: false),
                          CharacterRig.frameRate(for: .idle, reducedMotion: false, lowPower: false))
        var a = Pose()
        let target = CharacterRig.target(.success, t: 0, input: RigInput())
        for _ in 0..<120 { a = CharacterRig.blend(a, target, dt: 1.0 / 60) }
        XCTAssertEqual(a.armL, target.armL, accuracy: 0.01)
    }
}


final class GazeTests: XCTestCase {
    func testCursorToLookMatchesDesktopVectors() {
        // Same vectors as desktop/tests/gaze.test.ts
        let c = GazeConfig()
        let center = GazeMath.cursorToLook(vx: 0.5, vy: 0.5, cfg: c)
        XCTAssertEqual(center.x, 0, accuracy: 1e-9); XCTAssertEqual(center.y, 0.3, accuracy: 1e-9)
        let topLeft = GazeMath.cursorToLook(vx: 0, vy: 0, cfg: c)
        XCTAssertEqual(topLeft.x, -1, accuracy: 1e-9); XCTAssertEqual(topLeft.y, 0.72, accuracy: 1e-9)
        let bottomRight = GazeMath.cursorToLook(vx: 1, vy: 1, cfg: c)
        XCTAssertEqual(bottomRight.x, 1, accuracy: 1e-9); XCTAssertEqual(bottomRight.y, -0.12, accuracy: 1e-9)
        XCTAssertEqual(GazeMath.cursorToLook(vx: 3, vy: 0.5, cfg: c).x, 1) // other monitor: clamped
        XCTAssertEqual(GazeMath.cursorToLook(vx: .nan, vy: 0.5, cfg: c).x, 0)
    }

    func testTrackerStalenessOrderingAndPointing() {
        var t = GazeTracker()
        XCTAssertTrue(t.update(vx: 1, vy: 0.5, seq: 5, now: 0))
        XCTAssertFalse(t.update(vx: 0, vy: 0.5, seq: 4, now: 10)) // out of order
        XCTAssertEqual(t.look(now: 100).x, 1, accuracy: 1e-9)
        XCTAssertEqual(t.look(now: 2500 + 800 + 1).x, 0, accuracy: 1e-9) // decayed to rest
        t.pointAt(vx: 0, vy: 0.5, label: "Run", durationMs: 1000, now: 4000)
        XCTAssertEqual(t.look(now: 4100).x, -1, accuracy: 1e-9)
        XCTAssertNil(t.activePoint(now: 5200))
        t.reset()
        XCTAssertTrue(t.update(vx: 0.5, vy: 0.5, seq: 1, now: 6000))
    }

    func testPointGestureRaisesArmOnTargetSide() {
        let right = CharacterRig.target(.idle, t: 1, input: RigInput(point: Look(x: 0.8, y: 0.2)))
        XCTAssertGreaterThan(right.armRFwd, 0.8)
        let left = CharacterRig.target(.idle, t: 1, input: RigInput(point: Look(x: -0.8, y: 0.2)))
        XCTAssertGreaterThan(left.armLFwd, 0.8)
        let asleep = CharacterRig.target(.sleeping, t: 1, input: RigInput(point: Look(x: 0.8, y: 0.2)))
        XCTAssertLessThan(asleep.armRFwd, 0.1)
    }
}

/// design/baymax-visual-spec.json is the single source for the 2D face on both platforms.
final class VisualSpecParityTests: XCTestCase {
    private func loadVisualSpec() throws -> [String: Any] {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }
        let data = try Data(contentsOf: url.appendingPathComponent("design/baymax-visual-spec.json"))
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    private func pose(_ d: [String: Double]) -> Pose {
        var p = Pose()
        p.bodyY = d["bodyY"]!; p.breathe = d["breathe"]!; p.lean = d["lean"]!; p.sway = d["sway"]!
        p.headYaw = d["headYaw"]!; p.headPitch = d["headPitch"]!; p.headRoll = d["headRoll"]!
        p.eyeX = d["eyeX"]!; p.eyeY = d["eyeY"]!; p.eyeOpen = d["eyeOpen"]!
        p.armL = d["armL"]!; p.armR = d["armR"]!; p.armLFwd = d["armLFwd"]!; p.armRFwd = d["armRFwd"]!
        p.glow = d["glow"]!; p.dim = d["dim"]!
        return p
    }

    /// Allows a last-digit difference: the vectors were produced by JavaScript, whose sin/rounding may differ by 1e-6.
    private func assertEllipse(_ e: FaceEllipse, _ d: Any?, _ name: String, file: StaticString = #filePath, line: UInt = #line) {
        let w = d as! [String: Double]
        for (a, b) in [(e.cx, w["cx"]!), (e.cy, w["cy"]!), (e.rx, w["rx"]!), (e.ry, w["ry"]!)] {
            XCTAssertEqual(a, b, accuracy: 3e-6, name, file: file, line: line)
        }
    }

    func testFaceNumbersMatchSpec() throws {
        let f = try loadVisualSpec()["face2d"] as! [String: Any]
        let head = f["head"] as! [String: Double], eye = f["eye"] as! [String: Double], travel = f["travel"] as! [String: Double]
        XCTAssertEqual(FaceSpec.headRx, head["rx"]); XCTAssertEqual(FaceSpec.headRy, head["ry"]); XCTAssertEqual(FaceSpec.breatheGain, head["breatheGain"])
        XCTAssertEqual(FaceSpec.eyeX, eye["x"]); XCTAssertEqual(FaceSpec.eyeY, eye["y"]); XCTAssertEqual(FaceSpec.eyeRx, eye["rx"])
        XCTAssertEqual(FaceSpec.eyeRy, eye["ry"]); XCTAssertEqual(FaceSpec.eyeMinOpen, eye["minOpen"]); XCTAssertEqual(FaceSpec.eyeMargin, eye["margin"])
        XCTAssertEqual(FaceSpec.travelX, travel["x"]); XCTAssertEqual(FaceSpec.travelY, travel["y"])
        XCTAssertEqual(FaceSpec.yawFollow, travel["yawFollow"]); XCTAssertEqual(FaceSpec.pitchFollow, travel["pitchFollow"])
        XCTAssertEqual(FaceSpec.lineThickness, (f["line"] as! [String: Double])["thickness"])
        let shading = f["shading"] as! [String: Any]
        XCTAssertEqual(FaceSpec.skin, shading["skin"] as? String)
        XCTAssertEqual(FaceSpec.skinShade, shading["skinShade"] as? String)
    }

    func testFaceGeometryMatchesSharedVectors() throws {
        let spec = try loadVisualSpec()
        for v in spec["face2dTestVectors"] as! [[String: Any]] {
            let expr = FaceExpression(rawValue: v["expression"] as! String)!
            let pt = (v["point"] as? [String: Double]).map { Look(x: $0["x"]!, y: $0["y"]!) }
            let comp = FaceComposition(rawValue: v["composition"] as? String ?? "full")!
            let g = Face2D.geometry(pose(v["pose"] as! [String: Double]), expression: expr, point: pt, composition: comp)
            let want = v["geometry"] as! [String: Any]
            let name = v["name"] as! String
            assertEllipse(g.head, want["head"], name)
            XCTAssertEqual(g.headRotation, (want["head"] as! [String: Double])["rotation"]!, accuracy: 3e-6, name)
            assertEllipse(g.eyeL, want["eyeL"], name)
            assertEllipse(g.eyeR, want["eyeR"], name)
            let pupil = want["pupil"] as! [String: Double]
            XCTAssertEqual(g.pupil.x, pupil["x"]!, accuracy: 3e-6, name)
            XCTAssertEqual(g.pupil.y, pupil["y"]!, accuracy: 3e-6, name)
            if let p = want["pointer"] as? [String: Double] {
                XCTAssertEqual(g.pointer?.angle ?? .nan, p["angle"]!, accuracy: 3e-6, name)
                XCTAssertEqual(g.pointer?.x ?? .nan, p["x"]!, accuracy: 3e-6, name)
            } else {
                XCTAssertNil(g.pointer, name)
            }
        }
        for vv in spec["fitTestVectors"] as! [[String: Any]] {
            let v = vv.compactMapValues { $0 as? Double }
            let f = Face2D.fit(width: v["w"]!, height: v["h"]!)
            XCTAssertEqual(f.composition.rawValue, vv["composition"] as? String)
            XCTAssertEqual(f.scale, v["scale"]!, accuracy: 3e-6)
            XCTAssertEqual(f.cx, v["cx"]!, accuracy: 3e-6)
            XCTAssertEqual(f.cy, v["cy"]!, accuracy: 3e-6)
        }
        for v in spec["expressionTestVectors"] as! [[String: Any]] {
            let state = CharacterState(rawValue: v["state"] as! String)!
            let e = FaceExpression.from(state, cursorLive: v["cursorLive"] as! Bool, pointing: v["pointing"] as! Bool, taskKind: v["taskKind"] as? String)
            XCTAssertEqual(e.rawValue, v["expression"] as? String)
        }
    }

    func testRealCursorMovesPupilsAndTheyStayOnTheFace() {
        func at(_ vx: Double, _ vy: Double) -> FaceGeometry {
            let l = GazeMath.cursorToLook(vx: vx, vy: vy)
            return Face2D.geometry(CharacterRig.target(.idle, t: 1, input: RigInput(lookX: l.x, lookY: l.y, reducedMotion: true)), expression: .tracking)
        }
        XCTAssertGreaterThan(at(1, 0.5).pupil.x - at(0, 0.5).pupil.x, 0.5)
        XCTAssertLessThan(at(0.5, 0).pupil.y, at(0.5, 1).pupil.y)
        for vx in stride(from: -0.5, through: 1.5, by: 0.25) {
            for vy in stride(from: -0.5, through: 1.5, by: 0.25) {
                let g = at(vx, vy)
                for e in [g.eyeL, g.eyeR] {
                    for (dx, dy) in [(e.rx, 0.0), (-e.rx, 0.0), (0.0, e.ry), (0.0, -e.ry)] {
                        let x = e.cx + dx, y = e.cy + dy
                        XCTAssertLessThanOrEqual((x / g.head.rx) * (x / g.head.rx) + (y / g.head.ry) * (y / g.head.ry), 1)
                    }
                }
            }
        }
    }

    func testPaletteMatchesSpec() throws {
        let colors = try loadVisualSpec()["colors"] as! [String: String]
        XCTAssertEqual(colors["background"], "#180509")
        XCTAssertEqual(colors["accent"], "#B5122A")
        XCTAssertEqual(colors["highlight"], "#FF334F")
        XCTAssertEqual(colors["surface"], "#080305")
        XCTAssertEqual(colors["text"], "#F5F5F2")
    }
}


/// Landscape-first desk companion layout.
final class CompanionLayoutTests: XCTestCase {
    func testLandscapeIsFaceOnlyByDefault() {
        let l = CompanionLayout.compute(width: 844, height: 360, conversationOpen: false)  // iPhone 14 landscape, inside safe area
        XCTAssertTrue(l.landscape)
        XCTAssertEqual(l.mode, .faceOnly)
        XCTAssertEqual(l.faceWidthFraction, 1)
        XCTAssertLessThan(l.micDiameter, 64)  // compact controls leave the height to the face
        XCTAssertTrue(l.immersive)  // face fills the screen; bars overlaid on demand
        XCTAssertFalse(CompanionLayout.compute(width: 844, height: 360, conversationOpen: true).immersive)
        XCTAssertFalse(CompanionLayout.compute(width: 390, height: 760, conversationOpen: false).immersive)
    }

    func testLandscapeConversationSitsBesideASmallerFace() {
        let l = CompanionLayout.compute(width: 844, height: 360, conversationOpen: true)
        XCTAssertEqual(l.mode, .faceBesideConversation)
        XCTAssertGreaterThan(l.faceWidthFraction, 0.3)
        XCTAssertLessThan(l.faceWidthFraction, 0.5)
        XCTAssertEqual(l.replyLines, 0)  // the full conversation is already visible
    }

    func testPortraitAndNarrowUseASheet() {
        XCTAssertEqual(CompanionLayout.compute(width: 390, height: 760, conversationOpen: true).mode, .faceWithConversationSheet)
        XCTAssertEqual(CompanionLayout.compute(width: 520, height: 300, conversationOpen: true).mode, .faceWithConversationSheet)
    }

    func testPortraitShowsWholeHeadAndLandscapeIsADome() {
        for (w, h) in [(390.0, 844.0), (300.0, 300.0)] {
            let f = Face2D.fit(width: w, height: h)
            XCTAssertEqual(f.composition, .full)
            XCTAssertLessThanOrEqual(f.cx + f.scale * FaceSpec.headRx, w)
            XCTAssertGreaterThanOrEqual(f.cy - f.scale * FaceSpec.headRy, 0)
            XCTAssertLessThanOrEqual(f.cy + f.scale * FaceSpec.headRy, h)
        }
        for (w, h) in [(844.0, 390.0), (932.0, 430.0)] {
            let f = Face2D.fit(width: w, height: h)
            XCTAssertEqual(f.composition, .dome)
            XCTAssertGreaterThanOrEqual(f.cy, h)  // only the upper part shows
            XCTAssertGreaterThan(f.cy - f.scale * FaceSpec.headRy, 0)
            for lx in stride(from: -1.0, through: 1.0, by: 0.5) {
                for ly in stride(from: -1.0, through: 1.0, by: 0.5) {
                    let p = CharacterRig.target(.idle, t: 1, input: RigInput(lookX: lx, lookY: ly, reducedMotion: true))
                    let g = Face2D.geometry(p, expression: .tracking, composition: .dome)
                    for e in [g.eyeL, g.eyeR] { XCTAssertLessThan(f.cy + (g.head.cy + e.cy + e.ry) * f.scale, h) }
                }
            }
        }
    }
}
