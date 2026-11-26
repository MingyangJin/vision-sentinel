import Foundation

public enum VideoQualityPreset: String, Codable, CaseIterable, Identifiable, Sendable {
    case hd720 = "720p"
    case sd480 = "480p"

    public var id: String { rawValue }

    public var width: Int {
        switch self {
        case .hd720: return 1280
        case .sd480: return 854
        }
    }

    public var height: Int {
        switch self {
        case .hd720: return 720
        case .sd480: return 480
        }
    }

    public var videoBitrate: Int {
        switch self {
        case .hd720: return 1_800_000
        case .sd480: return 900_000
        }
    }
}

public struct CameraSettings: Codable, Equatable, Sendable {
    public var serverURL: String
    public var cameraID: String
    public var bearerToken: String
    public var videoDeviceID: String
    public var audioDeviceID: String
    public var includeAudio: Bool
    public var segmentSeconds: Double
    public var quality: VideoQualityPreset

    public init(
        serverURL: String = "http://127.0.0.1:8560",
        cameraID: String = "mac_cam",
        bearerToken: String = "",
        videoDeviceID: String = "",
        audioDeviceID: String = "",
        includeAudio: Bool = true,
        segmentSeconds: Double = 2.0,
        quality: VideoQualityPreset = .hd720
    ) {
        self.serverURL = serverURL
        self.cameraID = cameraID
        self.bearerToken = bearerToken
        self.videoDeviceID = videoDeviceID
        self.audioDeviceID = audioDeviceID
        self.includeAudio = includeAudio
        self.segmentSeconds = segmentSeconds
        self.quality = quality
    }

    public var normalizedBaseURL: URL? {
        let trimmed = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty else { return nil }
        return URL(string: trimmed)
    }

    public var sanitizedCameraID: String {
        cameraID.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func load(from defaults: UserDefaults = .standard, key: String = "vs.macos_camera.settings") -> CameraSettings {
        guard let data = defaults.data(forKey: key),
              let decoded = try? JSONDecoder().decode(CameraSettings.self, from: data)
        else {
            return CameraSettings()
        }
        return decoded
    }

    public func save(to defaults: UserDefaults = .standard, key: String = "vs.macos_camera.settings") {
        if let data = try? JSONEncoder().encode(self) {
            defaults.set(data, forKey: key)
        }
    }
}
