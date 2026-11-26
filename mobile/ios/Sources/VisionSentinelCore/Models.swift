import Foundation

/// Severity tier emitted by Vision Sentinel's Notification Service.
public enum AlertSeverity: String, Codable, CaseIterable, Sendable {
    case info
    case warning
    case urgent

    public var displayName: String {
        switch self {
        case .info: return "Info"
        case .warning: return "Warning"
        case .urgent: return "Urgent"
        }
    }
}

/// Human feedback rating: thumbs up ("right to alert") or thumbs down ("false alarm").
public enum FeedbackRating: String, Codable, CaseIterable, Identifiable, Sendable {
    case up
    case down

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .up: return "True Alarm"
        case .down: return "False Alarm"
        }
    }
}

/// Delivery status tracked by the Notification Service.
public enum AlertStatus: String, Codable, Sendable {
    case sent
    case deduped
    case failed
}

/// Original alert request embedded inside an `AlertRecord`.
public struct AlertRequestPayload: Codable, Equatable, Sendable {
    public let topicId: String
    public let severity: AlertSeverity
    public let title: String
    public let body: String
    public let clipRef: String?
    public let dedupeKey: String?
    public let cooldownSec: Double?
    public let requiresAck: Bool
    public let bundleId: String?
    public let cameraIds: [String]
    public let source: String
    public let rubricVersion: String?
    public let toolVersions: [String: String]
    public let speaker: Bool

    enum CodingKeys: String, CodingKey {
        case topicId = "topic_id"
        case severity
        case title
        case body
        case clipRef = "clip_ref"
        case dedupeKey = "dedupe_key"
        case cooldownSec = "cooldown_sec"
        case requiresAck = "requires_ack"
        case bundleId = "bundle_id"
        case cameraIds = "camera_ids"
        case source
        case rubricVersion = "rubric_version"
        case toolVersions = "tool_versions"
        case speaker
    }

    public init(
        topicId: String,
        severity: AlertSeverity,
        title: String,
        body: String = "",
        clipRef: String? = nil,
        dedupeKey: String? = nil,
        cooldownSec: Double? = nil,
        requiresAck: Bool = false,
        bundleId: String? = nil,
        cameraIds: [String] = [],
        source: String = "main_agent",
        rubricVersion: String? = nil,
        toolVersions: [String: String] = [:],
        speaker: Bool = false
    ) {
        self.topicId = topicId
        self.severity = severity
        self.title = title
        self.body = body
        self.clipRef = clipRef
        self.dedupeKey = dedupeKey
        self.cooldownSec = cooldownSec
        self.requiresAck = requiresAck
        self.bundleId = bundleId
        self.cameraIds = cameraIds
        self.source = source
        self.rubricVersion = rubricVersion
        self.toolVersions = toolVersions
        self.speaker = speaker
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        topicId = try c.decode(String.self, forKey: .topicId)
        severity = try c.decodeIfPresent(AlertSeverity.self, forKey: .severity) ?? .info
        title = try c.decode(String.self, forKey: .title)
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        clipRef = try c.decodeIfPresent(String.self, forKey: .clipRef)
        dedupeKey = try c.decodeIfPresent(String.self, forKey: .dedupeKey)
        cooldownSec = try c.decodeIfPresent(Double.self, forKey: .cooldownSec)
        requiresAck = try c.decodeIfPresent(Bool.self, forKey: .requiresAck) ?? false
        bundleId = try c.decodeIfPresent(String.self, forKey: .bundleId)
        cameraIds = try c.decodeIfPresent([String].self, forKey: .cameraIds) ?? []
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "main_agent"
        rubricVersion = try c.decodeIfPresent(String.self, forKey: .rubricVersion)
        toolVersions = try c.decodeIfPresent([String: String].self, forKey: .toolVersions) ?? [:]
        speaker = try c.decodeIfPresent(Bool.self, forKey: .speaker) ?? false
    }
}

/// Human feedback attached to an alert.
public struct FeedbackItem: Codable, Equatable, Identifiable, Sendable {
    public let alertId: String
    public let rating: FeedbackRating
    public let note: String
    public let by: String
    public let at: Date

