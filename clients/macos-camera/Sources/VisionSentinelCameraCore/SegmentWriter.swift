import AVFoundation
import AudioToolbox
import CoreMedia
import Foundation
import UniformTypeIdentifiers

/// Encodes real-time video (and optional audio) CMSampleBuffers into self-contained
/// CMAF fragmented MP4 segments using Apple's native AVAssetWriter.
///
/// Key macOS invariants:
/// - Initialized lazily on the FIRST video frame so `initialSegmentStartTime` matches
///   the real Mach-uptime capture PTS.
/// - Drops any audio buffers with PTS earlier than `firstPTS` (CMAF rejects samples
///   before `initialSegmentStartTime`).
/// - Uses explicit `AVChannelLayoutKey` + matched sample rate (or Apple's recommended
///   audio settings from `AVCaptureAudioDataOutput`) so MacBook / AirPods / USB mics
///   never fail AAC initialization.
public final class FMP4SegmentWriter: NSObject, AVAssetWriterDelegate, @unchecked Sendable {
    private let quality: VideoQualityPreset
    private let includeAudio: Bool
    private let segmentSeconds: Double
    private let recommendedAudioSettings: [String: Any]?
    private let onSegment: (ReadySegment) -> Void
    private let onError: (String) -> Void

    private let assembler = SegmentAssembler()
    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var audioInput: AVAssetWriterInput?
    private var started: Bool = false
    private var startPTS: CMTime = .invalid
    private var active: Bool = false
    private var segmentStartWall: Date = Date()

    public init(
        quality: VideoQualityPreset,
        includeAudio: Bool,
        segmentSeconds: Double = 2.0,
        recommendedAudioSettings: [String: Any]? = nil,
        onSegment: @escaping (ReadySegment) -> Void,
        onError: @escaping (String) -> Void
    ) {
        self.quality = quality
        self.includeAudio = includeAudio
        self.segmentSeconds = max(1.0, segmentSeconds)
        self.recommendedAudioSettings = recommendedAudioSettings
        self.onSegment = onSegment
        self.onError = onError
        super.init()
    }

    public func begin() {
        stop()
        assembler.reset()
        active = true
    }

    public func stop() {
        active = false
        started = false
        startPTS = .invalid
        if let aw = writer {
            videoInput?.markAsFinished()
            audioInput?.markAsFinished()
            if aw.status == .writing {
                let sem = DispatchSemaphore(value: 0)
                aw.finishWriting { sem.signal() }
                _ = sem.wait(timeout: .now() + 2.0)
            }
        }
        writer = nil
        videoInput = nil
        audioInput = nil
    }

    public func appendVideo(_ sampleBuffer: CMSampleBuffer) {
        guard active else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        guard pts.isValid else { return }

        if !started {
            guard startWriter(firstPTS: pts, videoBuffer: sampleBuffer) else { return }
        }
        guard let aw = writer, let vIn = videoInput else { return }
        if aw.status == .failed {
            fail(describe(aw.error, fallback: "AVAssetWriter failed during video"))
            return
        }
        if vIn.isReadyForMoreMediaData {
            if !vIn.append(sampleBuffer), aw.status == .failed {
                fail(describe(aw.error, fallback: "Failed to append video sample"))
            }
        }
    }

    public func appendAudio(_ sampleBuffer: CMSampleBuffer) {
        guard active, started, includeAudio else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        guard pts.isValid, startPTS.isValid, CMTimeCompare(pts, startPTS) >= 0 else { return }
        guard let aw = writer, let aIn = audioInput else { return }
        if aw.status == .failed {
            fail(describe(aw.error, fallback: "AVAssetWriter failed during audio"))
            return
        }
        if aIn.isReadyForMoreMediaData {
            if !aIn.append(sampleBuffer), aw.status == .failed {
                fail(describe(aw.error, fallback: "Failed to append audio sample"))
            }
        }
    }

    private func buildAudioSettings() -> [String: Any] {
        if let rec = recommendedAudioSettings, !rec.isEmpty {
            return rec
        }
        var layout = AudioChannelLayout()
        layout.mChannelLayoutTag = kAudioChannelLayoutTag_Mono
        let layoutData = Data(bytes: &layout, count: MemoryLayout<AudioChannelLayout>.size)
        return [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVNumberOfChannelsKey: 1,
            AVSampleRateKey: 48_000,
            AVEncoderBitRateKey: 64_000,
            AVChannelLayoutKey: layoutData,
        ]
    }

    private func startWriter(firstPTS: CMTime, videoBuffer: CMSampleBuffer) -> Bool {
        var width = quality.width
        var height = quality.height
        if let fmt = CMSampleBufferGetFormatDescription(videoBuffer) {
            let dims = CMVideoFormatDescriptionGetDimensions(fmt)
            if dims.width > 0 && dims.height > 0 {
                width = Int(dims.width)
                height = Int(dims.height)
            }
        }

        let aw = AVAssetWriter(contentType: UTType.mpeg4Movie)
        // .mpeg4AppleHLS supports muxed H.264 video + AAC audio in fragmented MP4 segments
        // (.mpeg4CMAFCompliant rejects multi-track video+audio with AVFoundationErrorDomain -11875).
        aw.outputFileTypeProfile = .mpeg4AppleHLS
        aw.preferredOutputSegmentInterval = CMTime(seconds: segmentSeconds, preferredTimescale: 600)
        aw.initialSegmentStartTime = firstPTS
        aw.delegate = self

        let vSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: quality.videoBitrate,
                AVVideoMaxKeyFrameIntervalDurationKey: segmentSeconds,
                AVVideoAllowFrameReorderingKey: false,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264MainAutoLevel,
            ],
        ]
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: vSettings)
        vIn.expectsMediaDataInRealTime = true
        guard aw.canAdd(vIn) else {
            fail("AVAssetWriter rejected video input (\(width)x\(height))")
            return false
        }
        aw.add(vIn)

        var aIn: AVAssetWriterInput?
        if includeAudio {
            let aSettings = buildAudioSettings()
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: aSettings)
            input.expectsMediaDataInRealTime = true
            if aw.canAdd(input) {
                aw.add(input)
                aIn = input
            }
        }

        guard aw.startWriting() else {
            fail(describe(aw.error, fallback: "AVAssetWriter.startWriting() failed"))
            return false
        }
        aw.startSession(atSourceTime: firstPTS)

        self.writer = aw
        self.videoInput = vIn
        self.audioInput = aIn
        self.startPTS = firstPTS
        self.started = true
        self.segmentStartWall = Date()
        return true
    }

    private func describe(_ error: Error?, fallback: String) -> String {
        guard let err = error as NSError? else { return fallback }
        let reason = err.localizedFailureReason.map { " (\($0))" } ?? ""
        return "\(err.localizedDescription)\(reason) [\(err.domain):\(err.code)]"
    }

    private func fail(_ message: String) {
        active = false
        started = false
        startPTS = .invalid
        writer = nil
        videoInput = nil
        audioInput = nil
        onError(message)
    }

    public func assetWriter(
        _ writer: AVAssetWriter,
        didOutputSegmentData segmentData: Data,
        segmentType: AVAssetSegmentType,
        segmentReport: AVAssetSegmentReport?
    ) {
        let kind: SegmentKind = (segmentType == .initialization) ? .initialization : .separable
        let now = Date()
        let startedAt = segmentStartWall
        if kind == .separable {
            segmentStartWall = now
        }
        if let ready = assembler.append(segmentData: segmentData, kind: kind, startedAt: startedAt) {
            onSegment(ready)
        }
    }
}
