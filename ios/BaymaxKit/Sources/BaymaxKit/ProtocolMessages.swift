import Foundation

/// Typed, versioned Mac <-> iPhone protocol (v1). Mirrors protocol/baymax-protocol.v1.json.
public enum BaymaxProtocol {
    public static let version = 1
    public static let minVersion = 1
    public static let maxMessageBytes = 65_536
    public static let maxString = 8000

    public enum FieldType: String, Sendable { case str, int, float, bool, list, dict }

    public typealias Schema = (required: [String: FieldType], optional: [String: FieldType])

    public static let clientToServer: [String: Schema] = [
        "hello": (["protocol": .int, "minProtocol": .int, "client": .str], ["appVersion": .str, "deviceId": .str]),
        "pair.request": (["code": .str, "deviceId": .str, "deviceName": .str], [:]),
        "auth.response": (["deviceId": .str, "mac": .str], [:]),
        "user.text": (["text": .str], ["source": .str, "speakReply": .bool]),
        "voice.state": (["state": .str], ["level": .float]),
        "approval.respond": (["approvalId": .str, "approve": .bool], [:]),
        "task.cancel": (["taskId": .str], [:]),
        "estop.trigger": ([:], ["reason": .str]),
        "sleep.set": (["sleeping": .bool], [:]),
        "history.request": ([:], ["limit": .int]),
        "ping": ([:], ["t": .int]),
    ]

    public static let serverToClient: [String: Schema] = [
        "hello.ack": (["protocol": .int, "server": .str], ["serverVersion": .str]),
        "auth.challenge": (["nonce": .str], [:]),
        "auth.ok": (["deviceId": .str], [:]),
        "pair.ok": (["deviceId": .str, "deviceKey": .str], [:]),
        "error": (["code": .str, "message": .str], ["replyTo": .str, "fatal": .bool]),
        "character.state": (["state": .str], ["reason": .str, "taskId": .str]),
        "connection.status": (["status": .str], ["devices": .int, "llm": .str]),
        "voice.state": (["state": .str], ["level": .float, "source": .str]),
        "assistant.message": (["text": .str, "messageId": .str], ["speak": .bool, "taskId": .str, "role": .str]),
        "task.update": (["taskId": .str, "status": .str, "title": .str], ["step": .str, "progress": .float, "detail": .str]),
        "task.result": (["taskId": .str, "status": .str, "summary": .str], ["verified": .bool, "artifacts": .list]),
        "approval.request": (["approvalId": .str, "taskId": .str, "summary": .str, "risk": .str], ["remoteAllowed": .bool, "expiresAt": .int]),
        "estop.state": (["engaged": .bool], ["reason": .str]),
        "sleep.state": (["sleeping": .bool], [:]),
        "history": (["messages": .list], [:]),
        "pong": ([:], ["t": .int]),
    ]

    public static let voiceStates: Set<String> = ["idle", "listening", "transcribing", "speaking", "unavailable"]
}

public struct ProtocolError: Error, Equatable, Sendable {
    public let code: String
    public let message: String
    public init(_ code: String, _ message: String) { self.code = code; self.message = message }
}

/// A validated message. Payload values are JSON-compatible Foundation objects.
public struct Envelope: @unchecked Sendable {
    public let type: String
    public let id: String
    public let ts: Int
    public let payload: [String: Any]

    public init(type: String, payload: [String: Any] = [:], id: String = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(),
                ts: Int = Int(Date().timeIntervalSince1970 * 1000)) {
        self.type = type
        self.payload = payload
        self.id = id
        self.ts = ts
    }

    public func string(_ k: String) -> String? { payload[k] as? String }
    public func bool(_ k: String) -> Bool? { payload[k] as? Bool }
    public func int(_ k: String) -> Int? { (payload[k] as? NSNumber)?.intValue }
    public func double(_ k: String) -> Double? { (payload[k] as? NSNumber)?.doubleValue }

