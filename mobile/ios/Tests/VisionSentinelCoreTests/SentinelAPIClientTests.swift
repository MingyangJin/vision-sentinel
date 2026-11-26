import Foundation
import XCTest
@testable import VisionSentinelCore

final class MockHTTPTransport: HTTPTransport, @unchecked Sendable {
    struct Stub {
        let statusCode: Int
        let data: Data
        let headers: [String: String]
    }

    private let lock = NSLock()
    private var _requests: [URLRequest] = []
    private let handler: (URLRequest) throws -> Stub

    init(handler: @escaping (URLRequest) throws -> Stub) {
        self.handler = handler
    }

    var requests: [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return _requests
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        lock.withLock {
            _requests.append(request)
        }

        let stub = try handler(request)
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: stub.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: stub.headers
        )!
        return (stub.data, response)
    }
}

final class SentinelAPIClientTests: XCTestCase {
    private let baseURL = URL(string: "https://sentinel.example.com:8790/")!
    private let token = "test-household-token-001"

    private let sampleAlertJSON = """
    {
      "id": "alert_abc123",
      "request": {
        "topic_id": "front-porch",
        "severity": "urgent",
        "title": "Package delivered",
        "body": "A parcel was left by the front door.",
        "clip_ref": "cam1/clip.mp4",
        "dedupe_key": "parcel",
        "cooldown_sec": 300.0,
        "requires_ack": true,
        "bundle_id": "20261125-160000-001",
        "camera_ids": ["porch-cam"],
        "source": "main_agent",
        "rubric_version": "9f86d081",
        "tool_versions": {"motion.py": "abc123"},
        "speaker": false
      },
      "created_at": "2026-11-25T16:25:40.123456+00:00",
      "status": "sent",
      "deliveries": {"apns": "ok", "desktop": "ok"},
      "acked_at": null,
      "acked_by": null,
      "feedback": [],
      "has_thumbnail": true,
      "has_clip": true
    }
    """

    func testDecodeAlertRecordWithFractionalISO8601() throws {
        let data = Data(sampleAlertJSON.utf8)
        let alert = try SentinelJSONDecoder.make().decode(AlertRecord.self, from: data)
        XCTAssertEqual(alert.id, "alert_abc123")
        XCTAssertEqual(alert.topicId, "front-porch")
        XCTAssertEqual(alert.severity, .urgent)
        XCTAssertEqual(alert.title, "Package delivered")
        XCTAssertEqual(alert.request.cameraIds, ["porch-cam"])
        XCTAssertFalse(alert.isAcknowledged)
        XCTAssertTrue(alert.hasThumbnail)
        XCTAssertTrue(alert.hasClip)
    }

    func testConfigNormalizationAndValidation() {
        let valid = SentinelServerConfig(
            baseURLString: "  https://sentinel.example.com:8790/// ",
            householdToken: "abc"
        )
        XCTAssertEqual(valid.normalizedBaseURL?.absoluteString, "https://sentinel.example.com:8790")
        XCTAssertTrue(valid.isConfigured)

        let invalid = SentinelServerConfig(baseURLString: "ftp://example.com", householdToken: "abc")
        XCTAssertNil(invalid.normalizedBaseURL)
        XCTAssertFalse(invalid.isConfigured)

        let missingToken = SentinelServerConfig(baseURLString: "https://sentinel.example.com", householdToken: "   ")
        XCTAssertFalse(missingToken.isConfigured)
    }

