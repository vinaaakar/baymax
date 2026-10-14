import Foundation

/// Layout rules for the iPhone companion screen (landscape-first desk mode). Pure, so they are unit-tested.
/// Portrait: the face gets its own region between the top bar and the controls. Landscape face mode: the face fills the
/// screen and the bars appear only when tapped or when something needs attention.
public struct CompanionLayout: Equatable, Sendable {
    public enum Mode: Equatable, Sendable {
        case faceOnly                 // Companion Face Mode (default)
        case faceBesideConversation   // landscape: smaller face on the left, conversation panel on the right
        case faceWithConversationSheet // portrait / very narrow: conversation opens as a sheet over the face screen
    }

    public var landscape: Bool
    public var mode: Mode
    /// Width of the face region as a fraction of the content width.
    public var faceWidthFraction: Double
    /// Diameter of the microphone button (smaller in landscape so the face keeps the height).
    public var micDiameter: Double
    /// Maximum lines of the last reply shown under the face.
    public var replyLines: Int
    /// Landscape face mode: the face fills the whole screen (like a desk device); the bars are overlaid on demand.
    public var immersive: Bool { landscape && mode == .faceOnly }

    /// Minimum landscape width (points, inside safe areas) for the side-by-side conversation layout.
    public static let sideBySideMinWidth = 560.0

    public static func compute(width: Double, height: Double, conversationOpen: Bool) -> CompanionLayout {
        let landscape = width > height
        let mic = landscape ? 52.0 : 64.0
        let lines = landscape ? 2 : 3
        guard conversationOpen else {
            return CompanionLayout(landscape: landscape, mode: .faceOnly, faceWidthFraction: 1, micDiameter: mic, replyLines: lines)
        }
        if landscape && width >= sideBySideMinWidth {
            return CompanionLayout(landscape: true, mode: .faceBesideConversation, faceWidthFraction: 0.38, micDiameter: mic, replyLines: 0)
        }
        return CompanionLayout(landscape: landscape, mode: .faceWithConversationSheet, faceWidthFraction: 1, micDiameter: mic, replyLines: lines)
    }
}