    /// Encode an outbound (client -> server) message after validating it.
    public func encoded() throws -> String {
        let clean = try MessageCodec.validate(type: type, payload: payload, table: BaymaxProtocol.clientToServer)
        let obj: [String: Any] = ["v": BaymaxProtocol.version, "type": type, "id": id, "ts": ts, "payload": clean]
        let data = try JSONSerialization.data(withJSONObject: obj)
        return String(decoding: data, as: UTF8.self)
    }
}

public enum MessageCodec {
    /// Decode and validate an inbound (server -> client) frame.
    public static func decode(_ text: String) throws -> Envelope {
        let data = Data(text.utf8)
        guard data.count <= BaymaxProtocol.maxMessageBytes else { throw ProtocolError("too_large", "message too large") }
        guard let obj = try? JSONSerialization.jsonObject(with: data), let env = obj as? [String: Any] else {
            throw ProtocolError("bad_json", "invalid JSON")
        }
        guard let v = env["v"] as? NSNumber, !isBool(v) else { throw ProtocolError("bad_envelope", "missing version") }
        guard v.intValue >= BaymaxProtocol.minVersion && v.intValue <= BaymaxProtocol.version else {
            throw ProtocolError("version_unsupported", "server protocol v\(v.intValue) is not supported")
        }
        guard let type = env["type"] as? String, let id = env["id"] as? String, id.count <= 64 else {
            throw ProtocolError("bad_envelope", "missing type or id")
        }
        guard let schema = BaymaxProtocol.serverToClient[type] else { throw ProtocolError("unknown_type", "unknown type \(type)") }
        let payload = env["payload"] as? [String: Any] ?? [:]
        let clean = try validate(type: type, payload: payload, table: [type: schema])
        let ts = (env["ts"] as? NSNumber)?.intValue ?? 0
        return Envelope(type: type, payload: clean, id: id, ts: ts)
    }

    static func isBool(_ n: NSNumber) -> Bool { CFGetTypeID(n) == CFBooleanGetTypeID() }

    static func check(_ name: String, _ value: Any, _ type: BaymaxProtocol.FieldType) throws -> Any {
        switch type {
        case .str:
            guard let s = value as? String else { throw ProtocolError("bad_payload", "\(name) must be a string") }
            guard s.count <= BaymaxProtocol.maxString else { throw ProtocolError("bad_payload", "\(name) too long") }
            return s
        case .int:
            guard let n = value as? NSNumber, !isBool(n), n.doubleValue == n.doubleValue.rounded() else { throw ProtocolError("bad_payload", "\(name) must be an integer") }
            return n.intValue
        case .float:
            guard let n = value as? NSNumber, !isBool(n), n.doubleValue.isFinite else { throw ProtocolError("bad_payload", "\(name) must be a number") }
            return n.doubleValue
        case .bool:
            guard let n = value as? NSNumber, isBool(n) else { throw ProtocolError("bad_payload", "\(name) must be a boolean") }
            return n.boolValue
        case .list:
            guard let a = value as? [Any] else { throw ProtocolError("bad_payload", "\(name) must be a list") }
            return a
        case .dict:
            guard let d = value as? [String: Any] else { throw ProtocolError("bad_payload", "\(name) must be an object") }
            return d
        }
    }

    public static func validate(type: String, payload: [String: Any], table: [String: BaymaxProtocol.Schema]) throws -> [String: Any] {
        guard let schema = table[type] else { throw ProtocolError("unknown_type", "unknown type \(type)") }
        var clean: [String: Any] = [:]
        for (name, t) in schema.required {
            guard let v = payload[name], !(v is NSNull) else { throw ProtocolError("bad_payload", "\(type): missing \(name)") }
            clean[name] = try check(name, v, t)
        }
        for (name, t) in schema.optional {
            if let v = payload[name], !(v is NSNull) { clean[name] = try check(name, v, t) }
        }
        if type == "voice.state", let s = clean["state"] as? String, !BaymaxProtocol.voiceStates.contains(s) {
            throw ProtocolError("bad_payload", "unknown voice state")
        }
        return clean
    }
}
