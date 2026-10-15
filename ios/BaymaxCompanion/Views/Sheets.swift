import AVFoundation
import BaymaxKit
import SwiftUI

/// Conversation history + text input. Used inline beside the face (landscape) and inside ConversationView (sheet).
/// Shows only real messages from the Mac (assistant replies and task results); nothing is generated on the phone.
struct ConversationPanel: View {
    @EnvironmentObject var model: AppModel
    var onClose: (() -> Void)?
    @State private var draft = ""

    var body: some View {
        VStack(spacing: 0) {
            if let onClose {
                HStack {
                    Text("Conversation").font(.subheadline.weight(.semibold))
                    Spacer()
                    Button(action: onClose) { Image(systemName: "xmark.circle.fill").font(.title3) }
                        .accessibilityLabel("Close conversation and show the face")
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if model.messages.isEmpty { Text("No messages yet.").foregroundStyle(Theme.muted) }
                        ForEach(model.messages) { m in
                            Text(m.text)
                                .padding(10)
                                .background(m.role == "user" ? Theme.crimson : Color(hex: 0x2A0F16), in: RoundedRectangle(cornerRadius: 14))
                                .frame(maxWidth: .infinity, alignment: m.role == "user" ? .trailing : .leading)
                                .accessibilityLabel("\(m.role == "user" ? "You" : "BAYMAX"): \(m.text)")
                                .id(m.id)
                        }
                    }
                    .padding(12)
                }
                .onAppear { proxy.scrollTo(model.messages.last?.id) }
                .onChange(of: model.messages.count) { _, _ in withAnimation { proxy.scrollTo(model.messages.last?.id) } }
            }
            HStack {
                TextField(model.display.connected ? "Message" : "Not connected to the Mac", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { model.send(text: draft); draft = "" }
                Button("Send") { model.send(text: draft); draft = "" }
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || !model.display.connected)
            }
            .padding(10)
        }
        .foregroundStyle(Theme.white)
    }
}

struct ConversationView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ConversationPanel()
                .background(Theme.background)
                .navigationTitle("Conversation")
                .toolbar { Button("Done") { dismiss() } }
        }
        .preferredColorScheme(.dark)
    }
}

