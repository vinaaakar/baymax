import Foundation

/// Message authenticated by the device key: HMAC-SHA256(key, "baymax-auth-v1|<nonce>|<deviceId>") as lowercase hex.
/// The HMAC primitive is injected so this package stays Foundation-only; the app passes CryptoKit's implementation.
public enum AuthMessage {
    public static func input(nonce: String, deviceId: String) -> Data {
        Data("baymax-auth-v1|\(nonce)|\(deviceId)".utf8)
    }

    public static func hex(_ bytes: some Sequence<UInt8>) -> String {
        bytes.map { String(format: "%02x", $0) }.joined()
    }
}

/// Recent task view kept by the phone (bounded).
public struct TaskSummary: Identifiable, Equatable, Sendable {
    public let id: String
    public var title: String
    public var status: String
    public var step: String?
    public var progress: Double?
    public var detail: String?
    public var verified: Bool?
    public var summary: String?

    public init(id: String, title: String, status: String) {
        self.id = id; self.title = title; self.status = status
    }

    public var isActive: Bool { !["COMPLETED", "FAILED", "CANCELLED"].contains(status) }
}
