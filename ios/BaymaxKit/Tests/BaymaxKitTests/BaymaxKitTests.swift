import XCTest
@testable import BaymaxKit

/// Loads the shared protocol file from the repository so Swift, Python and TypeScript stay in sync.
private func loadSpec() throws -> [String: Any] {
    var url = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { url.deleteLastPathComponent() } // Tests/BaymaxKitTests/<file> -> repo root
    let data = try Data(contentsOf: url.appendingPathComponent("protocol/baymax-protocol.v1.json"))
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
        let messages = spec["messages"] as! [String: [String: [String: [String: String]]]]
        for (dir, table) in [("clientToServer", BaymaxProtocol.clientToServer), ("serverToClient", BaymaxProtocol.serverToClient)] {
            XCTAssertEqual(Set(messages[dir]!.keys), Set(table.keys), dir)
            for (name, s) in messages[dir]! {
                XCTAssertEqual(s["required"]!, table[name]!.required.mapValues(\.rawValue), name)
                XCTAssertEqual(s["optional"] ?? [:], table[name]!.optional.mapValues(\.rawValue), name)
            }
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
            (#"{"v":2,"type":"pong","id":"a","payload":{}}"#, "version_unsupported"),
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
