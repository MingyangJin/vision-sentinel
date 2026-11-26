import Foundation

public enum SegmentKind: Sendable {
    case initialization
    case separable
}

public struct ReadySegment: Equatable, Sendable {
    public let sequence: Int
    public let data: Data
    public let startedAt: Date

    public init(sequence: Int, data: Data, startedAt: Date) {
        self.sequence = sequence
        self.data = data
        self.startedAt = startedAt
    }
}

/// Combines AVAssetWriter fragmented MP4 callbacks (`.initialization` + `.separable`)
/// into standalone, self-contained `.mp4` segments (`initData + mediaData`) ready to upload
/// to Vision Sentinel's `http_ingest` / `macos_camera` adapter.
public final class SegmentAssembler {
    private var initSegment: Data?
    private var sequence: Int = 0
    public private(set) var totalBytesEmitted: Int64 = 0

    public init() {}

    public var hasInitializationSegment: Bool {
        initSegment != nil
    }

    public func reset() {
        initSegment = nil
        sequence = 0
        totalBytesEmitted = 0
    }

    public func append(segmentData: Data, kind: SegmentKind, startedAt: Date = Date()) -> ReadySegment? {
        guard !segmentData.isEmpty else { return nil }
        switch kind {
        case .initialization:
            initSegment = segmentData
            return nil
        case .separable:
            guard let header = initSegment else { return nil }
            var combined = Data(capacity: header.count + segmentData.count)
            combined.append(header)
            combined.append(segmentData)
            sequence += 1
            totalBytesEmitted += Int64(combined.count)
            return ReadySegment(sequence: sequence, data: combined, startedAt: startedAt)
        }
    }
}
