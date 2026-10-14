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

/// design/baymax-visual-spec.json is the single source for the bust composition on both platforms.
final class VisualSpecParityTests: XCTestCase {
    private func loadVisualSpec() throws -> [String: Any] {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }
        let data = try Data(contentsOf: url.appendingPathComponent("design/baymax-visual-spec.json"))
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    private func pairs(_ value: Any?) -> [SIMD2<Float>] {
        let rows = value as! [[Double]]
        return rows.map { SIMD2<Float>(Float($0[0]), Float($0[1])) }
    }

    func testFramingMatchesSharedVectors() throws {
        let spec = try loadVisualSpec()
        let f = spec["framing"] as! [String: Any]
        let mine = FramingSpec()
        XCTAssertEqual(mine.fovDeg, f["fovDeg"] as! Double)
        XCTAssertEqual(mine.headTop, f["headTop"] as! Double)
        XCTAssertEqual(mine.bustBottom, f["bustBottom"] as! Double)
        XCTAssertEqual(mine.minHalfWidth, f["minHalfWidth"] as! Double)
        XCTAssertEqual(mine.minHalfHeight, f["minHalfHeight"] as! Double)
        XCTAssertEqual(mine.topMargin, f["topMargin"] as! Double)
        XCTAssertEqual(mine.anchorFromTop, f["anchorFromTop"] as! Double)
        XCTAssertEqual(mine.maxHalfHeight, f["maxHalfHeight"] as! Double)
        XCTAssertEqual(mine.minHalfHeightWide, f["minHalfHeightWide"] as! Double)
        XCTAssertEqual(mine.wideFrom, f["wideFrom"] as! Double)
        XCTAssertEqual(mine.wideTo, f["wideTo"] as! Double)
        for v in spec["framingTestVectors"] as! [[String: Double]] {
            let c = BustFraming.camera(aspect: v["aspect"]!)
            XCTAssertEqual(c.halfHeight, v["halfHeight"]!, accuracy: 1e-5)
            XCTAssertEqual(c.centerY, v["centerY"]!, accuracy: 1e-5)
            XCTAssertEqual(c.distance, v["distance"]!, accuracy: 1e-5)
        }
    }

    func testBustGeometryMatchesSpec() throws {
        let av = try loadVisualSpec()["avatar"] as! [String: Any]
        XCTAssertEqual(av["composition"] as? String, "head")
        XCTAssertEqual(BustSpec.torsoProfile, pairs(av["torsoProfile"]))
        XCTAssertEqual(BustSpec.armProfile, (av["armProfile"] as! [[Double]]).map { SIMD2<Float>(Float($0[0]), Float($0[1])) })
        let head = av["head"] as! [String: [Double]]
        XCTAssertEqual(BustSpec.headPivot, SIMD3<Float>(head["pivot"]!.map(Float.init)))
        XCTAssertEqual(BustSpec.headCenter, SIMD3<Float>(head["center"]!.map(Float.init)))
        XCTAssertEqual(BustSpec.headRadii, SIMD3<Float>(head["radii"]!.map(Float.init)))
        XCTAssertEqual(BustSpec.armPivot, SIMD3<Float>((av["armPivot"] as! [Double]).map(Float.init)))
        // Head-only: the lowest modelled point is the collar, and there are no arms.
        XCTAssertGreaterThanOrEqual(BustSpec.torsoProfile.map(\.y).min() ?? 0, 1.4)
        XCTAssertTrue(BustSpec.armProfile.isEmpty)
    }

    func testEyesAndLightingMatchSpec() throws {
        let spec = try loadVisualSpec()
        let eyes = (spec["avatar"] as! [String: Any])["eyes"] as! [String: Any]
        let travel = eyes["travel"] as! [Double]
        XCTAssertEqual(BustSpec.eyeTravel, SIMD2<Float>(Float(travel[0]), Float(travel[1])))
        let light = spec["lighting"] as! [String: Double]
        XCTAssertEqual(BustSpec.rimBase, light["rimBase"])
        XCTAssertEqual(BustSpec.rimGlow, light["rimGlow"])
        XCTAssertEqual(BustSpec.ambientRedness, light["ambientRedness"])
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

    func testWideFaceRegionShowsALargerHeadWithoutCropping() {
        let f = FramingSpec()
        for aspect in [1.6, 2.4, 3.2] {  // landscape face regions
            let c = BustFraming.camera(aspect: aspect)
            XCTAssertEqual(c.halfHeight, f.minHalfHeightWide, accuracy: 1e-9)
            XCTAssertGreaterThanOrEqual(c.centerY + c.halfHeight, f.headTop + f.topMargin - 1e-9)  // head never cut
        }
    }
}
