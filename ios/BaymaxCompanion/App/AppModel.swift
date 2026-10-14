import BaymaxKit
import Combine
import Foundation
import SwiftUI
import UIKit

struct ChatLine: Identifiable, Equatable {
    let id: String
    let role: String
    let text: String
}

struct ApprovalItem: Identifiable, Equatable {
    let id: String
    let taskId: String
    let summary: String
    let risk: String
    let remoteAllowed: Bool
}

@MainActor
final class AppModel: ObservableObject {
    @Published var display = DisplayedCharacter()
    @Published var connectionText = "Not paired"
    @Published var connectionProblem: String?
    @Published var paired: PairedMac? = PairedMac.load()
    @Published var messages: [ChatLine] = []
    @Published var tasks: [String: TaskSummary] = [:]
    @Published var approvals: [ApprovalItem] = []
    @Published var sleeping = false
    @Published var estopEngaged = false
    @Published var llmReady = false
    @Published var lastReply: ChatLine?
    @Published var speakReplies = true

    let gaze = GazeSource()
    @Published var placement: Placement = Placement(rawValue: UserDefaults.standard.string(forKey: "baymax.placement") ?? "") ?? .below {
        didSet {
            UserDefaults.standard.set(placement.rawValue, forKey: "baymax.placement")
            gaze.setPlacement(placement)
        }
    }
    @Published var cursorLive = false
    @Published var lastResearch: (query: String, sources: [(title: String, url: String)])?
    let speechIn = SpeechInput()
    let speechOut = SpeechOutput()
    private let connection = CompanionConnection()
    private var announcedState: CharacterState?
    private var forwarders: Set<AnyCancellable> = []

    var state: CharacterState { display.state }
    var activeTask: TaskSummary? { tasks.values.filter(\.isActive).sorted { $0.title < $1.title }.first }

