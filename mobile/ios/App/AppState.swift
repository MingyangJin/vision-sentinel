import Foundation
import Observation
import UserNotifications
import VisionSentinelCore
#if canImport(UIKit)
import UIKit
#endif

public enum AlertFeedFilter: String, CaseIterable, Identifiable {
    case all = "All"
    case unacked = "Unacked"
    case urgent = "Urgent"

    public var id: String { rawValue }
}

/// Shared keys between `AppState` and background `AppDelegate` notification actions.
enum SentinelDefaultsKeys {
    static let baseURL = "vision_sentinel.server_base_url"
    static let deviceName = "vision_sentinel.device_name"
    static let lastAPNsToken = "vision_sentinel.last_apns_token"
}

@MainActor
@Observable
final class AppState {
    static let shared = AppState()

    var baseURLString: String
    var householdToken: String
    var deviceName: String

    var apnsDeviceToken: String?
    var notificationAuthStatus: UNAuthorizationStatus = .notDetermined
    var lastRegisteredAt: Date?
    var lastRegistrationMessage: String?

    var connectionStatus: String?
    var connectionOK: Bool = false
    var isTestingConnection: Bool = false

    var alerts: [AlertRecord] = []
    var filter: AlertFeedFilter = .all
    var isLoadingAlerts: Bool = false
    var alertsError: String?

    var deepLinkAlertId: String?
    var selectedTab: Int = 0

    private let tokenStore: TokenStoring
    private let defaults: UserDefaults

    init(
        tokenStore: TokenStoring = KeychainTokenStore(),
        defaults: UserDefaults = .standard
    ) {
        self.tokenStore = tokenStore
        self.defaults = defaults
        self.baseURLString = defaults.string(forKey: SentinelDefaultsKeys.baseURL) ?? ""
        self.householdToken = tokenStore.loadToken() ?? ""
        #if canImport(UIKit)
        let defaultName = UIDevice.current.name
        #else
        let defaultName = "iOS Device"
        #endif
        self.deviceName = defaults.string(forKey: SentinelDefaultsKeys.deviceName) ?? defaultName
        self.apnsDeviceToken = defaults.string(forKey: SentinelDefaultsKeys.lastAPNsToken)
    }

    var config: SentinelServerConfig {
        SentinelServerConfig(
            baseURLString: baseURLString,
            householdToken: householdToken,
            deviceName: deviceName
        )
    }

    var isConfigured: Bool { config.isConfigured }

    var filteredAlerts: [AlertRecord] {
        switch filter {
        case .all:
            return alerts
        case .unacked:
            return alerts.filter { !$0.isAcknowledged }
        case .urgent:
            return alerts.filter { $0.severity == .urgent }
        }
    }

    var unackedCount: Int {
        alerts.filter { !$0.isAcknowledged }.count
    }

    func makeClient() throws -> SentinelAPIClient {
        try SentinelAPIClient(config: config)
    }

    func saveConfiguration() throws {
        let trimmedURL = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedName = deviceName.trimmingCharacters(in: .whitespacesAndNewlines)
        defaults.set(trimmedURL, forKey: SentinelDefaultsKeys.baseURL)
        defaults.set(trimmedName.isEmpty ? "iOS Device" : trimmedName, forKey: SentinelDefaultsKeys.deviceName)
        try tokenStore.saveToken(householdToken)
        baseURLString = trimmedURL
        deviceName = trimmedName.isEmpty ? "iOS Device" : trimmedName
    }

    func testConnection() async {
        isTestingConnection = true
        connectionStatus = nil
        defer { isTestingConnection = false }

        do {
            try saveConfiguration()
            let client = try makeClient()
            let result = try await client.testConnection()
            connectionOK = true
            let sinkSummary = result.sinks.isEmpty ? "none configured" : result.sinks.joined(separator: ", ")
            connectionStatus = "Connected. Active sinks: \(sinkSummary)."
        } catch {
            connectionOK = false
            connectionStatus = error.localizedDescription
        }
    }

    func refreshNotificationAuthStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationAuthStatus = settings.authorizationStatus
    }

    func updateAPNsToken(_ hexToken: String) async {
        apnsDeviceToken = hexToken
        defaults.set(hexToken, forKey: SentinelDefaultsKeys.lastAPNsToken)
        if isConfigured {
            await registerCurrentDevice()
        }
    }

    func registerCurrentDevice() async {
        guard let token = apnsDeviceToken, !token.isEmpty else {
            lastRegistrationMessage = "No APNs device token yet. Enable push notifications on a physical iOS device."
            return
        }
        do {
            try saveConfiguration()
            let client = try makeClient()
            let resp = try await client.registerDevice(pushToken: token, deviceName: deviceName, platform: "ios")
            lastRegisteredAt = resp.registeredAt
            lastRegistrationMessage = "Registered as \"\(resp.name)\" (\(resp.platform))."
        } catch {
            lastRegistrationMessage = "Registration failed: \(error.localizedDescription)"
        }
    }

    func refreshAlerts() async {
        guard isConfigured else {
            alerts = []
            alertsError = nil
            return
        }
        isLoadingAlerts = true
        alertsError = nil
        defer { isLoadingAlerts = false }

        do {
            let client = try makeClient()
            alerts = try await client.listAlerts(limit: 100)
        } catch {
            alertsError = error.localizedDescription
        }
    }

    func acknowledge(alertId: String) async throws -> AlertRecord {
        let client = try makeClient()
        let updated = try await client.acknowledgeAlert(id: alertId, by: deviceName)
        replaceAlert(updated)
        return updated
    }

    func submitFeedback(alertId: String, rating: FeedbackRating, note: String) async throws -> FeedbackResponse {
        let client = try makeClient()
        let response = try await client.submitFeedback(
            id: alertId,
            rating: rating,
            note: note,
            by: deviceName
        )
        replaceAlert(response.alert)
        return response
    }

    func fetchThumbnailData(alertId: String) async throws -> Data {
        let client = try makeClient()
        return try await client.fetchThumbnail(alertId: alertId)
    }

    /// Downloads the authenticated MP4 clip to a temporary file so `AVPlayer` can play it cleanly.
    func downloadClipToTemporaryFile(alertId: String) async throws -> URL {
        let client = try makeClient()
        let data = try await client.fetchClip(alertId: alertId)
        let safeId = alertId.replacingOccurrences(of: "/", with: "_")
        let dest = FileManager.default.temporaryDirectory.appendingPathComponent("vs-clip-\(safeId).mp4")
        try data.write(to: dest, options: .atomic)
        return dest
    }

    func openAlert(id: String) async {
        selectedTab = 0
        deepLinkAlertId = id
        if !alerts.contains(where: { $0.id == id }) {
            await refreshAlerts()
        }
    }

    private func replaceAlert(_ updated: AlertRecord) {
        if let idx = alerts.firstIndex(where: { $0.id == updated.id }) {
            alerts[idx] = updated
        } else {
            alerts.insert(updated, at: 0)
        }
    }
}
