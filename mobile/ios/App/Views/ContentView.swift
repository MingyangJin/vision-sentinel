import SwiftUI

struct ContentView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState

        TabView(selection: $state.selectedTab) {
            AlertFeedView()
                .tabItem {
                    Label("Alerts", systemImage: "bell.badge")
                }
                .badge(appState.unackedCount)
                .tag(0)

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gearshape")
                }
                .tag(1)
        }
    }
}
