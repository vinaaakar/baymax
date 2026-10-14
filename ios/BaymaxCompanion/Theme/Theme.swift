import SwiftUI

enum Theme {
    static let background = Color(hex: 0x180509)
    static let crimson = Color(hex: 0xB5122A)
    static let glow = Color(hex: 0xFF334F)
    static let shadow = Color(hex: 0x080305)
    static let white = Color(hex: 0xF5F5F2)
    static let face = Color(hex: 0x121214)
    static let panel = Color(hex: 0x14090C).opacity(0.78)
    static let muted = Color(hex: 0xC9A9AE)

    /// Deep burgundy room: prominent red that never overpowers the white character.
    static var stage: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x1F060C), background, shadow], startPoint: .top, endPoint: .bottom)
            RadialGradient(colors: [Color(hex: 0x3A0A14), Color(hex: 0x24070D).opacity(0.6), .clear], center: .top, startRadius: 0, endRadius: 520)
            RadialGradient(colors: [glow.opacity(0.22), crimson.opacity(0.10), .clear], center: UnitPoint(x: 0.5, y: 0.62), startRadius: 10, endRadius: 330)
        }
        .ignoresSafeArea()
    }
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255, opacity: alpha)
    }
}

struct PillStyle: ViewModifier {
    func body(content: Content) -> some View {
        content.font(.caption).padding(.horizontal, 10).padding(.vertical, 5)
            .background(.ultraThinMaterial.opacity(0.6), in: Capsule())
            .overlay(Capsule().strokeBorder(.white.opacity(0.08)))
    }
}

extension View {
    func pill() -> some View { modifier(PillStyle()) }
}
