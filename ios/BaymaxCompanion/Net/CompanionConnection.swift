import BaymaxKit
import CryptoKit
import Foundation
import Network

/// Authenticated WebSocket connection to the Mac over the local network.
///
/// * TLS with certificate pinning: the server certificate's SHA-256 must equal the fingerprint
///   from the pairing QR code; anything else is rejected (no system trust fallback).
/// * Challenge-response auth with the device key (HMAC-SHA256), sent after `hello`.
/// * Reconnects with exponential backoff, immediately when the network path changes,
///   and when a heartbeat ping goes unanswered.
@MainActor
final class CompanionConnection: NSObject {
    enum Status: Equatable { case idle, connecting, authenticating, connected, failed(String) }

    var onStatus: ((Status) -> Void)?
    var onMessage: ((Envelope) -> Void)?
    var onPaired: ((PairedMac) -> Void)?

    private(set) var status: Status = .idle { didSet { onStatus?(status) } }
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var target: PairedMac?
    private var pendingPairCode: String?
    private var backoff = Backoff()
    private var reconnectWork: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var lastPong = Date()
    private let monitor = NWPathMonitor()
    private var wantConnected = false
    private var pinnedFingerprint = ""

    override init() {
        super.init()
        session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: .main)
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                guard let self, self.wantConnected, path.status == .satisfied, self.status != .connected else { return }
                self.reconnectNow()
            }
        }
        monitor.start(queue: .global(qos: .utility))
    }

    // MARK: - Public API

    func connect(to mac: PairedMac) {
        target = mac
        pendingPairCode = nil
        wantConnected = true
        open()
    }

    /// First-time pairing with a scanned QR payload.
    func pair(with payload: PairingPayload, deviceName: String) {
        let deviceId = KeychainStore.get("deviceId") ?? {
            let id = "iphone-" + UUID().uuidString.lowercased()
            KeychainStore.set(id, for: "deviceId")
            return id
        }()
        target = PairedMac(host: payload.host, port: payload.port, fingerprint: payload.fp, deviceId: deviceId)
        pendingPairCode = payload.code
        self.deviceName = deviceName
        wantConnected = true
        open()
    }

    func disconnect() {
        wantConnected = false
        reconnectWork?.cancel()
        heartbeat?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        status = .idle
    }

    func send(_ type: String, _ payload: [String: Any] = [:]) {
        guard let task, status == .connected || ["hello", "pair.request", "auth.response"].contains(type) else { return }
        do {
            let text = try Envelope(type: type, payload: payload).encoded()
            task.send(.string(text)) { _ in }
        } catch {
            // Outbound validation failure is a programming error; never send malformed frames.
            assertionFailure("invalid outbound \(type): \(error)")
        }
    }

    // MARK: - Internals

    private var deviceName = "iPhone"

    private func open() {
        guard let target, let url = target.url else { status = .failed("No paired Mac"); return }
        reconnectWork?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        pinnedFingerprint = target.fingerprint
        status = .connecting
        var request = URLRequest(url: url)
        request.timeoutInterval = 10
        let t = session.webSocketTask(with: request)
        t.maximumMessageSize = BaymaxProtocol.maxMessageBytes
        task = t
        t.resume()
        status = .authenticating
        receive(on: t)
        var hello: [String: Any] = ["protocol": BaymaxProtocol.version, "minProtocol": BaymaxProtocol.minVersion, "client": "baymax-ios",
                                    "appVersion": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"]
        if pendingPairCode == nil { hello["deviceId"] = target.deviceId }
        send("hello", hello)
    }

    private func receive(on t: URLSessionWebSocketTask) {
        t.receive { [weak self] result in
            Task { @MainActor in
                guard let self, t === self.task else { return }
                switch result {
                case .success(.string(let text)):
                    self.handle(text)
                    self.receive(on: t)
                case .success:
                    self.receive(on: t) // binary frames are not part of the protocol; ignore
                case .failure(let error):
                    self.connectionLost(error.localizedDescription)
                }
            }
        }
    }

    private func handle(_ text: String) {
        let env: Envelope
        do {
            env = try MessageCodec.decode(text)
        } catch let e as ProtocolError {
            if e.code == "version_unsupported" { fail("The Mac uses a newer protocol. Update the BAYMAX app.") }
            return // malformed frames are dropped
        } catch { return }
        switch env.type {
        case "hello.ack":
            if let code = pendingPairCode, let target {
                send("pair.request", ["code": code, "deviceId": target.deviceId, "deviceName": deviceName])
            }
        case "pair.ok":
            guard var t = target, let key = env.string("deviceKey") else { return }
            KeychainStore.set(key, for: "key-\(t.deviceId)")
            t.deviceId = env.string("deviceId") ?? t.deviceId
            target = t
            pendingPairCode = nil
            t.save()
            onPaired?(t)
        case "auth.challenge":
            guard let target, let nonce = env.string("nonce"), let key = target.deviceKey else {
                fail("This iPhone is not paired with the Mac. Pair again.")
                return
            }
            let mac = HMAC<SHA256>.authenticationCode(for: AuthMessage.input(nonce: nonce, deviceId: target.deviceId), using: SymmetricKey(data: Data(key.utf8)))
            send("auth.response", ["deviceId": target.deviceId, "mac": AuthMessage.hex(mac)])
        case "auth.ok":
            backoff.reset()
            status = .connected
            startHeartbeat()
            send("history.request", ["limit": 50])
        case "pong":
            lastPong = Date()
        case "error":
            let code = env.string("code") ?? "error"
            switch code {
            case "pair_failed", "pair_locked":
                fail("Pairing failed: \(env.string("message") ?? code)")
            case "auth_failed":
                // A wrong key will not fix itself by retrying; the user has to pair again.
                fail("The Mac did not accept this iPhone's credentials. Pair again from the Mac.")
            case "version_unsupported":
                fail("The Mac and this app speak different protocol versions. Update BAYMAX on both.")
            default:
                onMessage?(env)
            }
        default:
            onMessage?(env)
        }
    }

    private func startHeartbeat() {
        heartbeat?.cancel()
        lastPong = Date()
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                guard let self else { return }
                if Date().timeIntervalSince(self.lastPong) > 40 {
                    self.connectionLost("heartbeat timeout")
                    return
                }
                self.send("ping", ["t": Int(Date().timeIntervalSince1970)])
            }
        }
    }

    private func fail(_ message: String) {
        wantConnected = false
        heartbeat?.cancel()
        task?.cancel(with: .policyViolation, reason: nil)
        task = nil
        status = .failed(message)
    }

    private func connectionLost(_ reason: String) {
        heartbeat?.cancel()
        task = nil
        guard wantConnected else { status = .idle; return }
        if case .failed = status { return }
        status = .connecting
        let delay = backoff.nextDelay()
        reconnectWork = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.open()
        }
    }

    private func reconnectNow() {
        backoff.reset()
        open()
    }
}

extension CompanionConnection: URLSessionDelegate {
    /// Certificate pinning: accept only the exact certificate whose SHA-256 was in the QR code.
    nonisolated func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                                completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(leaf) as Data
        let fp = AuthMessage.hex(SHA256.hash(data: der))
        let expected = MainActor.assumeIsolated { self.pinnedFingerprint }
        if !expected.isEmpty && fp == expected {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