    func testConnectionTestVerifiesHealthAndAuth() async throws {
        let transport = MockHTTPTransport { req in
            switch req.url?.path {
            case "/healthz":
                XCTAssertNil(req.value(forHTTPHeaderField: "Authorization"))
                return .init(statusCode: 200, data: Data(#"{"ok":true,"sinks":["apns","webhook"]}"#.utf8), headers: [:])
            case "/v1/alerts":
                XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer \(self.token)")
                XCTAssertEqual(req.url?.query, "limit=1")
                return .init(statusCode: 200, data: Data("[\(self.sampleAlertJSON)]".utf8), headers: [:])
            default:
                XCTFail("Unexpected path: \(req.url?.path ?? "")")
                return .init(statusCode: 404, data: Data(), headers: [:])
            }
        }
        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        let result = try await client.testConnection()
        XCTAssertEqual(result.sinks, ["apns", "webhook"])
        XCTAssertEqual(result.alertCountChecked, 1)
    }

    func testUnauthorizedReturnsTypedErrorWithoutTokenLeak() async throws {
        let transport = MockHTTPTransport { _ in
            .init(statusCode: 401, data: Data(#"{"detail":"missing or invalid household token"}"#.utf8), headers: [:])
        }
        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        do {
            _ = try await client.listAlerts()
            XCTFail("Expected unauthorized error")
        } catch let error as SentinelAPIError {
            XCTAssertEqual(error, .unauthorized)
            XCTAssertFalse(error.localizedDescription.contains(self.token))
        }
    }

    func testRegisterDeviceSendsPlatformIOSAndBearerToken() async throws {
        let transport = MockHTTPTransport { req in
            XCTAssertEqual(req.httpMethod, "POST")
            XCTAssertEqual(req.url?.path, "/v1/devices/register")
            XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer \(self.token)")
            let body = try JSONSerialization.jsonObject(with: req.httpBody!) as? [String: String]
            XCTAssertEqual(body?["platform"], "ios")
            XCTAssertEqual(body?["token"], "deadbeef0102")
            XCTAssertEqual(body?["name"], "Test iPhone")
            let resp = #"{"id":"dev_1","platform":"ios","name":"Test iPhone","registered_at":"2026-11-25T16:30:00+00:00"}"#
            return .init(statusCode: 200, data: Data(resp.utf8), headers: [:])
        }
        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        let reg = try await client.registerDevice(pushToken: "deadbeef0102", deviceName: "Test iPhone")
        XCTAssertEqual(reg.id, "dev_1")
        XCTAssertEqual(reg.platform, "ios")
    }

    func testListAlertsQueryParameters() async throws {
        let transport = MockHTTPTransport { req in
            let items = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let map = Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
            XCTAssertEqual(map["topic_id"], "front-porch")
            XCTAssertEqual(map["severity"], "urgent")
            XCTAssertEqual(map["unacked"], "true")
            XCTAssertEqual(map["limit"], "25")
            return .init(statusCode: 200, data: Data("[\(self.sampleAlertJSON)]".utf8), headers: [:])
        }
        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        let alerts = try await client.listAlerts(topicId: "front-porch", severity: .urgent, unacked: true, limit: 25)
        XCTAssertEqual(alerts.count, 1)
    }

    func testAcknowledgeAndFeedbackAndMediaEndpoints() async throws {
        let ackedJSON = sampleAlertJSON
            .replacingOccurrences(of: #""acked_at": null"#, with: #""acked_at": "2026-11-25T16:26:00+00:00""#)
            .replacingOccurrences(of: #""acked_by": null"#, with: #""acked_by": "Test iPhone""#)

        let feedbackJSON = """
        {
          "feedback": {
            "alert_id": "alert_abc123",
            "rating": "down",
            "note": "Tree shadow",
            "by": "Test iPhone",
            "at": "2026-11-25T16:27:00Z"
          },
          "pinned": true,
          "alert": \(ackedJSON)
        }
        """

        let transport = MockHTTPTransport { req in
            XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer \(self.token)")
            switch (req.httpMethod, req.url?.path) {
            case ("POST", "/v1/alerts/alert_abc123/ack"):
                let body = try JSONSerialization.jsonObject(with: req.httpBody!) as? [String: String]
                XCTAssertEqual(body?["by"], "Test iPhone")
                return .init(statusCode: 200, data: Data(ackedJSON.utf8), headers: [:])
            case ("POST", "/v1/alerts/alert_abc123/feedback"):
                let body = try JSONSerialization.jsonObject(with: req.httpBody!) as? [String: String]
                XCTAssertEqual(body?["rating"], "down")
                XCTAssertEqual(body?["note"], "Tree shadow")
                XCTAssertEqual(body?["by"], "Test iPhone")
                return .init(statusCode: 200, data: Data(feedbackJSON.utf8), headers: [:])
            case ("GET", "/v1/alerts/alert_abc123/thumbnail"):
                return .init(statusCode: 200, data: Data([0xFF, 0xD8, 0xFF]), headers: ["Content-Type": "image/jpeg"])
            case ("GET", "/v1/alerts/alert_abc123/clip"):
                return .init(statusCode: 200, data: Data([0x00, 0x00, 0x00, 0x18]), headers: ["Content-Type": "video/mp4"])
            default:
                XCTFail("Unexpected request: \(req.httpMethod ?? "") \(req.url?.path ?? "")")
                return .init(statusCode: 404, data: Data(), headers: [:])
            }
        }

        let client = try SentinelAPIClient(baseURL: baseURL, householdToken: token, transport: transport)
        let acked = try await client.acknowledgeAlert(id: "alert_abc123", by: "Test iPhone")
        XCTAssertTrue(acked.isAcknowledged)
        XCTAssertEqual(acked.ackedBy, "Test iPhone")

        let fb = try await client.submitFeedback(id: "alert_abc123", rating: .down, note: "Tree shadow", by: "Test iPhone")
        XCTAssertTrue(fb.pinned)
        XCTAssertEqual(fb.feedback.rating, .down)
        XCTAssertEqual(fb.feedback.note, "Tree shadow")

        let thumb = try await client.fetchThumbnail(alertId: "alert_abc123")
        XCTAssertEqual(thumb, Data([0xFF, 0xD8, 0xFF]))

        let clip = try await client.fetchClip(alertId: "alert_abc123")
        XCTAssertEqual(clip, Data([0x00, 0x00, 0x00, 0x18]))
    }

    func testInMemoryTokenStoreLifecycle() throws {
        let store = InMemoryTokenStore()
        XCTAssertNil(store.loadToken())
        try store.saveToken("  secret-123  ")
        XCTAssertEqual(store.loadToken(), "secret-123")
        try store.deleteToken()
        XCTAssertNil(store.loadToken())
    }

    func testHexEncodedDeviceToken() {
        let data = Data([0x0A, 0xFF, 0x00, 0x1B])
        XCTAssertEqual(hexEncodedDeviceToken(data), "0aff001b")
    }
}
