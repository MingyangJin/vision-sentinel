import AVKit
import SwiftUI
import UIKit
import VisionSentinelCore

struct AlertDetailView: View {
    @Environment(AppState.self) private var appState
    let alertId: String

    @State private var loadedAlert: AlertRecord?
    @State private var thumbnailImage: UIImage?
    @State private var isLoadingThumbnail = false
    @State private var clipPlayer: AVPlayer?
    @State private var isLoadingClip = false
    @State private var mediaError: String?

    @State private var isAcking = false
    @State private var actionMessage: String?
    @State private var actionError: String?
    @State private var pendingFeedbackRating: FeedbackRating?

    private var alert: AlertRecord? {
        appState.alerts.first(where: { $0.id == alertId }) ?? loadedAlert
    }

    var body: some View {
        ScrollView {
            if let alert {
                VStack(alignment: .leading, spacing: 16) {
                    headerSection(alert)
                    mediaSection(alert)
                    actionSection(alert)
                    feedbackHistorySection(alert)
                    metadataSection(alert)
                }
                .padding()
            } else {
                ProgressView("Loading alert…")
                    .padding(.top, 48)
            }
        }
        .navigationTitle("Alert Detail")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: alertId) {
            await loadAlertAndThumbnail()
        }
        .sheet(item: $pendingFeedbackRating) { rating in
            FeedbackSheetView(alertId: alertId, initialRating: rating) { response in
                loadedAlert = response.alert
                actionMessage = response.pinned
                    ? "Feedback recorded and bundle pinned for review."
                    : "Feedback recorded."
                actionError = nil
            }
        }
    }

    @ViewBuilder
    private func headerSection(_ alert: AlertRecord) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SeverityBadge(severity: alert.severity)
                Text(alert.topicId)
                    .font(.caption.monospaced())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(.secondary.opacity(0.15), in: Capsule())
                Spacer()
                Text(alert.createdAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(alert.title)
                .font(.title3.weight(.semibold))

            if !alert.body.isEmpty {
                Text(alert.body)
                    .font(.body)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func mediaSection(_ alert: AlertRecord) -> some View {
        if alert.hasThumbnail || alert.hasClip {
            VStack(alignment: .leading, spacing: 10) {
                Text("Observation Media")
                    .font(.subheadline.weight(.semibold))

                if let player = clipPlayer {
                    VideoPlayer(player: player)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                } else if let thumbnailImage {
                    Image(uiImage: thumbnailImage)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: .infinity, maxHeight: 240)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                } else if isLoadingThumbnail {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(.secondary.opacity(0.12))
                            .frame(height: 180)
                        ProgressView("Loading authenticated thumbnail…")
                    }
                }

                if alert.hasClip && clipPlayer == nil {
                    Button {
                        Task { await loadClip() }
                    } label: {
                        HStack {
                            if isLoadingClip {
                                ProgressView()
                            } else {
                                Image(systemName: "play.circle.fill")
                            }
                            Text(isLoadingClip ? "Downloading Clip…" : "Play Clip")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isLoadingClip)
                }

                if let mediaError {
                    Text(mediaError)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func actionSection(_ alert: AlertRecord) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if alert.isAcknowledged {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Acknowledged")
                            .font(.subheadline.weight(.semibold))
                        if let ackedAt = alert.ackedAt {
                            let who = alert.ackedBy ?? "device"
                            Text("By \(who) at \(ackedAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                }
                .padding(12)
                .background(.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            } else {
                Button {
                    Task { await acknowledge() }
                } label: {
                    HStack {
                        if isAcking {
                            ProgressView()
                        } else {
                            Image(systemName: "checkmark.circle.fill")
                        }
                        Text(isAcking ? "Acknowledging…" : "Acknowledge Alert")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isAcking)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Was this alert helpful?")
                    .font(.subheadline.weight(.semibold))
                Text("Feedback is separate from acknowledgment and helps tune the Topic's Main Agent.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack(spacing: 12) {
                    Button {
                        pendingFeedbackRating = .up
                    } label: {
                        Label("Thumbs Up", systemImage: "hand.thumbsup.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.blue)

                    Button {
                        pendingFeedbackRating = .down
                    } label: {
                        Label("Thumbs Down", systemImage: "hand.thumbsdown.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
            }

            if let actionMessage {
                Label(actionMessage, systemImage: "checkmark.circle")
                    .font(.footnote)
                    .foregroundStyle(.green)
            }
            if let actionError {
                Label(actionError, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private func feedbackHistorySection(_ alert: AlertRecord) -> some View {
        if !alert.feedback.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Feedback History")
                    .font(.subheadline.weight(.semibold))
                ForEach(alert.feedback) { item in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: item.rating == .up ? "hand.thumbsup.fill" : "hand.thumbsdown.fill")
                            .foregroundStyle(item.rating == .up ? .blue : .red)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.rating.displayName)
                                .font(.subheadline.weight(.medium))
                            if !item.note.isEmpty {
                                Text(item.note)
                                    .font(.footnote)
                            }
                            Text("\(item.by.isEmpty ? "device" : item.by) · \(item.at.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    @ViewBuilder
    private func metadataSection(_ alert: AlertRecord) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Details")
                .font(.subheadline.weight(.semibold))
            LabeledContent("Alert ID", value: alert.id)
                .font(.caption.monospaced())
            if !alert.request.cameraIds.isEmpty {
                LabeledContent("Cameras", value: alert.request.cameraIds.joined(separator: ", "))
                    .font(.caption)
            }
            if let bundleId = alert.request.bundleId {
                LabeledContent("Bundle", value: bundleId)
                    .font(.caption.monospaced())
            }
            LabeledContent("Source", value: alert.request.source)
                .font(.caption)
        }
        .padding(12)
        .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private func loadAlertAndThumbnail() async {
        if alert == nil {
            if let client = try? appState.makeClient(),
               let fetched = try? await client.getAlert(id: alertId) {
                loadedAlert = fetched
            }
        }
        guard let current = alert, current.hasThumbnail else { return }
        isLoadingThumbnail = true
        defer { isLoadingThumbnail = false }
        do {
            let data = try await appState.fetchThumbnailData(alertId: current.id)
            if let img = UIImage(data: data) {
                thumbnailImage = img
            }
        } catch {
            mediaError = "Thumbnail unavailable (\(error.localizedDescription))."
        }
    }

    private func loadClip() async {
        isLoadingClip = true
        mediaError = nil
        defer { isLoadingClip = false }
        do {
            let fileURL = try await appState.downloadClipToTemporaryFile(alertId: alertId)
            let player = AVPlayer(url: fileURL)
            clipPlayer = player
            player.play()
        } catch {
            mediaError = "Could not load clip: \(error.localizedDescription)"
        }
    }

    private func acknowledge() async {
        isAcking = true
        actionError = nil
        actionMessage = nil
        defer { isAcking = false }
        do {
            let updated = try await appState.acknowledge(alertId: alertId)
            loadedAlert = updated
            actionMessage = "Alert acknowledged."
        } catch {
            actionError = error.localizedDescription
        }
    }
}

