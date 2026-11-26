import Foundation

/// Minimal HTTP transport abstraction so `SentinelAPIClient` can be tested offline.
public protocol HTTPTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, URLResponse)
}

extension URLSession: HTTPTransport {}

/// Typed errors returned by `SentinelAPIClient`. Never includes credentials or tokens.
public enum SentinelAPIError: Error, LocalizedError, Equatable, Sendable {
    case invalidBaseURL
    case missingToken
    case unauthorized
    case notFound
    case httpError(statusCode: Int, detail: String)
    case networkError(String)
    case decodingError(String)

    public var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "Invalid server URL. Enter a full http:// or https:// address."
        case .missingToken:
            return "Household bearer token is required."
        case .unauthorized:
            return "Unauthorized (401). Check your shared household token."
        case .notFound:
            return "The requested alert or media resource was not found (404)."
        case .httpError(let code, let detail):
            return detail.isEmpty ? "Server error (HTTP \(code))." : "HTTP \(code): \(detail)"
        case .networkError(let message):
            return "Network error: \(message)"
        case .decodingError(let message):
            return "Invalid server response: \(message)"
        }
    }
}

/// Async client for the self-hosted Vision Sentinel Notification Service (`vs-notify`).
public final class SentinelAPIClient: Sendable {
    public let baseURL: URL
    private let token: String
    private let transport: HTTPTransport
    private let timeoutInterval: TimeInterval

    public init(
        baseURL: URL,
        householdToken: String,
        transport: HTTPTransport = URLSession.shared,
        timeoutInterval: TimeInterval = 20
    ) throws {
        guard let scheme = baseURL.scheme?.lowercased(),
              (scheme == "http" || scheme == "https"),
              baseURL.host != nil else {
            throw SentinelAPIError.invalidBaseURL
        }
        let trimmedToken = householdToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedToken.isEmpty else {
            throw SentinelAPIError.missingToken
        }
        let cleaned = baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let normalized = URL(string: cleaned) else {
            throw SentinelAPIError.invalidBaseURL
        }
        self.baseURL = normalized
        self.token = trimmedToken
        self.transport = transport
        self.timeoutInterval = timeoutInterval
    }

    public convenience init(
        config: SentinelServerConfig,
        transport: HTTPTransport = URLSession.shared,
        timeoutInterval: TimeInterval = 20
    ) throws {
        guard let url = config.normalizedBaseURL else {
            throw SentinelAPIError.invalidBaseURL
        }
        try self.init(
            baseURL: url,
            householdToken: config.householdToken,
            transport: transport,
            timeoutInterval: timeoutInterval
        )
    }

    // MARK: - URLs

    public func thumbnailURL(alertId: String) -> URL {
        endpointURL(path: "/v1/alerts/\(encodedPathSegment(alertId))/thumbnail")
    }

    public func clipURL(alertId: String) -> URL {
        endpointURL(path: "/v1/alerts/\(encodedPathSegment(alertId))/clip")
    }

