import SwiftUI
import UIKit

@main
struct BaymaxCompanionApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            CompanionScreen()
                .environmentObject(model)
                .statusBarHidden(false)
                .onAppear { OrientationController.apply() }
        }
        .onChange(of: phase) { _, newPhase in
            switch newPhase {
            case .active:
                model.appBecameActive()
                OrientationController.apply()
            case .background: model.appWentToBackground()
            default: break
            }
        }
    }
}

/// Landscape-first "desk companion" orientation.
///
/// iOS decides rotation from (a) Info.plist's supported orientations, (b) what the app delegate returns for the
/// window, and (c) a geometry request. With Desk mode on we allow only landscape and ask the scene to rotate, so the
/// face appears horizontally even if the phone is held upright or Portrait Orientation Lock is on (iOS honours a
/// landscape-only app). With Desk mode off, landscape and portrait both follow the device.
@MainActor
enum OrientationController {
    nonisolated static let deskModeKey = "baymax.deskMode"
    static private(set) var mask: UIInterfaceOrientationMask = .landscape

    static var deskMode: Bool {
        UserDefaults.standard.object(forKey: deskModeKey) as? Bool ?? true // landscape is the default experience
    }

    static func apply(deskMode: Bool? = nil) {
        let on = deskMode ?? Self.deskMode
        mask = on ? .landscape : [.landscape, .portrait]
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            scene.windows.first?.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in
                // iOS declines only if the mask is impossible for this device; the UI keeps working either way.
            }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        MainActor.assumeIsolated { OrientationController.mask }
    }
}
