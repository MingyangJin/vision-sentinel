import Foundation
import XCTest
@testable import VisionSentinelCore

final class NotificationActionHandlerTests: XCTestCase {
    private let baseURL = URL(string: "https://sentinel.example.com:8790")!
    private let token = "test-household-token-002"

    private let alertJSON = """
    {
      "id": "alert_push_1",
      "request": {
        "topic_id": "garage-door",
        "severity": "warning",
        "title": "Garage door open",
        "body": "Open for more than 10 minutes."
      },
      "created_at": "2026-11-25T16:40:00Z",
      "status": "sent",
      "deliveries": {},
      "acked_at": "2026-11-25T16:41:00Z",
      "acked_by": "Sample iPhone",
      "feedback": []
    }
    """

    func testParseAPNsUserInfo() {
        let userInfo: [AnyHashable: Any] = [
            "aps": [
                "category": "VISION_ALERT",
                "alert": ["title": "Garage door open", "body": "Open for 10m"],
            ],
            "alert_id": "alert_push_1",
            "topic_id": "garage-door",
            "severity": "warning",
            "bundle_id": "bundle-42",
            "requires_ack": true,
        ]
        let payload = PushNotificationPayload.parse(userInfo: userInfo)
        XCTAssertEqual(payload?.alertId, "alert_push_1")
        XCTAssertEqual(payload?.topicId, "garage-door")
        XCTAssertEqual(payload?.severity, .warning)
        XCTAssertEqual(payload?.title, "Garage door open")
        XCTAssertEqual(payload?.body, "Open for 10m")
        XCTAssertEqual(payload?.bundleId, "bundle-42")
        XCTAssertEqual(payload?.requiresAck, true)
    }

    func testNotificationActionsDispatchAckAndFeedback() async throws {
        var observedPaths: [String] = []
        let transport = MockHTTPTransport { req in
            let path = req.url?.path ?? ""
            observedPaths.append(path)
            if path.hasSuffix("/ack") {
                return .init(statusCode: 200, data: Data(self.alertJSON.utf8), headers: [:])
            }
            if path.hasSuffix("/feedback") {
                let body = try JSONSerialization.jsonObject(with: req.httpBody!) as? [String: String]
                let rating = body?["rating"] ?? "up"
                let resp = """
                {
                  "feedback": {"alert_id":"alert_push_1","rating":"\(rating)","note":"","by":"Sample iPhone","at":"2026-11-25T16:42:00Z"},
                  "pinned": true,
                  "alert": \(self.alertJSON)
                }
                """
                return .init(statusCode: 200, data: Data(resp.utf8), headers: [:])
            }
            return .init(statusCode: 404, data: Data(), headers: [:])
        }

        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        let handler = NotificationActionHandler(client: client, deviceName: "Sample iPhone")
        let userInfo: [AnyHashable: Any] = ["alert_id": "alert_push_1", "severity": "warning"]

        let ackOutcome = try await handler.handle(
            actionIdentifier: NotificationConstants.actionAcknowledge,
            userInfo: userInfo
        )
        XCTAssertEqual(ackOutcome, .acknowledged(alertId: "alert_push_1"))

        let upOutcome = try await handler.handle(
            actionIdentifier: NotificationConstants.actionFeedbackUp,
            userInfo: userInfo
        )
        XCTAssertEqual(upOutcome, .feedbackSubmitted(alertId: "alert_push_1", rating: .up))

        let downOutcome = try await handler.handle(
            actionIdentifier: NotificationConstants.actionFeedbackDown,
            userInfo: userInfo
        )
        XCTAssertEqual(downOutcome, .feedbackSubmitted(alertId: "alert_push_1", rating: .down))

        let tapOutcome = try await handler.handle(
            actionIdentifier: NotificationConstants.defaultTapAction,
            userInfo: userInfo
        )
        XCTAssertEqual(tapOutcome, .openAlert(alertId: "alert_push_1"))

        XCTAssertEqual(observedPaths, [
            "/v1/alerts/alert_push_1/ack",
            "/v1/alerts/alert_push_1/feedback",
            "/v1/alerts/alert_push_1/feedback",
        ])
    }
}