    public var id: String {
        "\(alertId)-\(rating.rawValue)-\(at.timeIntervalSince1970)-\(by)"
    }

    enum CodingKeys: String, CodingKey {
        case alertId = "alert_id"
        case rating
        case note
        case by
        case at
    }

    public init(alertId: String, rating: FeedbackRating, note: String = "", by: String = "", at: Date = Date()) {
        self.alertId = alertId
        self.rating = rating
        self.note = note
        self.by = by
        self.at = at
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        alertId = try c.decode(String.self, forKey: .alertId)
        rating = try c.decode(FeedbackRating.self, forKey: .rating)
        note = try c.decodeIfPresent(String.self, forKey: .note) ?? ""
        by = try c.decodeIfPresent(String.self, forKey: .by) ?? ""
        at = try c.decode(Date.self, forKey: .at)
    }
}

/// Full alert record returned by `GET /v1/alerts`, `GET /v1/alerts/{id}`, and `POST /v1/alerts/{id}/ack`.
public struct AlertRecord: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let request: AlertRequestPayload
    public let createdAt: Date
    public let status: AlertStatus
    public let deliveries: [String: String]
    public let ackedAt: Date?
    public let ackedBy: String?
    public let feedback: [FeedbackItem]
    public let hasThumbnail: Bool
    public let hasClip: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case request
        case createdAt = "created_at"
        case status
        case deliveries
        case ackedAt = "acked_at"
        case ackedBy = "acked_by"
        case feedback
        case hasThumbnail = "has_thumbnail"
        case hasClip = "has_clip"
    }

    public init(
        id: String,
        request: AlertRequestPayload,
        createdAt: Date,
        status: AlertStatus = .sent,
        deliveries: [String: String] = [:],
        ackedAt: Date? = nil,
        ackedBy: String? = nil,
        feedback: [FeedbackItem] = [],
        hasThumbnail: Bool? = nil,
        hasClip: Bool? = nil
    ) {
        self.id = id
        self.request = request
        self.createdAt = createdAt
        self.status = status
        self.deliveries = deliveries
        self.ackedAt = ackedAt
        self.ackedBy = ackedBy
        self.feedback = feedback
        let inferredMedia = request.bundleId != nil || request.clipRef != nil
        self.hasThumbnail = hasThumbnail ?? inferredMedia
        self.hasClip = hasClip ?? inferredMedia
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        request = try c.decode(AlertRequestPayload.self, forKey: .request)
        createdAt = try c.decode(Date.self, forKey: .createdAt)
        status = try c.decodeIfPresent(AlertStatus.self, forKey: .status) ?? .sent
        deliveries = try c.decodeIfPresent([String: String].self, forKey: .deliveries) ?? [:]
        ackedAt = try c.decodeIfPresent(Date.self, forKey: .ackedAt)
        ackedBy = try c.decodeIfPresent(String.self, forKey: .ackedBy)
        feedback = try c.decodeIfPresent([FeedbackItem].self, forKey: .feedback) ?? []
        let inferredMedia = request.bundleId != nil || request.clipRef != nil
        hasThumbnail = try c.decodeIfPresent(Bool.self, forKey: .hasThumbnail) ?? inferredMedia
        hasClip = try c.decodeIfPresent(Bool.self, forKey: .hasClip) ?? inferredMedia
    }

    public var title: String { request.title }
    public var body: String { request.body }
    public var severity: AlertSeverity { request.severity }
    public var topicId: String { request.topicId }
    public var isAcknowledged: Bool { ackedAt != nil }
    public var latestFeedback: FeedbackItem? { feedback.last }
}

/// Response from `POST /v1/alerts/{id}/feedback`.
public struct FeedbackResponse: Codable, Equatable, Sendable {
    public let feedback: FeedbackItem
    public let pinned: Bool
    public let alert: AlertRecord
}

/// Response from `POST /v1/devices/register`.
public struct DeviceRegistrationResponse: Codable, Equatable, Sendable {
    public let id: String
    public let platform: String
    public let name: String
    public let registeredAt: Date

