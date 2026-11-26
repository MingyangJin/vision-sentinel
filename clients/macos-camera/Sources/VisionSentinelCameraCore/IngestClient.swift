import Foundation

public enum IngestError: Error, LocalizedError, Equatable {
    case invalidConfiguration(String)
    case httpStatus(Int, String)
    case transport(String)

    public var errorDescription: String? {
        switch self {
        case .invalidConfiguration(let msg): return "Configuration error: \(msg)"
        case .httpStatus(let code, let detail): return "Server returned HTTP \(code): \(detail)"
        case .transport(let msg): return "Network error: \(msg)"
        }
    }
}

public struct IngestUploadResult: Codable, Equatable, Sendable {
    public let ok: Bool
    public let camera_id: String
    public let segment: String
    public let bytes: Int
}

public struct IngestHealthResponse: Codable, Equatable, Sendable {
    public let ok: Bool
    public let cameras: [String: CameraHealthItem]
}

public struct CameraHealthItem: Codable, Equatable, Sendable {
    public let camera_id: String
    public let status: String
    public let detail: String?
}

public final class IngestClient: @unchecked Sendable {
    private let session: URLSession
    private let isoFormatter: ISO8601DateFormatter

    public init(session: URLSession = .shared) {
        self.session = session
        let fmt = ISO8601DateFormatter()
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        self.isoFormatter = fmt
    }

    public func healthURL(for settings: CameraSettings) throws -> URL {
        guard let base = settings.normalizedBaseURL else {
            throw IngestError.invalidConfiguration("Server URL is empty or invalid.")
        }
        return base.appendingPathComponent("v1/ingest/health")
    }

    public func segmentURL(for settings: CameraSettings) throws -> URL {
        guard let base = settings.normalizedBaseURL else {
            throw IngestError.invalidConfiguration("Server URL is empty or invalid.")
        }
        let camID = settings.sanitizedCameraID
        guard !camID.isEmpty else {
            throw IngestError.invalidConfiguration("Camera ID is required.")
        }
        return base
            .appendingPathComponent("v1/ingest")
            .appendingPathComponent(camID)
            .appendingPathComponent("segment")
    }

    public func testConnection(settings: CameraSettings) async throws -> IngestHealthResponse {
        let url = try healthURL(for: settings)
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.timeoutInterval = 5.0
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw IngestError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw IngestError.transport("Unexpected non-HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? ""
            throw IngestError.httpStatus(http.statusCode, msg)
        }
        do {
            return try JSONDecoder().decode(IngestHealthResponse.self, from: data)
        } catch {
            throw IngestError.transport("Invalid health JSON: \(error.localizedDescription)")
        }
    }

    public func uploadSegment(_ segment: ReadySegment, settings: CameraSettings) async throws -> IngestUploadResult {
        let url = try segmentURL(for: settings)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 10.0
        req.setValue("video/mp4", forHTTPHeaderField: "Content-Type")
        req.setValue(isoFormatter.string(from: segment.startedAt), forHTTPHeaderField: "X-Segment-Start")
        let token = settings.bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        req.httpBody = segment.data

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw IngestError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw IngestError.transport("Unexpected non-HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? ""
            throw IngestError.httpStatus(http.statusCode, msg)
        }
        do {
            return try JSONDecoder().decode(IngestUploadResult.self, from: data)
        } catch {
            throw IngestError.transport("Invalid upload JSON: \(error.localizedDescription)")
        }
    }
}