    init() {
        gaze.setPlacement(placement)
        // Nested ObservableObjects don't propagate changes on their own; forward them so views update.
        speechIn.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &forwarders)
        speechOut.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &forwarders)
        connection.onStatus = { [weak self] in self?.handleStatus($0) }
        connection.onMessage = { [weak self] in self?.handle($0) }
        connection.onPaired = { [weak self] mac in self?.paired = mac }
        speechOut.onStateChange = { [weak self] speaking in
            guard let self else { return }
            self.display.localSpeaking = speaking
            self.connection.send("voice.state", ["state": speaking ? "speaking" : "idle"])
            self.announce()
        }
        if let mac = paired { connection.connect(to: mac) }
    }

    // MARK: - Lifecycle

    func appBecameActive() {
        if let mac = paired, case .idle = connection.status { connection.connect(to: mac) }
    }

    func appWentToBackground() {
        // iOS does not allow open-ended background microphone use; stop capture and speech explicitly.
        speechIn.cancel()
        speechOut.stop()
        display.localListening = false
        connection.disconnect()
        display.connected = false
    }

    // MARK: - Pairing

    func pair(scanned: String) -> String? {
        do {
            let payload = try PairingPayload.parse(scanned)
            connection.pair(with: payload, deviceName: UIDevice.current.name)
            return nil
        } catch {
            return "That QR code isn't a valid BAYMAX pairing code for a Mac on your local network."
        }
    }

    func pairManually(host: String, port: Int, fingerprint: String, code: String) -> String? {
        let json = #"{"v":1,"host":"\#(host)","port":\#(port),"fp":"\#(fingerprint.lowercased())","code":"\#(code)"}"#
        return pair(scanned: json)
    }

    func unpair() {
        connection.disconnect()
        PairedMac.forget()
        paired = nil
        display.connected = false
        connectionText = "Not paired"
    }

    // MARK: - Actions

    func send(text: String, fromVoice: Bool = false) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        speechOut.stop()
        messages.append(ChatLine(id: UUID().uuidString, role: "user", text: t))
        connection.send("user.text", ["text": t, "source": fromVoice ? "voice" : "text", "speakReply": fromVoice && speakReplies])
    }

    func toggleMic() {
        if speechIn.isListening {
            speechIn.stop()
            return
        }
        speechOut.stop()
        Task { [weak self] in
            guard let self else { return }
            self.display.localListening = true
            self.connection.send("voice.state", ["state": "listening"])
            self.announce()
            await self.speechIn.start { [weak self] text in
                self?.send(text: text, fromVoice: true)
            }
            if !self.speechIn.isListening { self.micEnded() }
        }
    }

    /// Called when listening ends (silence, stop, or failure).
    func micEnded() {
        display.localListening = false
        connection.send("voice.state", ["state": "idle"])
        announce()
    }

    func cancelActiveTask() {
        speechOut.stop()
        if let t = activeTask { connection.send("task.cancel", ["taskId": t.id]) }
    }

    func emergencyStop() {
        speechOut.stop()
        speechIn.cancel()
        connection.send("estop.trigger", ["reason": "iPhone"])
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
    }

    func setSleeping(_ sleeping: Bool) {
        if sleeping { speechOut.stop(); speechIn.cancel() }
        connection.send("sleep.set", ["sleeping": sleeping])
    }

    func respond(_ approval: ApprovalItem, approve: Bool) {
        connection.send("approval.respond", ["approvalId": approval.id, "approve": approve])
        if !approve || approval.remoteAllowed { approvals.removeAll { $0.id == approval.id } }
    }

    // MARK: - Incoming

    private func handleStatus(_ status: CompanionConnection.Status) {
        switch status {
        case .connected:
            display.connected = true
            gaze.reset()
            connectionText = "Connected to Mac"
            connectionProblem = nil
        case .connecting, .authenticating:
            display.connected = false
            cursorLive = false
            connectionText = "Connecting…"
        case .idle:
            display.connected = false
            connectionText = paired == nil ? "Not paired" : "Disconnected"
        case .failed(let msg):
            display.connected = false
            connectionText = "Not connected"
            connectionProblem = msg
        }
        announce()
    }

    private func handle(_ env: Envelope) {
        switch env.type {
        case "character.state":
            if let s = env.string("state") { display.applyServer(s) }
        case "sleep.state":
            sleeping = env.bool("sleeping") ?? false
            gaze.reset() // asleep: no gaze following; on wake start fresh
            if sleeping {
                // Sleep may come from the Mac: stop listening and speaking here too. Wake does not restart the mic;
                // listening only ever starts from an explicit push-to-talk.
                speechIn.cancel()
                speechOut.stop()
            }
        case "cursor.update":
            // Real Mac cursor (normalised to the primary display) -> gaze direction.
            guard !sleeping else { return }
            gaze.cursor(env)
            if !cursorLive { cursorLive = true }
            return // high-rate: skip the VoiceOver announcement path below
        case "gaze.target":
            guard !sleeping else { return }
            if env.string("kind") == "clear" { gaze.clearPoint(); return }
            if let vx = env.double("vx") ?? env.double("x"), let vy = env.double("vy") ?? env.double("y") {
                gaze.point(vx: vx, vy: vy, label: env.string("label") ?? "", durationMs: Double(env.int("durationMs") ?? 4000))
            }
        case "research.result":
            let sources = (env.payload["sources"] as? [[String: Any]] ?? []).compactMap { s -> (String, String)? in
                guard let url = s["url"] as? String else { return nil }
                return (s["title"] as? String ?? url, url)
            }
            lastResearch = (env.string("query") ?? "", sources.map { (title: $0.0, url: $0.1) })
        case "estop.state":
            estopEngaged = env.bool("engaged") ?? false
            if estopEngaged { speechIn.cancel(); speechOut.stop() }
        case "connection.status":
            llmReady = env.string("llm") == "ready"
        case "assistant.message":
            let line = ChatLine(id: env.string("messageId") ?? UUID().uuidString, role: env.string("role") ?? "assistant", text: env.string("text") ?? "")
            if !messages.contains(where: { $0.id == line.id }) { messages.append(line) }
            if messages.count > 200 { messages.removeFirst(messages.count - 200) }
            lastReply = line
            if env.bool("speak") == true && !sleeping { speechOut.speak(line.text) }
        case "history":
            let items = (env.payload["messages"] as? [[String: Any]]) ?? []
            messages = items.compactMap { m in
                guard let id = m["id"] as? String, let text = m["text"] as? String else { return nil }
                return ChatLine(id: id, role: m["role"] as? String ?? "assistant", text: text)
            }
        case "task.update":
            guard let id = env.string("taskId") else { return }
            var t = tasks[id] ?? TaskSummary(id: id, title: env.string("title") ?? "", status: "")
            t.title = env.string("title") ?? t.title
            t.status = env.string("status") ?? t.status
            t.step = env.string("step") ?? t.step
            t.progress = env.double("progress") ?? t.progress
            t.detail = env.string("detail") ?? t.detail
            tasks[id] = t
        case "task.result":
            guard let id = env.string("taskId") else { return }
            var t = tasks[id] ?? TaskSummary(id: id, title: "Task", status: "")
            t.status = env.string("status") ?? t.status
            t.verified = env.bool("verified")
            t.summary = env.string("summary")
            tasks[id] = t
            approvals.removeAll { $0.taskId == id }
            if tasks.count > 50, let oldest = tasks.values.filter({ !$0.isActive }).first { tasks.removeValue(forKey: oldest.id) }
        case "approval.request":
            guard let id = env.string("approvalId"), let task = env.string("taskId") else { return }
            if !approvals.contains(where: { $0.id == id }) {
                approvals.append(ApprovalItem(id: id, taskId: task, summary: env.string("summary") ?? "", risk: env.string("risk") ?? "high",
                                              remoteAllowed: env.bool("remoteAllowed") ?? false))
            }
        case "error":
            connectionProblem = env.string("message")
        default:
            break
        }
        announce()
    }

    /// VoiceOver announcement when the visible state changes.
    private func announce() {
        let s = display.state
        guard s != announcedState else { return }
        announcedState = s
        if UIAccessibility.isVoiceOverRunning {
            UIAccessibility.post(notification: .announcement, argument: "BAYMAX is \(s.accessibilityDescription)")
        }
    }
}