struct TaskSheet: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            List {
                ForEach(model.tasks.values.sorted { ($0.isActive ? 0 : 1, $0.title) < ($1.isActive ? 0 : 1, $1.title) }) { t in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(t.title).font(.headline)
                            Spacer()
                            Text(label(t)).font(.caption).foregroundStyle(color(t))
                        }
                        if let step = t.step, t.isActive { Text(step).font(.caption).foregroundStyle(Theme.muted) }
                        if let p = t.progress, t.isActive { ProgressView(value: p) }
                        if let d = t.detail, t.isActive { Text(d).font(.caption2).foregroundStyle(Theme.muted) }
                        if let s = t.summary { Text(s).font(.caption) }
                        if t.isActive { Button("Cancel task", role: .destructive) { model.cancelActiveTask() } }
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("Tasks")
        }
        .preferredColorScheme(.dark)
    }

    private func label(_ t: TaskSummary) -> String {
        if t.status == "COMPLETED" { return t.verified == true ? "verified" : "done, unverified" }
        return t.status.lowercased().replacingOccurrences(of: "_", with: " ")
    }

    private func color(_ t: TaskSummary) -> Color {
        switch t.status {
        case "COMPLETED": return t.verified == true ? .green : .yellow
        case "FAILED": return .red
        default: return Theme.muted
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var scanning = false
    @State private var error: String?
    @AppStorage("baymax.debugCursor") private var debugCursor = false
    @AppStorage(OrientationController.deskModeKey) private var deskMode = true
    @State private var host = ""
    @State private var port = "8766"
    @State private var fingerprint = ""
    @State private var code = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac") {
                    if let mac = model.paired {
                        LabeledContent("Address", value: "\(mac.host):\(mac.port)")
                        LabeledContent("Status", value: model.connectionText)
                        if !model.macBuild.isEmpty {
                            LabeledContent("Backend build", value: model.macBuild + (model.macBuildStale ? " (outdated)" : ""))
                        }
                        if model.macBuildStale {
                            Text("The Mac is running older BAYMAX code than it has installed. Restart BAYMAX on the Mac (menu bar → Restart Backend) before testing.")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        LabeledContent("Certificate", value: String(mac.fingerprint.prefix(16)) + "…")
                        Button("Forget this Mac", role: .destructive) { model.unpair() }
                    } else {
                        Text("On your Mac open BAYMAX → Workspace → iPhone Pairing → Pair iPhone, then scan the QR code. Both devices must be on the same local network.")
                            .font(.footnote)
                        Button("Scan pairing QR code") { scanning = true }
                    }
                    if let error { Text(error).foregroundStyle(.red).font(.footnote) }
                }
                if model.paired == nil {
                    Section("Pair manually") {
                        TextField("Mac IP address (e.g. 192.168.1.20)", text: $host).keyboardType(.decimalPad)
                        TextField("Port", text: $port).keyboardType(.numberPad)
                        TextField("TLS fingerprint (64 hex characters)", text: $fingerprint).autocorrectionDisabled().textInputAutocapitalization(.never)
                        TextField("6-digit code", text: $code).keyboardType(.numberPad)
                        Button("Pair") { error = model.pairManually(host: host, port: Int(port) ?? 0, fingerprint: fingerprint, code: code) }
                    }
                }
                Section("Desk mode") {
                    Toggle("Landscape desk companion", isOn: $deskMode)
                        .onChange(of: deskMode) { _, on in OrientationController.apply(deskMode: on) }
                    Text("On: BAYMAX's face always shows in landscape, like a small companion on your desk, even with Portrait Orientation Lock on. Off: the screen follows how you hold the phone.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
                Section("Where is this iPhone?") {
                    Picker("Position relative to the Mac display", selection: $model.placement) {
                        Text("Below the screen").tag(Placement.below)
                        Text("Left of the screen").tag(Placement.left)
                        Text("Right of the screen").tag(Placement.right)
                    }
                    Text("BAYMAX turns its head and eyes toward the real Mac cursor from this position.").font(.footnote).foregroundStyle(Theme.muted)
                    Toggle("Cursor debug overlay", isOn: $debugCursor)
                    Text("Shows the raw coordinates received from the Mac, the normalised values, the gaze target and when the last event arrived.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
                Section("Voice") {
                    Toggle("Speak replies to voice requests", isOn: $model.speakReplies)
                    Text("Speech recognition runs on this iPhone only. Audio is never uploaded; if on-device recognition is unsupported, use text input.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
                Section("Safety") {
                    Text("Emergency Stop from the iPhone cancels and blocks all actions; only the Mac can reset it. High-risk approvals must be given on the Mac unless you allow phone approvals in the Mac's settings.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
                Section("About") {
                    Text("BAYMAX is an original healthcare-robot-inspired companion and is not affiliated with Disney.").font(.footnote).foregroundStyle(Theme.muted)
                }
            }
            .navigationTitle("Settings")
            .toolbar { Button("Done") { dismiss() } }
            .sheet(isPresented: $scanning) {
                QRScannerView { text in
                    scanning = false
                    error = model.pair(scanned: text)
                }
                .ignoresSafeArea()
            }
        }
        .preferredColorScheme(.dark)
    }
}

/// Minimal AVFoundation QR scanner (camera used only while this sheet is open).
struct QRScannerView: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let vc = ScannerController()
        vc.onCode = onCode
        return vc
    }

    func updateUIViewController(_ vc: ScannerController, context: Context) {}

    final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        var onCode: ((String) -> Void)?
        private let session = AVCaptureSession()
        private var delivered = false

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            guard let device = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else {
                let label = UILabel()
                label.text = "Camera unavailable. Use manual pairing instead."
                label.textColor = .white
                label.frame = view.bounds.insetBy(dx: 20, dy: 20)
                label.numberOfLines = 0
                view.addSubview(label)
                return
            }
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            if session.canAddOutput(output) {
                session.addOutput(output)
                output.setMetadataObjectsDelegate(self, queue: .main)
                output.metadataObjectTypes = [.qr]
            }
            let preview = AVCaptureVideoPreviewLayer(session: session)
            preview.frame = view.bounds
            preview.videoGravity = .resizeAspectFill
            view.layer.addSublayer(preview)
            DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() }
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            session.stopRunning()
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard !delivered, let obj = objects.first as? AVMetadataMachineReadableCodeObject, let text = obj.stringValue else { return }
            delivered = true
            session.stopRunning()
            onCode?(text)
        }
    }
}
