import CoreMedia
import CoreVideo
import Foundation
import XCTest
@testable import VisionSentinelCameraCore

final class MockURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (resp, data) = try handler(request)
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

final class CameraCoreTests: XCTestCase {
    func testSegmentAssemblerCombinesInitAndMediaSegments() {
        let assembler = SegmentAssembler()
        let initBytes = Data([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70]) // ftyp
        let seg1 = Data([0x00, 0x00, 0x00, 0x10, 0x6D, 0x6F, 0x6F, 0x66])      // moof 1
        let seg2 = Data([0x00, 0x00, 0x00, 0x10, 0x6D, 0x6F, 0x6F, 0x66, 0x01])

        // Media before init is ignored
        XCTAssertNil(assembler.append(segmentData: seg1, kind: .separable))
        XCTAssertFalse(assembler.hasInitializationSegment)

        // Init segment cached
        XCTAssertNil(assembler.append(segmentData: initBytes, kind: .initialization))
        XCTAssertTrue(assembler.hasInitializationSegment)

        let ready1 = assembler.append(segmentData: seg1, kind: .separable)
        XCTAssertEqual(ready1?.sequence, 1)
        XCTAssertEqual(ready1?.data, initBytes + seg1)

        let ready2 = assembler.append(segmentData: seg2, kind: .separable)
        XCTAssertEqual(ready2?.sequence, 2)
        XCTAssertEqual(ready2?.data, initBytes + seg2)
        XCTAssertEqual(assembler.totalBytesEmitted, Int64((initBytes + seg1).count + (initBytes + seg2).count))
    }

    func testSettingsRoundTripAndURLNormalization() {
        let suite = UserDefaults(suiteName: "org.visionsentinel.camera.tests.\(UUID().uuidString)")!
        let original = CameraSettings(
            serverURL: "  http://127.0.0.1:8560/  ",
            cameraID: " desk_mac ",
            bearerToken: "tok-123",
            includeAudio: true,
            segmentSeconds: 2.0,
            quality: .sd480
        )
        original.save(to: suite)
        let loaded = CameraSettings.load(from: suite)
        XCTAssertEqual(loaded, original)
        XCTAssertEqual(loaded.normalizedBaseURL?.absoluteString, "http://127.0.0.1:8560")
        XCTAssertEqual(loaded.sanitizedCameraID, "desk_mac")
    }

    func testIngestClientUploadsWithBearerAndTestsHealth() async throws {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [MockURLProtocol.self]
        let session = URLSession(configuration: cfg)
        let client = IngestClient(session: session)
        let settings = CameraSettings(serverURL: "http://127.0.0.1:8560", cameraID: "mac_cam", bearerToken: "secret-tok")

        MockURLProtocol.handler = { req in
            if req.httpMethod == "GET" {
                XCTAssertEqual(req.url?.path, "/v1/ingest/health")
                let resp = HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                let body = #"{"ok":true,"cameras":{"mac_cam":{"camera_id":"mac_cam","status":"degraded","detail":"waiting"}}}"#.data(using: .utf8)!
                return (resp, body)
            }
            XCTAssertEqual(req.httpMethod, "POST")
            XCTAssertEqual(req.url?.path, "/v1/ingest/mac_cam/segment")
            XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer secret-tok")
            XCTAssertEqual(req.value(forHTTPHeaderField: "Content-Type"), "video/mp4")
            XCTAssertNotNil(req.value(forHTTPHeaderField: "X-Segment-Start"))
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let body = #"{"ok":true,"camera_id":"mac_cam","segment":"2026-11-25_18-00-00-000000.mp4","bytes":16}"#.data(using: .utf8)!
            return (resp, body)
        }

        let health = try await client.testConnection(settings: settings)
        XCTAssertTrue(health.ok)
        XCTAssertEqual(health.cameras["mac_cam"]?.status, "degraded")

        let seg = ReadySegment(sequence: 1, data: Data(repeating: 0x41, count: 16), startedAt: Date())
        let uploaded = try await client.uploadSegment(seg, settings: settings)
        XCTAssertTrue(uploaded.ok)
        XCTAssertEqual(uploaded.camera_id, "mac_cam")
        XCTAssertEqual(uploaded.bytes, 16)
    }

    func testFMP4SegmentWriterProducesPlayableMP4WithMachLikePTS() throws {
        var emitted: [ReadySegment] = []
        var writerError: String?
        let lock = NSLock()

        let writer = FMP4SegmentWriter(
            quality: .sd480,
            includeAudio: false,
            segmentSeconds: 1.0,
            onSegment: { seg in
                lock.lock()
                emitted.append(seg)
                lock.unlock()
            },
            onError: { err in
                lock.lock()
                writerError = err
                lock.unlock()
            }
        )
        writer.begin()

        // Feed 45 frames at 15 fps (~3 s) starting at a realistic Mach-uptime PTS (50000 s)
        let width = 320
        let height = 240
        for i in 0..<45 {
            var pixelBuffer: CVPixelBuffer?
            let status = CVPixelBufferCreate(
                kCFAllocatorDefault,
                width,
                height,
                kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                nil,
                &pixelBuffer
            )
            XCTAssertEqual(status, kCVReturnSuccess)
            let pb = try XCTUnwrap(pixelBuffer)

            var formatDesc: CMVideoFormatDescription?
            XCTAssertEqual(
                CMVideoFormatDescriptionCreateForImageBuffer(
                    allocator: kCFAllocatorDefault,
                    imageBuffer: pb,
                    formatDescriptionOut: &formatDesc
                ),
                noErr
            )
            let desc = try XCTUnwrap(formatDesc)

            let pts = CMTime(seconds: 50_000.0 + Double(i) / 15.0, preferredTimescale: 600)
            var timing = CMSampleTimingInfo(
                duration: CMTime(value: 40, timescale: 600),
                presentationTimeStamp: pts,
                decodeTimeStamp: .invalid
            )
            var sampleBuffer: CMSampleBuffer?
            XCTAssertEqual(
                CMSampleBufferCreateReadyWithImageBuffer(
                    allocator: kCFAllocatorDefault,
                    imageBuffer: pb,
                    formatDescription: desc,
                    sampleTiming: &timing,
                    sampleBufferOut: &sampleBuffer
                ),
                noErr
            )
            writer.appendVideo(try XCTUnwrap(sampleBuffer))
            usleep(15_000) // pace frames so real-time hardware encoder accepts all 45 frames
        }
        writer.stop()

        XCTAssertNil(writerError)
        XCTAssertGreaterThanOrEqual(emitted.count, 2, "Expected at least 2 fMP4 segments for 3s of frames")
        let first = try XCTUnwrap(emitted.first)
        XCTAssertGreaterThan(first.data.count, 500)
        // Verify ISO BMFF 'ftyp' box at bytes 4..8 (exact check performed by vs_streamhub HttpIngestAdapter)
        XCTAssertEqual(String(data: first.data[4..<8], encoding: .ascii), "ftyp")
    }

