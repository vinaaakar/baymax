import AVFoundation
import Speech

/// Push-to-talk speech recognition that runs **on the device only**.
/// If on-device recognition is unavailable for the locale, the app says so and offers text input;
/// audio is never sent to a server-based recognizer.
@MainActor
final class SpeechInput: ObservableObject {
    enum State: Equatable { case idle, listening, unavailable(String) }

    @Published private(set) var state: State = .idle
    @Published private(set) var level: Double = 0
    @Published private(set) var partial = ""

    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?
    private let recognizer = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var finish: ((String) -> Void)?
    private var silenceTimer: Timer?
    private var lastVoice = Date()
    private var heardVoice = false
    /// Deactivating the audio session can block for a moment, so it runs off the main thread (Xcode's "hang risk").
    nonisolated private static let sessionQueue = DispatchQueue(label: "baymax.audio-session")

    var isListening: Bool { state == .listening }

    func requestPermissions() async -> String? {
        let speech = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
        guard speech == .authorized else { return "Speech recognition permission is off. Enable it in Settings → BAYMAX." }
        let mic = await AVAudioApplication.requestRecordPermission()
        guard mic else { return "Microphone permission is off. Enable it in Settings → BAYMAX." }
        guard let recognizer, recognizer.supportsOnDeviceRecognition else {
            return "On-device speech recognition isn't available for this language on this iPhone, so voice input is off (BAYMAX won't send your audio to a server). Use text input instead."
        }
        return nil
    }

    func start(onFinal: @escaping (String) -> Void) async {
        if let problem = await requestPermissions() {
            state = .unavailable(problem)
            return
        }
        guard let recognizer, recognizer.isAvailable else {
            state = .unavailable("Speech recognition is temporarily unavailable.")
            return
        }
        finish = onFinal
        partial = ""
        heardVoice = false
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.duckOthers, .defaultToSpeaker])
            Self.sessionQueue.sync {}  // let a pending deactivation finish first, so it can't cut off this recording
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let req = SFSpeechAudioBufferRecognitionRequest()
            req.requiresOnDeviceRecognition = true // never server-side
            req.shouldReportPartialResults = true
            request = req
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                req.append(buffer)
                let lvl = Self.rms(buffer)
                Task { @MainActor in self?.updateLevel(lvl) }
            }
            engine.prepare()
            try engine.start()
            recognition = recognizer.recognitionTask(with: req) { [weak self] result, error in
                Task { @MainActor in
                    guard let self else { return }
                    if let result {
                        self.partial = result.bestTranscription.formattedString
                        if result.isFinal { self.complete() }
                    } else if error != nil {
                        self.complete()
                    }
                }
            }
            state = .listening
            lastVoice = Date()
            silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.checkSilence() }
            }
        } catch {
            stopAudio()
            state = .unavailable("Couldn't start the microphone: \(error.localizedDescription)")
        }
    }

    /// Stop listening (user released / tapped again). The transcript so far is delivered.
    func stop() {
        guard state == .listening else { return }
        request?.endAudio()
        complete()
    }

    func cancel() {
        finish = nil
        recognition?.cancel()
        stopAudio()
        state = .idle
    }

    private func updateLevel(_ lvl: Double) {
        level = min(1, lvl * 6)
        if lvl > 0.02 {
            lastVoice = Date()
            heardVoice = true
        }
    }

    private func checkSilence() {
        guard state == .listening else { return }
        let quiet = Date().timeIntervalSince(lastVoice)
        if (heardVoice && quiet > 1.3) || (!heardVoice && quiet > 7) { stop() }
    }

    private func complete() {
        let text = partial.trimmingCharacters(in: .whitespacesAndNewlines)
        let cb = finish
        finish = nil
        recognition?.finish()
        stopAudio()
        state = .idle
        if !text.isEmpty { cb?(text) }
    }

    private func stopAudio() {
        silenceTimer?.invalidate()
        silenceTimer = nil
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
        request = nil
        recognition = nil
        level = 0
        Self.sessionQueue.async { // release the mic without blocking the UI
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    nonisolated static func rms(_ buffer: AVAudioPCMBuffer) -> Double {
        guard let data = buffer.floatChannelData?[0] else { return 0 }
        let n = Int(buffer.frameLength)
        guard n > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<n { sum += data[i] * data[i] }
        return Double(sqrt(sum / Float(n)))
    }
}
