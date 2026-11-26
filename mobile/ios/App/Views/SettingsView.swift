import SwiftUI
import UserNotifications

struct SettingsView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState

        NavigationStack {
            Form {
                Section {
                    TextField("https://sentinel.example.com:8790", text: $state.baseURLString)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("Shared Household Bearer Token", text: $state.householdToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    TextField("Device Name", text: $state.deviceName)
                } header: {
                    Text("Notification Service")
                } footer: {
                    Text("The bearer token is stored in the iOS Keychain and sent via Authorization: Bearer on all device API calls.")
                }

                Section {
                    Button {
                        Task { await appState.testConnection() }
                    } label: {
                        HStack {
                            if appState.isTestingConnection {
                                ProgressView()
                            } else {
                                Image(systemName: "network")
                            }
                            Text(appState.isTestingConnection ? "Testing Connection…" : "Save & Test Connection")
                        }
                    }
                    .disabled(appState.isTestingConnection)

                    if let status = appState.connectionStatus {
                        Label(
                            status,
                            systemImage: appState.connectionOK ? "checkmark.circle.fill" : "xmark.octagon.fill"
                        )
                        .font(.footnote)
                        .foregroundStyle(appState.connectionOK ? .green : .red)
                    }
                }

                Section {
                    LabeledContent("Permission", value: authStatusLabel(appState.notificationAuthStatus))

                    if let token = appState.apnsDeviceToken {
                        LabeledContent("APNs Token", value: "\(token.prefix(8))…\(token.suffix(6))")
                            .font(.caption.monospaced())
                    } else {
                        LabeledContent("APNs Token", value: "Not received yet")
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        AppDelegate.requestPushPermissionAndRegister()
                    } label: {
                        Label("Request Push Permission & Fetch APNs Token", systemImage: "bell.badge.fill")
                    }

                    Button {
                        Task { await appState.registerCurrentDevice() }
                    } label: {
                        Label("Register Device with Server", systemImage: "iphone.radiowaves.left.and.right")
                    }
                    .disabled(!appState.isConfigured || appState.apnsDeviceToken == nil)

                    if let msg = appState.lastRegistrationMessage {
                        Text(msg)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Apple Push Notifications (APNs)")
                } footer: {
                    Text("Registers this device via POST /v1/devices/register with platform \"ios\" and enables inline Acknowledge / Thumbs Up / Thumbs Down notification actions.")
                }
            }
            .navigationTitle("Settings")
            .task {
                await appState.refreshNotificationAuthStatus()
            }
        }
    }

    private func authStatusLabel(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "Authorized"
        case .provisional: return "Provisional"
        case .ephemeral: return "Ephemeral"
        case .denied: return "Denied (enable in iOS Settings)"
        case .notDetermined: return "Not requested"
        @unknown default: return "Unknown"
        }
    }
}
