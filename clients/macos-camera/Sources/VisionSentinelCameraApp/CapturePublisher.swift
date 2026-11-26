import AVFoundation
import Combine
import CoreMedia
import Foundation
import VisionSentinelCameraCore

struct CaptureDeviceOption: Identifiable, Hashable {
    let id: String
    let name: String
}

final class CapturePublisher: NSObject, ObservableObject, @unchecked Sendable {
    @Published var settings: CameraSettings {
        didSet { settings.save() }
    }
    @Published var videoDevices: [CaptureDeviceOption] = []
    @Published var audioDevices: [CaptureDeviceOption] = []
    @Published var isStreaming: Bool = false
    @Published var statusText: String = "Idle"
    @Published var lastError: String?
    @Published var segmentsUploaded: Int = 0
    @Published var lastSegmentKB: Int = 0
    @Published var audioLevel: Float = 0.0
    @Published var cameraAuthorized: Bool = false
    @Published var micAuthorized: Bool = false

    let previewSession = AVCaptureSession()

    private let captureQueue = DispatchQueue(label: "org.visionsentinel.camera.capture", qos: .userInitiated)
    private let writerQueue = DispatchQueue(label: "org.visionsentinel.camera.writer", qos: .userInitiated)
    private let ingestClient = IngestClient()

    private var videoOutput: AVCaptureVideoDataOutput?
    private var audioOutput: AVCaptureAudioDataOutput?
    private var segmentWriter: FMP4SegmentWriter?
    private var currentSettingsSnapshot = CameraSettings()

    override init() {
        self.settings = CameraSettings.load()
        super.init()
        refreshDevices()
    }

    func bootstrap() {
        Task {
            await requestPermissions()
            await MainActor.run {
                self.refreshDevices()
                self.configurePreviewSession()
            }
        }
    }

    func requestPermissions() async {
        let camGranted: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            camGranted = true
        case .notDetermined:
            camGranted = await AVCaptureDevice.requestAccess(for: .video)
        default:
            camGranted = false
        }

