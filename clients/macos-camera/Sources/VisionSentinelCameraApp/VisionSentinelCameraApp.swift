import AppKit
import SwiftUI

@main
struct VisionSentinelCameraApp: App {
    @StateObject private var publisher = CapturePublisher()

    init() {
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async {
            NSApplication.shared.activate(ignoringOtherApps: true)
        }
    }

    var body: some Scene {
        WindowGroup("Vision Sentinel Camera") {
            ContentView(publisher: publisher)
                .preferredColorScheme(.dark)
        }
        .windowResizability(.contentSize)
    }
}
