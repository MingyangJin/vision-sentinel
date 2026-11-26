import SwiftUI

@main
struct VisionSentinelApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var appState = AppState.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(appState)
                .task {
                    await appState.refreshNotificationAuthStatus()
                    if appState.isConfigured {
                        await appState.refreshAlerts()
                    } else {
                        appState.selectedTab = 1
                    }
                }
        }
    }
}
