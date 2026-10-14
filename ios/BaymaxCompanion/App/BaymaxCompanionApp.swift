import SwiftUI

@main
struct BaymaxCompanionApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            CompanionScreen()
                .environmentObject(model)
                .statusBarHidden(false)
        }
        .onChange(of: phase) { _, newPhase in
            switch newPhase {
            case .active: model.appBecameActive()
            case .background: model.appWentToBackground()
            default: break
            }
        }
    }
}
