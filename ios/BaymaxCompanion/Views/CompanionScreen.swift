import BaymaxKit
import SwiftUI

/// The main iPhone screen: deep red stage, large centred character, compact controls below it.
struct CompanionScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var showConversation = false
    @State private var showTasks = false
    @State private var showSettings = false
    @State private var typing = false
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            Theme.stage
            GeometryReader { geo in
                CharacterView(state: model.state, level: model.speechIn.level, gaze: model.gaze)
                    .frame(width: geo.size.width, height: geo.size.height * 0.78)
                    .opacity(model.state == .disconnected || model.state == .sleeping ? 0.75 : 1)
                    .saturation(model.state == .disconnected ? 0.5 : 1)
                    .contentShape(Rectangle())
                    .onTapGesture { if model.sleeping { model.setSleeping(false) } }
                    .accessibilityElement()
                    .accessibilityLabel("BAYMAX, \(model.state.accessibilityDescription)")
                    .accessibilityHint(model.sleeping ? "Double-tap to wake" : "")
                    .accessibilityAddTraits(.isImage)
            }
            VStack(spacing: 10) {
                topBar
                Spacer()
                bottomControls
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showConversation) { ConversationView().environmentObject(model) }
        .sheet(isPresented: $showTasks) { TaskSheet().environmentObject(model).presentationDetents([.medium, .large]) }
        .sheet(isPresented: $showSettings) { SettingsView().environmentObject(model) }
        .onChange(of: model.speechIn.state) { _, newValue in
            if newValue != .listening { model.micEnded() }
        }
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(model.display.connected ? Color.green : (model.paired == nil ? Theme.muted : Color.red)).frame(width: 8, height: 8)
                Text(model.connectionText)
            }
            .pill()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Mac connection: \(model.connectionText)")
            if model.display.connected && model.cursorLive && !model.sleeping {
                Label("Following cursor", systemImage: "cursorarrow.motionlines").labelStyle(.titleAndIcon).pill()
                    .accessibilityLabel("Following the Mac cursor")
            }
            if model.display.connected && !model.llmReady {
                Text("No local model").pill().foregroundStyle(.yellow)
            }
            Spacer()
            Button { showSettings = true } label: { Image(systemName: "gearshape").padding(8) }
                .accessibilityLabel("Settings and pairing")
        }
        .foregroundStyle(Theme.white)
        .padding(.top, 4)
    }

    private var bottomControls: some View {
        VStack(spacing: 8) {
            if model.estopEngaged {
                Label("Emergency Stop engaged — reset it on the Mac", systemImage: "exclamationmark.octagon.fill")
                    .font(.footnote.weight(.semibold)).frame(maxWidth: .infinity).padding(10)
                    .background(Color(hex: 0x5C0C18), in: RoundedRectangle(cornerRadius: 12))
            }
            ForEach(model.approvals) { ApprovalCard(item: $0) }
            if let problem = model.connectionProblem {
                Text(problem).font(.footnote).padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            }
            if case .unavailable(let why) = model.speechIn.state {
                Text(why).font(.footnote).padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            }
            if let reply = model.lastReply, model.state != .sleeping {
                Text(reply.text).font(.callout).lineLimit(3).padding(10).frame(maxWidth: .infinity, alignment: .leading)
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
                    .padding(10)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Current task: \(task.title). Double-tap for details.")
            }
            if typing {
                HStack {
                    TextField("Ask BAYMAX…", text: $draft).textFieldStyle(.plain).padding(10)
                        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                        .focused($focused).submitLabel(.send)
                        .onSubmit { model.send(text: draft); draft = "" }
                    Button("Send") { model.send(text: draft); draft = "" }.disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            HStack(spacing: 14) {
                Button { showConversation = true } label: { Image(systemName: "text.bubble").frame(width: 44, height: 44) }
                    .accessibilityLabel("Conversation history")
                Button { typing.toggle(); focused = typing } label: { Image(systemName: "keyboard").frame(width: 44, height: 44) }
                    .accessibilityLabel(typing ? "Hide keyboard input" : "Type a message")
                MicButton()
                Button { model.setSleeping(!model.sleeping) } label: {
                    Image(systemName: model.sleeping ? "sun.max" : "moon.zzz").frame(width: 44, height: 44)
                }
                .accessibilityLabel(model.sleeping ? "Wake BAYMAX" : "Put BAYMAX to sleep")
                Button(role: .destructive) { model.emergencyStop() } label: {
                    Text("STOP").font(.caption.weight(.heavy)).frame(width: 52, height: 44)
                        .background(Color(hex: 0x5C0C18), in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.glow))
                }
                .accessibilityLabel("Emergency stop")
                .accessibilityHint("Cancels and blocks all controlled actions on the Mac")
            }
            .foregroundStyle(Theme.white)
            .disabled(!model.display.connected)
        }
    }
}

struct MicButton: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let listening = model.speechIn.isListening
        Button { model.toggleMic() } label: {
            ZStack {
                Circle().fill(listening ? Color(hex: 0x4A0A15) : Theme.panel).frame(width: 64, height: 64)
                Circle().strokeBorder(listening ? Theme.glow : .white.opacity(0.2), lineWidth: 2).frame(width: 64, height: 64)
                if listening {
                    Circle().stroke(Theme.glow.opacity(0.3 + model.speechIn.level * 0.7), lineWidth: 2)
                        .frame(width: 64 + model.speechIn.level * 18, height: 64 + model.speechIn.level * 18)
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