    /// Builds a `URLRequest` bearing `Authorization: Bearer <household_token>`.
    public func authorizedRequest(for url: URL, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeoutInterval
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    // MARK: - Endpoints

    /// `GET /healthz` (open liveness route; no bearer header required).
    public func checkHealth() async throws -> HealthResponse {
        var req = URLRequest(url: endpointURL(path: "/healthz"))
        req.httpMethod = "GET"
        req.timeoutInterval = timeoutInterval
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let data = try await send(req)
        return try decode(HealthResponse.self, from: data)
    }

    /// Verifies both reachability (`GET /healthz`) and household bearer authentication (`GET /v1/alerts?limit=1`).
    public func testConnection() async throws -> ConnectionTestResult {
        let health = try await checkHealth()
        let alerts = try await listAlerts(limit: 1)
        return ConnectionTestResult(sinks: health.sinks, alertCountChecked: alerts.count)
    }

    /// `POST /v1/devices/register`
    public func registerDevice(
        pushToken: String,
        deviceName: String,
        platform: String = "ios"
    ) async throws -> DeviceRegistrationResponse {
        struct Body: Encodable {
            let platform: String
            let token: String
            let name: String
        }
        let body = Body(platform: platform, token: pushToken, name: deviceName)
        let req = try authorizedJSONRequest(path: "/v1/devices/register", method: "POST", body: body)
        let data = try await send(req)
        return try decode(DeviceRegistrationResponse.self, from: data)
    }

    /// `GET /v1/alerts`
    public func listAlerts(
        topicId: String? = nil,
        severity: AlertSeverity? = nil,
        unacked: Bool? = nil,
        limit: Int = 50
    ) async throws -> [AlertRecord] {
        var queryItems: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(max(1, min(limit, 500))))]
        if let topicId, !topicId.isEmpty {
            queryItems.append(URLQueryItem(name: "topic_id", value: topicId))
        }
        if let severity {
            queryItems.append(URLQueryItem(name: "severity", value: severity.rawValue))
        }
        if let unacked {
            queryItems.append(URLQueryItem(name: "unacked", value: unacked ? "true" : "false"))
        }
        let url = endpointURL(path: "/v1/alerts", queryItems: queryItems)
        var req = authorizedRequest(for: url, method: "GET")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let data = try await send(req)
        return try decode([AlertRecord].self, from: data)
    }

    /// `GET /v1/alerts/{id}`
    public func getAlert(id: String) async throws -> AlertRecord {
        let url = endpointURL(path: "/v1/alerts/\(encodedPathSegment(id))")
        var req = authorizedRequest(for: url, method: "GET")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let data = try await send(req)
        return try decode(AlertRecord.self, from: data)
    }

    /// `POST /v1/alerts/{id}/ack`
    public func acknowledgeAlert(id: String, by: String? = nil) async throws -> AlertRecord {
        struct Body: Encodable {
            let by: String?
        }
        let req = try authorizedJSONRequest(
            path: "/v1/alerts/\(encodedPathSegment(id))/ack",
            method: "POST",
            body: Body(by: by)
        )
        let data = try await send(req)
        return try decode(AlertRecord.self, from: data)
    }

    /// `POST /v1/alerts/{id}/feedback`
    public func submitFeedback(
        id: String,
        rating: FeedbackRating,
        note: String = "",
        by: String = ""
    ) async throws -> FeedbackResponse {
        struct Body: Encodable {
            let rating: FeedbackRating
            let note: String
            let by: String
        }
        let req = try authorizedJSONRequest(
            path: "/v1/alerts/\(encodedPathSegment(id))/feedback",
            method: "POST",
            body: Body(rating: rating, note: note, by: by)
        )
        let data = try await send(req)
        return try decode(FeedbackResponse.self, from: data)
    }

    /// `GET /v1/alerts/{id}/thumbnail`
    public func fetchThumbnail(alertId: String) async throws -> Data {
        var req = authorizedRequest(for: thumbnailURL(alertId: alertId), method: "GET")
        req.setValue("image/*", forHTTPHeaderField: "Accept")
        return try await send(req)
    }

    /// `GET /v1/alerts/{id}/clip`
    public func fetchClip(alertId: String) async throws -> Data {
        var req = authorizedRequest(for: clipURL(alertId: alertId), method: "GET")
        req.setValue("video/mp4, */*", forHTTPHeaderField: "Accept")
        return try await send(req)
    }

    // MARK: - Internals

    private func endpointURL(path: String, queryItems: [URLQueryItem] = []) -> URL {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let suffix = path.hasPrefix("/") ? path : "/\(path)"
        components.path = basePath.isEmpty ? suffix : "/\(basePath)\(suffix)"
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        return components.url ?? baseURL
    }

    private func encodedPathSegment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? value
    }

    private func authorizedJSONRequest<T: Encodable>(
        path: String,
        method: String,
        body: T
    ) throws -> URLRequest {
        var req = authorizedRequest(for: endpointURL(path: path), method: method)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.httpBody = try JSONEncoder().encode(body)
        return req
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await transport.data(for: request)
        } catch {
            throw SentinelAPIError.networkError(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw SentinelAPIError.networkError("Unexpected non-HTTP response.")
        }
        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            throw SentinelAPIError.unauthorized
        case 404:
            throw SentinelAPIError.notFound
        default:
            let detail = extractDetail(from: data)
            throw SentinelAPIError.httpError(statusCode: http.statusCode, detail: detail)
        }
    }

    private func extractDetail(from data: Data) -> String {
        struct ErrorBody: Decodable { let detail: String? }
        if let parsed = try? JSONDecoder().decode(ErrorBody.self, from: data), let detail = parsed.detail {
            return detail
        }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try SentinelJSONDecoder.make().decode(type, from: data)
        } catch {
            throw SentinelAPIError.decodingError(error.localizedDescription)
        }
    }
}

/// Shared JSONDecoder supporting Python `datetime.isoformat()` with or without fractional seconds.
public enum SentinelJSONDecoder {
    public static func make() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            if let date = parseISO8601(string) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Invalid ISO-8601 date: \(string)"
            )
        }
        return decoder
    }

    public static func parseISO8601(_ string: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: string) {
            return date
        }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: string)
    }
}
