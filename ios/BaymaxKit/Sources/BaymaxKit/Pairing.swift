import Foundation

/// Contents of the pairing QR code shown by the Mac:
/// {"v":1,"host":"192.168.1.20","hosts":[...],"port":8766,"fp":"<sha256 hex of TLS cert>","code":"123456"}
public struct PairingPayload: Codable, Equatable, Sendable {
    public var v: Int
    public var host: String
    public var hosts: [String]?
    public var port: Int
    public var fp: String
    public var code: String

    public init(v: Int = 1, host: String, hosts: [String]? = nil, port: Int, fp: String, code: String) {
        self.v = v; self.host = host; self.hosts = hosts; self.port = port; self.fp = fp; self.code = code
    }

    public enum Invalid: Error, Equatable { case notJSON, version, host, port, fingerprint, code }

    /// Parse and strictly validate a scanned QR string.
    public static func parse(_ text: String) throws -> PairingPayload {
        guard let data = text.data(using: .utf8), let p = try? JSONDecoder().decode(PairingPayload.self, from: data) else { throw Invalid.notJSON }
        guard p.v == 1 else { throw Invalid.version }
        guard isPrivateLANHost(p.host) else { throw Invalid.host }
        guard (1...65535).contains(p.port) else { throw Invalid.port }
        guard p.fp.count == 64, p.fp.allSatisfy({ $0.isHexDigit }) else { throw Invalid.fingerprint }
        guard p.code.count == 6, p.code.allSatisfy({ $0.isNumber }) else { throw Invalid.code }
        return PairingPayload(v: 1, host: p.host, hosts: p.hosts, port: p.port, fp: p.fp.lowercased(), code: p.code)
    }

    public var url: URL? { URL(string: "wss://\(host):\(port)/v1/ws") }
}

/// Only private-network IPv4 addresses or .local hostnames: the companion never connects across the internet.
public func isPrivateLANHost(_ host: String) -> Bool {
    if host.hasSuffix(".local") && host.count < 254 { return true }
    let parts = host.split(separator: ".").compactMap { Int($0) }
    guard parts.count == 4, parts.allSatisfy({ (0...255).contains($0) }) else { return false }
    switch (parts[0], parts[1]) {
    case (10, _): return true
    case (172, 16...31): return true
    case (192, 168): return true
    case (169, 254): return true
    default: return false
    }
}

/// Exponential backoff with full jitter for reconnects (0.5 s doubling to 16 s).
public struct Backoff: Sendable {
    public private(set) var attempt = 0
    public let base: Double
    public let cap: Double

    public init(base: Double = 0.5, cap: Double = 16) { self.base = base; self.cap = cap }

    public mutating func nextDelay(random: Double = Double.random(in: 0...1)) -> Double {
        let ceiling = min(cap, base * pow(2, Double(attempt)))
        attempt += 1
        return max(0.25, ceiling * (0.5 + 0.5 * random))
    }

    public mutating func reset() { attempt = 0 }
}
