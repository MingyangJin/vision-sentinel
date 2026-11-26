import Foundation
import UIKit
import UserNotifications
import VisionSentinelCore

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        registerNotificationCategories(center: center)
        return true
    }

    /// Registers the `VISION_ALERT` category with inline Acknowledge, Thumbs Up, and Thumbs Down actions.
    func registerNotificationCategories(center: UNUserNotificationCenter = .current()) {
        let ackAction = UNNotificationAction(
            identifier: NotificationConstants.actionAcknowledge,
            title: "Acknowledge",
            options: []
        )
        let thumbsUpAction = UNNotificationAction(
            identifier: NotificationConstants.actionFeedbackUp,
            title: "Thumbs Up (True)",
            options: []
        )
        let thumbsDownAction = UNNotificationAction(
            identifier: NotificationConstants.actionFeedbackDown,
            title: "Thumbs Down (False)",
            options: [.destructive]
        )

        let category = UNNotificationCategory(
            identifier: NotificationConstants.alertCategoryIdentifier,
            actions: [ackAction, thumbsUpAction, thumbsDownAction],
            intentIdentifiers: [],
            options: [.customDismissAction]
        )
        center.setNotificationCategories([category])
    }

    /// Requests push notification authorization and registers with APNs if granted.
    static func requestPushPermissionAndRegister() {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
            Task { @MainActor in
                await AppState.shared.refreshNotificationAuthStatus()
                if granted {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
        }
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let hex = hexEncodedDeviceToken(deviceToken)
        Task { @MainActor in
            await AppState.shared.updateAPNsToken(hex)
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        Task { @MainActor in
            AppState.shared.lastRegistrationMessage = "APNs registration failed: \(error.localizedDescription)"
        }
    }

    // Show alerts even while the app is in the foreground.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        Task { @MainActor in
            await AppState.shared.refreshAlerts()
        }
        return [.banner, .sound, .list]
    }

    // Handle inline Acknowledge / Thumbs Up / Thumbs Down actions or notification taps.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        let actionId = response.actionIdentifier

        let defaults = UserDefaults.standard
        let baseURL = defaults.string(forKey: SentinelDefaultsKeys.baseURL) ?? ""
        let deviceName = defaults.string(forKey: SentinelDefaultsKeys.deviceName) ?? "iOS Device"
        let token = KeychainTokenStore().loadToken() ?? ""

        let config = SentinelServerConfig(
            baseURLString: baseURL,
            householdToken: token,
            deviceName: deviceName
        )

        guard let client = try? SentinelAPIClient(config: config) else {
            if let payload = PushNotificationPayload.parse(userInfo: userInfo) {
                await MainActor.run {
                    AppState.shared.deepLinkAlertId = payload.alertId
                }
            }
            return
        }

        let handler = NotificationActionHandler(client: client, deviceName: deviceName)
        if let outcome = try? await handler.handle(actionIdentifier: actionId, userInfo: userInfo) {
            await MainActor.run {
                switch outcome {
                case .openAlert(let alertId):
                    Task { await AppState.shared.openAlert(id: alertId) }
                case .acknowledged, .feedbackSubmitted:
                    Task { await AppState.shared.refreshAlerts() }
                case .ignored:
                    break
                }
            }
        }
    }
}
