import SwiftUI
import VisionSentinelCore

struct FeedbackSheetView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    let alertId: String
    let onSubmitted: (FeedbackResponse) -> Void

    @State private var rating: FeedbackRating
    @State private var note: String = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    init(
        alertId: String,
        initialRating: FeedbackRating,
        onSubmitted: @escaping (FeedbackResponse) -> Void
    ) {
        self.alertId = alertId
        self.onSubmitted = onSubmitted
        _rating = State(initialValue: initialRating)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Rating") {
                    Picker("Rating", selection: $rating) {
                        Label("Thumbs Up — True Alarm", systemImage: "hand.thumbsup.fill")
                            .tag(FeedbackRating.up)
                        Label("Thumbs Down — False Alarm", systemImage: "hand.thumbsdown.fill")
                            .tag(FeedbackRating.down)
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }

                Section {
                    TextField(
                        "Optional note (e.g., \"Ignore tree shadow on left\")",
                        text: $note,
                        axis: .vertical
                    )
                    .lineLimit(3...6)
                } header: {
                    Text("Optional Note")
                } footer: {
                    Text("Submitted feedback is logged to the Topic directory and pins the observation bundle so it is not expired.")
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Alert Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSubmitting ? "Sending…" : "Submit") {
                        Task { await submit() }
                    }
                    .disabled(isSubmitting)
                }
            }
        }
    }

    private func submit() async {
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            let response = try await appState.submitFeedback(
                alertId: alertId,
                rating: rating,
                note: note.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            onSubmitted(response)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