    enum CodingKeys: String, CodingKey {
        case id
        case platform
        case name
        case registeredAt = "registered_at"
    }
}

/// Response from `GET /healthz`.
public struct HealthResponse: Codable, Equatable, Sendable {
    public let ok: Bool
    public let sinks: [String]
}

/// Combined result of a reachability + bearer-token verification check.
public struct ConnectionTestResult: Equatable, Sendable {
    public let sinks: [String]
    public let alertCountChecked: Int

    public init(sinks: [String], alertCountChecked: Int) {
        self.sinks = sinks
        self.alertCountChecked = alertCountChecked
    }
}

/// Persisted connection configuration (URL + token + optional display name).
public struct SentinelServerConfig: Equatable, Sendable {
    public var baseURLString: String
    public var householdToken: String
    public var deviceName: String

    public init(
        baseURLString: String = "",
        householdToken: String = "",
        deviceName: String = "iOS Device"
    ) {
        self.baseURLString = baseURLString
        self.householdToken = householdToken
        self.deviceName = deviceName
    }

    /// Normalizes and validates the base URL (strips whitespace and trailing slashes).
    public var normalizedBaseURL: URL? {
        let trimmed = baseURLString
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty,
              let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host != nil else {
            return nil
        }
        return url
    }

    public var isConfigured: Bool {
        normalizedBaseURL != nil && !householdToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// Constants shared between APNs payload registration and notification action handling.
public enum NotificationConstants {
    public static let alertCategoryIdentifier = "VISION_ALERT"
    public static let actionAcknowledge = "ACTION_ACK"
    public static let actionFeedbackUp = "ACTION_FEEDBACK_UP"
    public static let actionFeedbackDown = "ACTION_FEEDBACK_DOWN"
    public static let defaultTapAction = "com.apple.UNNotificationDefaultActionIdentifier"
}

/// Structured view of an incoming APNs `userInfo` dictionary.
public struct PushNotificationPayload: Equatable, Sendable {
    public let alertId: String
    public let topicId: String?
    public let severity: AlertSeverity
    public let title: String?
    public let body: String?
    public let bundleId: String?
    public let requiresAck: Bool

    public init(
        alertId: String,
        topicId: String? = nil,
        severity: AlertSeverity = .info,
        title: String? = nil,
        body: String? = nil,
        bundleId: String? = nil,
        requiresAck: Bool = false
    ) {
        self.alertId = alertId
        self.topicId = topicId
        self.severity = severity
        self.title = title
        self.body = body
        self.bundleId = bundleId
        self.requiresAck = requiresAck
    }

    public static func parse(userInfo: [AnyHashable: Any]) -> PushNotificationPayload? {
        guard let alertId = userInfo["alert_id"] as? String, !alertId.isEmpty else {
            return nil
        }
        let topicId = userInfo["topic_id"] as? String
        let severityRaw = (userInfo["severity"] as? String)?.lowercased() ?? "info"
        let severity = AlertSeverity(rawValue: severityRaw) ?? .info
        let bundleId = userInfo["bundle_id"] as? String

        let requiresAck: Bool
        if let boolVal = userInfo["requires_ack"] as? Bool {
            requiresAck = boolVal
        } else if let strVal = userInfo["requires_ack"] as? String {
            requiresAck = strVal.lowercased() == "true" || strVal == "1"
        } else {
            requiresAck = false
        }

        let aps = userInfo["aps"] as? [AnyHashable: Any]
        let apsAlert = aps?["alert"] as? [AnyHashable: Any]
        let title = (userInfo["title"] as? String) ?? (apsAlert?["title"] as? String)
        let body = (userInfo["body"] as? String) ?? (apsAlert?["body"] as? String)

        return PushNotificationPayload(
            alertId: alertId,
            topicId: topicId,
            severity: severity,
            title: title,
            body: body,
            bundleId: bundleId,
            requiresAck: requiresAck
        )
    }
}

/// Formats raw APNs device token bytes as lowercase hex for `POST /v1/devices/register`.
public func hexEncodedDeviceToken(_ data: Data) -> String {
    data.map { String(format: "%02x", $0) }.joined()
}
