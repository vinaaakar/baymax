import BaymaxKit
import SwiftUI

/// The main iPhone screen — a desk companion, landscape first.
/// Landscape (default): the face is a dome rising from the bottom edge, like a desk device, with only settings and keyboard
/// icons (top-right); long-press the
/// face for conversation, sleep, settings and Emergency Stop. Portrait (top to bottom): compact status +
/// settings, the face (its own region), short status messages, and one compact row of controls. In landscape with the conversation open, the face
/// shrinks to the left and the conversation panel sits beside it. Rules live in BaymaxKit.CompanionLayout.
struct CompanionScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var showConversation = false
    @State private var showTasks = false
    @State private var showSettings = false
    @State private var typing = false
    @State private var draft = ""
    @FocusState private var focused: Bool
    @AppStorage("baymax.debugCursor") private var debugCursor = false

    var body: some View {
        GeometryReader { geo in
            let layout = CompanionLayout.compute(width: Double(geo.size.width), height: Double(geo.size.height),
                                                 conversationOpen: showConversation)
            ZStack {
                Theme.stage
                if layout.immersive {
                    immersiveFace(layout)
                } else {
                    VStack(spacing: layout.landscape ? 6 : 10) {
                        topBar
                        if debugCursor {
                            CursorDebugPanel(gaze: model.gaze, connection: model.connectionText, sleeping: model.sleeping)
                        }
                        if layout.mode == .faceBesideConversation {
                            HStack(spacing: 12) {
                                face.frame(width: geo.size.width * layout.faceWidthFraction)
                                ConversationPanel(onClose: { showConversation = false })
                                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
                            }
                        } else {
                            face.frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        statusArea(layout)
                        controlRow(layout)
                    }
                    .padding(.horizontal, layout.landscape ? 12 : 16)
                    .padding(.vertical, 4)
                }
            }
            .sheet(isPresented: Binding(get: { showConversation && layout.mode == .faceWithConversationSheet },
                                        set: { if !$0 { showConversation = false } })) {
                ConversationView().environmentObject(model)
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showTasks) { TaskSheet().environmentObject(model).presentationDetents([.medium, .large]) }
        .sheet(isPresented: $showSettings) { SettingsView().environmentObject(model) }
        .onChange(of: model.speechIn.state) { _, newValue in
            if newValue != .listening { model.micEnded() }
        }
    }

    /// Landscape face mode, like the reference desk device: the face as a dome rising from the bottom edge, and two
    /// small icons top-right (settings, keyboard).
    /// Conversation, sleep, settings/pairing and Emergency Stop are in the long-press menu on the face; alerts
    /// (E-stop, approvals, errors, connection problems) appear only while they need you.
    private func immersiveFace(_ layout: CompanionLayout) -> some View {
        ZStack(alignment: .topTrailing) {
            face.frame(maxWidth: .infinity, maxHeight: .infinity).ignoresSafeArea()
                .contextMenu { faceMenu }
            VStack(alignment: .trailing, spacing: 6) {
                HStack(spacing: 8) {
                    iconButton("gearshape", label: "Settings and pairing") { showSettings = true }
                    iconButton("keyboard", label: typing ? "Close keyboard input" : "Type a message") {
                        typing.toggle()
                        focused = typing
                    }
                }
                if typing {
                    HStack {
                        TextField("Ask BAYMAX…", text: $draft).textFieldStyle(.plain).padding(10)
                            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                            .focused($focused).submitLabel(.send)
                            .onSubmit { sendDraft() }
                        Button("Send") { sendDraft() }
                            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || !model.display.connected)
                    }
                    .frame(maxWidth: 520)
                }
                alerts
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)
            .foregroundStyle(Theme.white)
            if debugCursor {
                VStack {
                    CursorDebugPanel(gaze: model.gaze, connection: model.connectionText, sleeping: model.sleeping).frame(maxWidth: 360)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .padding(12)
                .allowsHitTesting(false)
            }
        }
    }

    /// Small, quiet icon in the top-right corner (like the reference device).
    private func iconButton(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 15)).frame(width: 40, height: 34)
                .background(.black.opacity(0.25), in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.white.opacity(0.10)))
        }
        .foregroundStyle(Theme.white.opacity(0.6))
        .accessibilityLabel(label)
    }

    private func sendDraft() {
        model.send(text: draft)
        draft = ""
        typing = false
        focused = false
    }

    @ViewBuilder
    private var faceMenu: some View {
        Button { showConversation = true } label: { Label("Conversation", systemImage: "text.bubble") }
        if model.activeTask != nil {
            Button { showTasks = true } label: { Label("Current task", systemImage: "list.bullet.rectangle") }
        }
        Button { model.setSleeping(!model.sleeping) } label: {
            Label(model.sleeping ? "Wake" : "Sleep", systemImage: model.sleeping ? "sun.max" : "moon.zzz")
        }
        Button { showSettings = true } label: { Label("Settings & pairing", systemImage: "gearshape") }
        Button(role: .destructive) { model.emergencyStop() } label: { Label("Emergency Stop", systemImage: "exclamationmark.octagon") }
    }

    /// Only what needs the user right now; nothing is shown when everything is fine.
    @ViewBuilder
    private var alerts: some View {
        if model.estopEngaged {
            Label("Emergency Stop engaged — reset it on the Mac", systemImage: "exclamationmark.octagon.fill")
                .font(.footnote.weight(.semibold)).padding(8)
                .background(Color(hex: 0x5C0C18), in: RoundedRectangle(cornerRadius: 12))
        }
        ForEach(model.approvals) { ApprovalCard(item: $0).frame(maxWidth: 420) }
        if let problem = model.connectionProblem { messageRow(problem, icon: "wifi.exclamationmark").frame(maxWidth: 420) }
        if (model.state == .error || model.state == .concerned), !model.stateReason.isEmpty {
            messageRow(model.stateReason, icon: model.state == .error ? "exclamationmark.triangle" : "hand.raised").frame(maxWidth: 420)
        }
    }

    /// The head only, in its own region. Disconnected/asleep dims it; tapping a sleeping face wakes it.
    private var face: some View {
        CharacterView(state: model.state, level: model.speechIn.level, gaze: model.gaze,
                      taskKind: model.activeTask?.kind, progress: model.activeTask?.progress)
            .opacity(model.state == .disconnected || model.state == .sleeping ? 0.75 : 1)
            .saturation(model.state == .disconnected ? 0.5 : 1)
            .contentShape(Rectangle())
            .onTapGesture { if model.sleeping { model.setSleeping(false) } }
            .accessibilityElement()
            .accessibilityLabel("BAYMAX, \(model.state.accessibilityDescription)")
            .accessibilityHint(model.sleeping ? "Double-tap to wake" : "Long-press for conversation, sleep, settings and emergency stop")
            .accessibilityAddTraits(.isImage)
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(model.display.connected ? Color.green : (model.paired == nil ? Theme.muted : Color.red)).frame(width: 8, height: 8)
                Text(model.connectionText).lineLimit(1)
            }
            .pill()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Mac connection: \(model.connectionText)")
            if model.display.connected, let ready = model.macModelReady {
                // The AI runs on the Mac (Ollama); this reports that model, not anything on the phone.
                Text(ready ? "Mac AI · \(model.macModelName.isEmpty ? "ready" : model.macModelName)" : "Mac AI unavailable")
                    .lineLimit(1)
                    .pill()
                    .foregroundStyle(ready ? Theme.white : .yellow)
                    .accessibilityLabel(ready ? "The Mac's local AI model is ready" : "The Mac's local AI model is not available")
            }
            if model.display.connected && !model.sleeping {
                CursorLiveIndicator(gaze: model.gaze)
            }
            Spacer(minLength: 4)
            Button { showSettings = true } label: { Image(systemName: "gearshape").padding(6) }
                .accessibilityLabel("Settings and pairing")
        }
        .font(.caption)
        .foregroundStyle(Theme.white)
    }

    /// Short, readable messages under the face (never on top of it).
    @ViewBuilder
    private func statusArea(_ layout: CompanionLayout) -> some View {
        VStack(spacing: 6) {
            if model.estopEngaged {
                Label("Emergency Stop engaged — reset it on the Mac", systemImage: "exclamationmark.octagon.fill")
                    .font(.footnote.weight(.semibold)).frame(maxWidth: .infinity).padding(8)
                    .background(Color(hex: 0x5C0C18), in: RoundedRectangle(cornerRadius: 12))
            }
            ForEach(model.approvals) { ApprovalCard(item: $0) }
            if (model.state == .error || model.state == .concerned), !model.stateReason.isEmpty {
                messageRow(model.stateReason, icon: model.state == .error ? "exclamationmark.triangle" : "hand.raised")
            }
            if let problem = model.connectionProblem { messageRow(problem, icon: "wifi.exclamationmark") }
            if case .unavailable(let why) = model.speechIn.state { messageRow(why, icon: "mic.slash") }
            if layout.replyLines > 0, let reply = model.lastReply, model.state != .sleeping {
                Text(reply.text).font(.callout).lineLimit(layout.replyLines).padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                    .onTapGesture { showConversation = true }
                    .accessibilityHint("Opens the conversation")
            }
            if let task = model.activeTask {
                Button { showTasks = true } label: {
                    HStack {
                        Text(task.title).lineLimit(1)
                        Spacer()
                        if let p = task.progress { ProgressView(value: p).frame(width: 70) } else { ProgressView().controlSize(.small) }
                        Button("Cancel") { model.cancelActiveTask() }.buttonStyle(.bordered).controlSize(.small)
                    }
                    .padding(8)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Current task: \(task.title). Double-tap for details.")
            }
            if typing {
                HStack {
                    TextField("Ask BAYMAX…", text: $draft).textFieldStyle(.plain).padding(8)
                        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                        .focused($focused).submitLabel(.send)
                        .onSubmit { model.send(text: draft); draft = "" }
                    Button("Send") { model.send(text: draft); draft = "" }
                        .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || !model.display.connected)
                }
            }
        }
    }

    private func messageRow(_ text: String, icon: String) -> some View {
        Label(text, systemImage: icon).font(.footnote).lineLimit(3).padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
    }

    /// One compact row: conversation, keyboard, microphone (centre), sleep/wake, emergency stop.
    private func controlRow(_ layout: CompanionLayout) -> some View {
        HStack(spacing: layout.landscape ? 22 : 14) {
            Button { showConversation.toggle() } label: {
                Image(systemName: showConversation ? "face.smiling" : "text.bubble").frame(width: 44, height: 44)
            }
            .accessibilityLabel(showConversation ? "Back to the face" : "Conversation")
            Button { typing.toggle(); focused = typing } label: { Image(systemName: "keyboard").frame(width: 44, height: 44) }
                .accessibilityLabel(typing ? "Hide keyboard input" : "Type a message")
            MicButton(diameter: layout.micDiameter)
            Button { model.setSleeping(!model.sleeping) } label: {
                Image(systemName: model.sleeping ? "sun.max" : "moon.zzz").frame(width: 44, height: 44)
            }
            .accessibilityLabel(model.sleeping ? "Wake BAYMAX" : "Put BAYMAX to sleep")
            Button(role: .destructive) { model.emergencyStop() } label: {
                Text("STOP").font(.caption.weight(.heavy)).frame(width: 52, height: 40)
                    .background(Color(hex: 0x5C0C18), in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.glow))
            }
            .accessibilityLabel("Emergency stop")
            .accessibilityHint("Cancels and blocks all controlled actions on the Mac")
        }
        .foregroundStyle(Theme.white)
        .disabled(!model.display.connected)
        .padding(.bottom, layout.landscape ? 2 : 6)
    }
}

