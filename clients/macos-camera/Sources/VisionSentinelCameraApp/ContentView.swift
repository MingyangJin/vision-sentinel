import SwiftUI
import VisionSentinelCameraCore

struct ContentView: View {
    @ObservedObject var publisher: CapturePublisher

    var body: some View {
        VStack(spacing: 14) {
            // Live Camera Preview Card
            ZStack(alignment: .topLeading) {
                CameraPreviewView(session: publisher.previewSession)
                    .frame(height: 270)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(publisher.isStreaming ? Color.green.opacity(0.6) : Color.secondary.opacity(0.25), lineWidth: 1.5)
                    )

                HStack(spacing: 6) {
                    Circle()
                        .fill(publisher.isStreaming ? Color.red : Color.gray)
                        .frame(width: 8, height: 8)
                    Text(publisher.statusText)
                        .font(.caption.monospaced())
                        .foregroundStyle(.white)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(.black.opacity(0.65), in: Capsule())
                .padding(10)
            }

            if let err = publisher.lastError {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.yellow)
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(10)
                .background(Color.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            }

            // Capture Devices
            GroupBox("Capture Devices") {
                VStack(spacing: 10) {
                    HStack {
                        Text("Camera")
                            .frame(width: 86, alignment: .leading)
                            .foregroundStyle(.secondary)
                        Picker("", selection: $publisher.settings.videoDeviceID) {
                            ForEach(publisher.videoDevices) { dev in
                                Text(dev.name).tag(dev.id)
                            }
                        }
                        .labelsHidden()
                        .disabled(publisher.isStreaming)
                        .onChange(of: publisher.settings.videoDeviceID) { _ in
                            publisher.configurePreviewSession()
                        }

                        Picker("", selection: $publisher.settings.quality) {
                            ForEach(VideoQualityPreset.allCases) { preset in
                                Text(preset.rawValue).tag(preset)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 78)
                        .disabled(publisher.isStreaming)
                        .onChange(of: publisher.settings.quality) { _ in
                            publisher.configurePreviewSession()
                        }
                    }

                    HStack {
                        Toggle("Microphone", isOn: $publisher.settings.includeAudio)
                            .frame(width: 112, alignment: .leading)
                            .disabled(publisher.isStreaming)
                            .onChange(of: publisher.settings.includeAudio) { _ in
                                publisher.configurePreviewSession()
                            }

                        Picker("", selection: $publisher.settings.audioDeviceID) {
                            ForEach(publisher.audioDevices) { dev in
                                Text(dev.name).tag(dev.id)
                            }
                        }
                        .labelsHidden()
                        .disabled(publisher.isStreaming || !publisher.settings.includeAudio)
                        .onChange(of: publisher.settings.audioDeviceID) { _ in
                            publisher.configurePreviewSession()
                        }
                    }

                    if publisher.settings.includeAudio {
                        HStack(spacing: 8) {
                            Image(systemName: "mic.fill")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            GeometryReader { geo in
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Color.secondary.opacity(0.2))
                                    Capsule()
                                        .fill(Color.green)
                                        .frame(width: geo.size.width * CGFloat(publisher.audioLevel))
                                }
                            }
                            .frame(height: 6)
                        }
                    }
                }
                .padding(.vertical, 4)
            }

            // Stream Hub Target
            GroupBox("Vision Sentinel Stream Hub") {
                VStack(spacing: 8) {
                    HStack {
                        Text("Hub URL")
                            .frame(width: 86, alignment: .leading)
                            .foregroundStyle(.secondary)
                        TextField("http://127.0.0.1:8560", text: $publisher.settings.serverURL)
                            .textFieldStyle(.roundedBorder)
                            .disabled(publisher.isStreaming)
                    }
                    HStack {
                        Text("Camera ID")
                            .frame(width: 86, alignment: .leading)
                            .foregroundStyle(.secondary)
                        TextField("mac_cam", text: $publisher.settings.cameraID)
                            .textFieldStyle(.roundedBorder)
                            .disabled(publisher.isStreaming)
                    }
                    HStack {
                        Text("Token")
                            .frame(width: 86, alignment: .leading)
                            .foregroundStyle(.secondary)
                        SecureField("Optional Bearer token", text: $publisher.settings.bearerToken)
                            .textFieldStyle(.roundedBorder)
                            .disabled(publisher.isStreaming)
                    }
                }
                .padding(.vertical, 4)
            }

            // Primary Controls
            HStack(spacing: 10) {
                Button("Refresh Devices") {
                    publisher.refreshDevices()
                    publisher.configurePreviewSession()
                }
                .disabled(publisher.isStreaming)

                Button("Test Connection") {
                    publisher.testConnection()
                }

                Spacer()

                if publisher.isStreaming {
                    Button(role: .destructive) {
                        publisher.stopStreaming()
                    } label: {
                        Label("Stop Streaming", systemImage: "stop.fill")
                            .padding(.horizontal, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                } else {
                    Button {
                        publisher.startStreaming()
                    } label: {
                        Label("Start Streaming", systemImage: "video.fill")
                            .padding(.horizontal, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!publisher.cameraAuthorized)
                }
            }
        }
        .padding(16)
        .frame(width: 520)
        .onAppear {
            publisher.bootstrap()
        }
    }
}