    func testFMP4SegmentWriterWithVideoAndAudioIncludingEarlyAudioPTS() throws {
        var emitted: [ReadySegment] = []
        var writerError: String?
        let lock = NSLock()

        let writer = FMP4SegmentWriter(
            quality: .sd480,
            includeAudio: true,
            segmentSeconds: 1.0,
            onSegment: { seg in
                lock.lock()
                emitted.append(seg)
                lock.unlock()
            },
            onError: { err in
                lock.lock()
                writerError = err
                lock.unlock()
            }
        )
        writer.begin()

        // Create 48 kHz mono Float32 PCM format description (matching MacBook Pro Microphone)
        var asbd = AudioStreamBasicDescription(
            mSampleRate: 48_000,
            mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
            mBytesPerPacket: 4,
            mFramesPerPacket: 1,
            mBytesPerFrame: 4,
            mChannelsPerFrame: 1,
            mBitsPerChannel: 32,
            mReserved: 0
        )
        var audioFmt: CMAudioFormatDescription?
        XCTAssertEqual(
            CMAudioFormatDescriptionCreate(
                allocator: kCFAllocatorDefault,
                asbd: &asbd,
                layoutSize: 0,
                layout: nil,
                magicCookieSize: 0,
                magicCookie: nil,
                extensions: nil,
                formatDescriptionOut: &audioFmt
            ),
            noErr
        )
        let aDesc = try XCTUnwrap(audioFmt)

        func makeAudioBuffer(ptsSeconds: Double, frames: Int = 1024) throws -> CMSampleBuffer {
            let byteCount = frames * 4
            var block: CMBlockBuffer?
            XCTAssertEqual(
                CMBlockBufferCreateWithMemoryBlock(
                    allocator: kCFAllocatorDefault,
                    memoryBlock: nil,
                    blockLength: byteCount,
                    blockAllocator: kCFAllocatorDefault,
                    customBlockSource: nil,
                    offsetToData: 0,
                    dataLength: byteCount,
                    flags: 0,
                    blockBufferOut: &block
                ),
                noErr
            )
            let b = try XCTUnwrap(block)
            XCTAssertEqual(CMBlockBufferFillDataBytes(with: 0, blockBuffer: b, offsetIntoDestination: 0, dataLength: byteCount), noErr)
            var sb: CMSampleBuffer?
            let pts = CMTime(seconds: ptsSeconds, preferredTimescale: 48_000)
            XCTAssertEqual(
                CMAudioSampleBufferCreateReadyWithPacketDescriptions(
                    allocator: kCFAllocatorDefault,
                    dataBuffer: b,
                    formatDescription: aDesc,
                    sampleCount: frames,
                    presentationTimeStamp: pts,
                    packetDescriptions: nil,
                    sampleBufferOut: &sb
                ),
                noErr
            )
            return try XCTUnwrap(sb)
        }

        let base = 60_000.0
        // Feed 45 video frames (3s @ 15fps) plus audio buffers (including one EARLY audio buffer before base!)
        var audioCursor = base - 0.02
        for i in 0..<45 {
            var pb: CVPixelBuffer?
            XCTAssertEqual(
                CVPixelBufferCreate(kCFAllocatorDefault, 320, 240, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange, nil, &pb),
                kCVReturnSuccess
            )
            var vDesc: CMVideoFormatDescription?
            XCTAssertEqual(CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: pb!, formatDescriptionOut: &vDesc), noErr)
            let vPts = CMTime(seconds: base + Double(i) / 15.0, preferredTimescale: 600)
            var timing = CMSampleTimingInfo(duration: CMTime(value: 40, timescale: 600), presentationTimeStamp: vPts, decodeTimeStamp: .invalid)
            var vSB: CMSampleBuffer?
            XCTAssertEqual(
                CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: pb!, formatDescription: vDesc!, sampleTiming: &timing, sampleBufferOut: &vSB),
                noErr
            )
            writer.appendVideo(vSB!)
            while audioCursor <= base + Double(i) / 15.0 {
                writer.appendAudio(try makeAudioBuffer(ptsSeconds: audioCursor))
                audioCursor += 1024.0 / 48_000.0
            }
            usleep(15_000)
        }
        writer.stop()

        XCTAssertNil(writerError, "Unexpected encoder error: \(writerError ?? "")")
        XCTAssertGreaterThanOrEqual(emitted.count, 2)
        let first = try XCTUnwrap(emitted.first)
        XCTAssertEqual(String(data: first.data[4..<8], encoding: .ascii), "ftyp")
    }
}