        let micGranted: Bool
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            micGranted = true
        case .notDetermined:
            micGranted = await AVCaptureDevice.requestAccess(for: .audio)
        default:
            micGranted = false
        }

        await MainActor.run {
            self.cameraAuthorized = camGranted
            self.micAuthorized = micGranted
            if !camGranted {
                self.lastError = "Camera access denied. Enable Vision Sentinel Camera in System Settings → Privacy & Security → Camera."
            }
        }
    }

    func refreshDevices() {
        var videoTypes: [AVCaptureDevice.DeviceType] = [.builtInWideAngleCamera, .externalUnknown]
        if #available(macOS 14.0, *) {
            videoTypes = [.builtInWideAngleCamera, .external, .continuityCamera]
        }
        let vDiscovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: videoTypes,
            mediaType: .video,
            position: .unspecified
        )
        let vOpts = vDiscovery.devices.map { CaptureDeviceOption(id: $0.uniqueID, name: $0.localizedName) }

        var audioTypes: [AVCaptureDevice.DeviceType] = [.builtInMicrophone, .externalUnknown]
        if #available(macOS 14.0, *) {
            audioTypes = [.microphone]
        }
        let aDiscovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: audioTypes,
            mediaType: .audio,
            position: .unspecified
        )
        let aOpts = aDiscovery.devices.map { CaptureDeviceOption(id: $0.uniqueID, name: $0.localizedName) }

        self.videoDevices = vOpts
        self.audioDevices = aOpts

        if settings.videoDeviceID.isEmpty || !vOpts.contains(where: { $0.id == settings.videoDeviceID }),
           let first = vOpts.first {
            settings.videoDeviceID = first.id
        }
        if settings.audioDeviceID.isEmpty || !aOpts.contains(where: { $0.id == settings.audioDeviceID }),
           let first = aOpts.first {
            settings.audioDeviceID = first.id
        }
    }

    func configurePreviewSession() {
        let snapshot = settings
        captureQueue.async { [weak self] in
            guard let self else { return }
            self.previewSession.beginConfiguration()
            let desiredPreset: AVCaptureSession.Preset = snapshot.quality == .hd720 ? .hd1280x720 : .vga640x480
            if self.previewSession.canSetSessionPreset(desiredPreset) {
                self.previewSession.sessionPreset = desiredPreset
            }

            for input in self.previewSession.inputs {
                self.previewSession.removeInput(input)
            }
            for output in self.previewSession.outputs {
                self.previewSession.removeOutput(output)
            }

            if let vDev = self.resolveVideoDevice(id: snapshot.videoDeviceID),
               let vIn = try? AVCaptureDeviceInput(device: vDev),
               self.previewSession.canAddInput(vIn) {
                self.previewSession.addInput(vIn)
            }

            if snapshot.includeAudio,
               let aDev = self.resolveAudioDevice(id: snapshot.audioDeviceID),
               let aIn = try? AVCaptureDeviceInput(device: aDev),
               self.previewSession.canAddInput(aIn) {
                self.previewSession.addInput(aIn)
            }

            let vOut = AVCaptureVideoDataOutput()
            vOut.alwaysDiscardsLateVideoFrames = true
            vOut.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
            ]
            vOut.setSampleBufferDelegate(self, queue: self.writerQueue)
            if self.previewSession.canAddOutput(vOut) {
                self.previewSession.addOutput(vOut)
                self.videoOutput = vOut
            }

            if snapshot.includeAudio {
                let aOut = AVCaptureAudioDataOutput()
                aOut.setSampleBufferDelegate(self, queue: self.writerQueue)
                if self.previewSession.canAddOutput(aOut) {
                    self.previewSession.addOutput(aOut)
                    self.audioOutput = aOut
                }
            } else {
                self.audioOutput = nil
            }

            self.previewSession.commitConfiguration()
            if !self.previewSession.isRunning {
                self.previewSession.startRunning()
            }
        }
    }

    func testConnection() {
        let snapshot = settings
        statusText = "Testing connection…"
        lastError = nil
        Task {
            do {
                let health = try await ingestClient.testConnection(settings: snapshot)
                let camFound = health.cameras[snapshot.sanitizedCameraID] != nil
                await MainActor.run {
                    if camFound {
                        self.statusText = "Connected · camera '\(snapshot.sanitizedCameraID)' ready"
                    } else {
                        let avail = health.cameras.keys.sorted().joined(separator: ", ")
                        self.statusText = "Hub reachable (cameras: \(avail.isEmpty ? "none" : avail))"
                    }
                }
            } catch {
                await MainActor.run {
                    self.lastError = error.localizedDescription
                    self.statusText = "Connection failed"
                }
            }
        }
    }

    func startStreaming() {
        guard !isStreaming else { return }
        guard settings.normalizedBaseURL != nil, !settings.sanitizedCameraID.isEmpty else {
            lastError = "Enter a valid Server URL and Camera ID before streaming."
            return
        }
        lastError = nil
        segmentsUploaded = 0
        lastSegmentKB = 0
        statusText = "Starting stream…"
        isStreaming = true

        let snapshot = settings
        let useAudio = snapshot.includeAudio && micAuthorized
        writerQueue.async { [weak self] in
            guard let self else { return }
            self.currentSettingsSnapshot = snapshot
            let recAudio: [String: Any]? = useAudio
                ? self.audioOutput?.recommendedAudioSettingsForAssetWriter(writingTo: .mp4)
                : nil
            let sw = FMP4SegmentWriter(
                quality: snapshot.quality,
                includeAudio: useAudio,
                segmentSeconds: snapshot.segmentSeconds,
                recommendedAudioSettings: recAudio,
                onSegment: { [weak self] ready in
                    self?.handleReadySegment(ready)
                },
                onError: { [weak self] errMsg in
                    Self.logLine("ERROR: \(errMsg)")
                    DispatchQueue.main.async {
                        self?.lastError = errMsg
                        self?.isStreaming = false
                        self?.statusText = "Encoder error"
                    }
                }
            )
            sw.begin()
            self.segmentWriter = sw
            DispatchQueue.main.async {
                self.statusText = "Streaming to \(snapshot.sanitizedCameraID)…"
            }
        }
    }

    func stopStreaming() {
        guard isStreaming else { return }
        isStreaming = false
        statusText = "Stopped"
        writerQueue.async { [weak self] in
            self?.segmentWriter?.stop()
            self?.segmentWriter = nil
        }
    }

    private func handleReadySegment(_ ready: ReadySegment) {
        let snapshot = currentSettingsSnapshot
        Task {
            do {
                let res = try await ingestClient.uploadSegment(ready, settings: snapshot)
                await MainActor.run {
                    self.segmentsUploaded = ready.sequence
                    self.lastSegmentKB = max(1, res.bytes / 1024)
                    self.lastError = nil
                    self.statusText = "LIVE · \(self.segmentsUploaded) segments · \(self.lastSegmentKB) KB"
                }
            } catch {
                Self.logLine("UPLOAD ERROR: \(error.localizedDescription)")
                await MainActor.run {
                    self.lastError = error.localizedDescription
                    self.statusText = "Upload error (retrying…)"
                }
            }
        }
    }

    private static func logLine(_ msg: String) {
        let logURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/VisionSentinelCamera.log")
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(msg)\n"
        if let data = line.data(using: .utf8) {
            if FileManager.default.fileExists(atPath: logURL.path),
               let fh = try? FileHandle(forWritingTo: logURL) {
                fh.seekToEndOfFile()
                fh.write(data)
                try? fh.close()
            } else {
                try? data.write(to: logURL)
            }
        }
    }

    private func resolveVideoDevice(id: String) -> AVCaptureDevice? {
        if !id.isEmpty, let exact = AVCaptureDevice(uniqueID: id) {
            return exact
        }
        return AVCaptureDevice.default(for: .video)
    }

    private func resolveAudioDevice(id: String) -> AVCaptureDevice? {
        if !id.isEmpty, let exact = AVCaptureDevice(uniqueID: id) {
            return exact
        }
        return AVCaptureDevice.default(for: .audio)
    }

    private func updateAudioMeter(sampleBuffer: CMSampleBuffer) {
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        var length = 0
        var ptr: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &ptr) == kCMBlockBufferNoErr,
              let base = ptr, length >= 4
        else { return }

        guard let desc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)?.pointee
        else { return }

        var rms: Float = 0
        if (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0 && asbd.mBitsPerChannel == 32 {
            let count = length / 4
            let samples = base.withMemoryRebound(to: Float.self, capacity: count) { $0 }
            var sum: Float = 0
            for i in stride(from: 0, to: count, by: max(1, count / 128)) {
                let s = samples[i]
                sum += s * s
            }
            rms = sqrt(sum / Float(max(1, min(count, 128))))
        } else if asbd.mBitsPerChannel == 16 {
            let count = length / 2
            let samples = base.withMemoryRebound(to: Int16.self, capacity: count) { $0 }
            var sum: Float = 0
            for i in stride(from: 0, to: count, by: max(1, count / 128)) {
                let s = Float(samples[i]) / 32768.0
                sum += s * s
            }
            rms = sqrt(sum / Float(max(1, min(count, 128))))
        }
        let normalized = min(1.0, max(0.0, rms * 4.0))
        DispatchQueue.main.async {
            self.audioLevel = self.audioLevel * 0.6 + normalized * 0.4
        }
    }
}

extension CapturePublisher: AVCaptureVideoDataOutputSampleBufferDelegate, AVCaptureAudioDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        if output === audioOutput {
            updateAudioMeter(sampleBuffer: sampleBuffer)
            segmentWriter?.appendAudio(sampleBuffer)
        } else if output === videoOutput {
            segmentWriter?.appendVideo(sampleBuffer)
        }
    }
}
