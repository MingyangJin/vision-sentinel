import Foundation

/// Outcome of handling an APNs notification action response.
public enum NotificationActionOutcome: Equatable, Sendable {
    case openAlert(alertId: String)
    case acknowledged(alertId: String)
    case feedbackSubmitted(alertId: String, rating: FeedbackRating)
    case ignored
}

/// Pure, testable router for APNs notification category `VISION_ALERT` actions.
/// Invoked by `AppDelegate`'s `UNUserNotificationCenterDelegate` when the user
/// taps Acknowledge, Thumbs Up, Thumbs Down, or the notification body itself.
public struct NotificationActionHandler: Sendable {
    private let client: SentinelAPIClient
    private let deviceName: String

    public init(client: SentinelAPIClient, deviceName: String = "iOS Device") {
        self.client = client
        self.deviceName = deviceName
    }

    public func handle(
        actionIdentifier: String,
        userInfo: [AnyHashable: Any]
    ) async throws -> NotificationActionOutcome {
        guard let payload = PushNotificationPayload.parse(userInfo: userInfo) else {
            return .ignored
        }

        switch actionIdentifier {
        case NotificationConstants.actionAcknowledge:
            _ = try await client.acknowledgeAlert(id: payload.alertId, by: deviceName)
            return .acknowledged(alertId: payload.alertId)

        case NotificationConstants.actionFeedbackUp:
            _ = try await client.submitFeedback(
                id: payload.alertId,
                rating: .up,
                note: "",
                by: deviceName
            )
            return .feedbackSubmitted(alertId: payload.alertId, rating: .up)

        case NotificationConstants.actionFeedbackDown:
            _ = try await client.submitFeedback(
                id: payload.alertId,
                rating: .down,
                note: "",
                by: deviceName
            )
            return .feedbackSubmitted(alertId: payload.alertId, rating: .down)

        case NotificationConstants.defaultTapAction:
            return .openAlert(alertId: payload.alertId)

        default:
            return .ignored
        }
    }
}
