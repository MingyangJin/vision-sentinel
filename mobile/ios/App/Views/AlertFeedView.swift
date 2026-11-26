import SwiftUI
import VisionSentinelCore

struct AlertFeedView: View {
    @Environment(AppState.self) private var appState
    @State private var navigationPath = NavigationPath()

    var body: some View {
        @Bindable var state = appState

        NavigationStack(path: $navigationPath) {
            Group {
                if !appState.isConfigured {
                    ContentUnavailableView {
                        Label("Server Not Configured", systemImage: "server.rack")
                    } description: {
                        Text("Enter your Vision Sentinel Notification Service URL and shared household token in Settings.")
                    } actions: {
                        Button("Open Settings") {
                            appState.selectedTab = 1
                        }
                        .buttonStyle(.borderedProminent)
                    }
                } else {
                    VStack(spacing: 0) {
                        Picker("Filter", selection: $state.filter) {
                            ForEach(AlertFeedFilter.allCases) { item in
                                Text(item.rawValue).tag(item)
                            }
                        }
                        .pickerStyle(.segmented)
                        .padding(.horizontal)
                        .padding(.vertical, 8)

                        if let err = appState.alertsError {
                            HStack(spacing: 8) {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(.orange)
                                Text(err)
                                    .font(.footnote)
                                Spacer()
                            }
                            .padding(.horizontal)
                            .padding(.vertical, 6)
                            .background(.orange.opacity(0.12))
                        }

                        if appState.filteredAlerts.isEmpty && !appState.isLoadingAlerts {
                            ContentUnavailableView(
                                "No Alerts",
                                systemImage: "checkmark.shield",
                                description: Text("Pull down to refresh alerts from your Notification Service.")
                            )
                        } else {
                            List(appState.filteredAlerts) { alert in
                                NavigationLink(value: alert.id) {
                                    AlertRowView(alert: alert)
                                }
                            }
                            .listStyle(.plain)
                            .refreshable {
                                await appState.refreshAlerts()
                            }
                        }
                    }
                }
            }
            .navigationTitle("Vision Sentinel")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await appState.refreshAlerts() }
                    } label: {
                        if appState.isLoadingAlerts {
                            ProgressView()
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(!appState.isConfigured || appState.isLoadingAlerts)
                }
            }
            .navigationDestination(for: String.self) { alertId in
                AlertDetailView(alertId: alertId)
            }
            .onChange(of: appState.deepLinkAlertId) { _, newValue in
                if let alertId = newValue {
                    navigationPath = NavigationPath()
                    navigationPath.append(alertId)
                    appState.deepLinkAlertId = nil
                }
            }
        }
    }
}

private struct AlertRowView: View {
    let alert: AlertRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                SeverityBadge(severity: alert.severity)
                Text(alert.topicId)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer()
                Text(alert.createdAt, style: .relative)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(alert.title)
                .font(.headline)
                .lineLimit(2)

            if !alert.body.isEmpty {
                Text(alert.body)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 10) {
                if alert.isAcknowledged {
                    Label("Acked", systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                } else {
                    Label("Unacknowledged", systemImage: "circle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }

                if let fb = alert.latestFeedback {
                    Label(
                        fb.rating == .up ? "True Alarm" : "False Alarm",
                        systemImage: fb.rating == .up ? "hand.thumbsup.fill" : "hand.thumbsdown.fill"
                    )
                    .font(.caption)
                    .foregroundStyle(fb.rating == .up ? .blue : .red)
                }

                if alert.hasClip || alert.hasThumbnail {
                    Label("Media", systemImage: "film")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

struct SeverityBadge: View {
    let severity: AlertSeverity

    var color: Color {
        switch severity {
        case .info: return .blue
        case .warning: return .orange
        case .urgent: return .red
        }
    }

    var body: some View {
        Text(severity.displayName.uppercased())
            .font(.caption2.weight(.bold))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .foregroundStyle(.white)
            .background(color, in: Capsule())
    }
}
