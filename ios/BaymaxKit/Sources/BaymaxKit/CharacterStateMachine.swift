import Foundation

/// Character states — mirrors `characterStates` in protocol/baymax-protocol.v1.json.
public enum CharacterState: String, CaseIterable, Codable, Sendable {
    case idle = "IDLE", listening = "LISTENING", thinking = "THINKING", speaking = "SPEAKING"
    case executing = "EXECUTING", success = "SUCCESS", confused = "CONFUSED", concerned = "CONCERNED"
    case sleeping = "SLEEPING", error = "ERROR", disconnected = "DISCONNECTED"

    public var accessibilityDescription: String {
        switch self {
        case .idle: return "ready"
        case .listening: return "listening"
        case .thinking: return "thinking"
        case .speaking: return "speaking"
        case .executing: return "working on a task"
        case .success: return "task verified"
        case .confused: return "needs clarification"
        case .concerned: return "needs your attention"
        case .sleeping: return "sleeping"
        case .error: return "something failed"
        case .disconnected: return "disconnected from the Mac"
        }
    }
}

public enum CharacterEvent: String, CaseIterable, Codable, Sendable {
    case micStarted = "MIC_STARTED", micStopped = "MIC_STOPPED", requestReceived = "REQUEST_RECEIVED"
    case speechStarted = "SPEECH_STARTED", speechEnded = "SPEECH_ENDED", taskStarted = "TASK_STARTED"
    case taskVerified = "TASK_VERIFIED", taskFailed = "TASK_FAILED", taskCancelled = "TASK_CANCELLED"
    case clarificationNeeded = "CLARIFICATION_NEEDED", warningRaised = "WARNING_RAISED", approvalRequired = "APPROVAL_REQUIRED"
    case sleep = "SLEEP", wake = "WAKE", connectionLost = "CONNECTION_LOST", connectionRestored = "CONNECTION_RESTORED", reset = "RESET"
}

public struct TransitionResult: Equatable, Sendable {
    public let accepted: Bool
    public let previous: CharacterState
    public let state: CharacterState
    public let reason: String
}

public enum CharacterRules {
    // Tables mirror the shared protocol file; BaymaxKitTests verifies they are identical.
    public static let transitions: [CharacterState: Set<CharacterState>] = [
        .idle: [.listening, .thinking, .speaking, .executing, .confused, .concerned, .sleeping, .error, .disconnected],
        .listening: [.idle, .thinking, .sleeping, .error, .disconnected],
        .thinking: [.idle, .listening, .speaking, .executing, .confused, .concerned, .sleeping, .error, .disconnected],
        .speaking: [.idle, .listening, .thinking, .executing, .confused, .concerned, .sleeping, .error, .disconnected],
        .executing: [.idle, .thinking, .speaking, .success, .confused, .concerned, .sleeping, .error, .disconnected],
        .success: [.idle, .listening, .thinking, .speaking, .executing, .confused, .concerned, .sleeping, .error, .disconnected],
        .confused: [.idle, .listening, .thinking, .speaking, .executing, .concerned, .sleeping, .error, .disconnected],
        .concerned: [.idle, .listening, .thinking, .speaking, .executing, .confused, .sleeping, .error, .disconnected],
        .sleeping: [.idle, .disconnected],
        .error: [.idle, .listening, .thinking, .speaking, .executing, .confused, .concerned, .sleeping, .disconnected],
        .disconnected: [.idle, .sleeping],
    ]

    public static let eventTargets: [CharacterEvent: CharacterState] = [
        .micStarted: .listening, .micStopped: .idle, .requestReceived: .thinking, .speechStarted: .speaking,
        .speechEnded: .idle, .taskStarted: .executing, .taskVerified: .success, .taskFailed: .error, .taskCancelled: .idle,
        .clarificationNeeded: .confused, .warningRaised: .concerned, .approvalRequired: .concerned, .sleep: .sleeping,
        .wake: .idle, .connectionLost: .disconnected, .connectionRestored: .idle, .reset: .idle,
    ]

    public static let sleepExitEvents: Set<CharacterEvent> = [.wake, .connectionLost]
    public static let disconnectedExitEvents: Set<CharacterEvent> = [.connectionRestored, .sleep]

    public static func next(from: CharacterState, event: CharacterEvent, verified: Bool = false) -> TransitionResult {
        let to = eventTargets[event]!
        func reject(_ r: String) -> TransitionResult { TransitionResult(accepted: false, previous: from, state: from, reason: r) }
        if from == .sleeping && !sleepExitEvents.contains(event) { return reject("sleeping") }
        if from == .disconnected && !disconnectedExitEvents.contains(event) { return reject("disconnected") }
        if event == .taskVerified && !verified { return reject("success requires verified outcome") }
        if to == .success && from != .executing { return reject("success only follows an executing task") }
        if from != to && !(transitions[from]?.contains(to) ?? false) { return reject("invalid transition \(from.rawValue) -> \(to.rawValue)") }
        return TransitionResult(accepted: true, previous: from, state: to, reason: "")
    }
}

/// What the phone shows: the Mac's authoritative state, overlaid with what only the phone knows
/// (its connection, its own microphone, its own speech playback).
public struct DisplayedCharacter: Equatable, Sendable {
    public var serverState: CharacterState = .idle
    public var connected = false
    public var localListening = false
    public var localSpeaking = false

    public init() {}

    public var state: CharacterState {
        if !connected { return .disconnected }
        if serverState == .sleeping { return .sleeping }
        if localListening { return .listening }
        if localSpeaking { return .speaking }
        return serverState
    }

    /// Accepts only known states from the server; anything else is ignored.
    @discardableResult
    public mutating func applyServer(_ raw: String) -> Bool {
        guard let s = CharacterState(rawValue: raw) else { return false }
        serverState = s
        return true
    }
}