/// Shown only while real cursor samples are arriving (same 2.5 s freshness rule the eyes use), so it never
/// claims "following" after the Mac stops sending, disconnects or sleeps.
struct CursorLiveIndicator: View {
    let gaze: GazeSource

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.5)) { _ in
            if gaze.current().live {
                Image(systemName: "cursorarrow.motionlines").pill()
                    .accessibilityLabel("Following the Mac cursor")
            }
        }
    }
}

struct MicButton: View {
    @EnvironmentObject var model: AppModel
    var diameter: Double = 64

    var body: some View {
        let listening = model.speechIn.isListening
        let d = CGFloat(diameter)
        Button { model.toggleMic() } label: {
            ZStack {
                Circle().fill(listening ? Color(hex: 0x4A0A15) : Theme.panel).frame(width: d, height: d)
                Circle().strokeBorder(listening ? Theme.glow : .white.opacity(0.2), lineWidth: 2).frame(width: d, height: d)
                if listening {
                    Circle().stroke(Theme.glow.opacity(0.3 + model.speechIn.level * 0.7), lineWidth: 2)
                        .frame(width: d + CGFloat(model.speechIn.level * 18), height: d + CGFloat(model.speechIn.level * 18))
                }
                Image(systemName: listening ? "waveform" : "mic.fill").font(.title2)
            }
        }
        .disabled(model.sleeping)
        .accessibilityLabel(listening ? "Stop listening" : "Push to talk")
        .accessibilityHint("The microphone is only on while this is active")
    }
}

struct ApprovalCard: View {
    @EnvironmentObject var model: AppModel
    let item: ApprovalItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Approval needed").font(.subheadline.weight(.semibold))
                Text("\(item.risk) risk").font(.caption).padding(.horizontal, 8).padding(.vertical, 2)
                    .overlay(Capsule().strokeBorder(Theme.glow))
            }
            Text(item.summary).font(.caption.monospaced()).lineLimit(4)
            HStack {
                if item.remoteAllowed {
                    Button("Approve") { model.respond(item, approve: true) }.buttonStyle(.borderedProminent).tint(Theme.crimson)
                } else {
                    Text("Approve this on the Mac").font(.caption).foregroundStyle(Theme.muted)
                }
                Button("Deny") { model.respond(item, approve: false) }.buttonStyle(.bordered)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: 0x3A0A12), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.glow))
        .accessibilityElement(children: .contain)
    }
}
