import Foundation
import UIKit

@MainActor
final class ReviewCardsStore: ObservableObject {
    @Published var verdicts: [String: ReviewVerdictResponse] = [:]
    @Published var selectedAnswers: [String: String] = [:]
    @Published var activeCardIndex = 0
    @Published var isCompleted = false
    @Published var isSubmitting = false
    @Published var errorMessage: String?

    private let client: APIClient
    private var appearedAt = Date()

    init(client: APIClient = APIClient()) {
        self.client = client
    }

    func resetTimer() {
        appearedAt = Date()
    }

    @discardableResult
    func submit(
        docketId: String,
        card: DocketCard,
        selectedAnswer: String,
        accessToken: String
    ) async -> ReviewVerdictResponse? {
        guard verdicts[card.cardId] == nil else { return verdicts[card.cardId] }
        isSubmitting = true
        errorMessage = nil
        selectedAnswers[card.cardId] = selectedAnswer
        defer { isSubmitting = false }
        do {
            let elapsed = max(0, Int(Date().timeIntervalSince(appearedAt)))
            let verdict: ReviewVerdictResponse = try await client.post(
                "/api/v1/review-sessions/\(docketId)/cards/\(card.cardId)/verdict",
                body: ReviewVerdictRequest(
                    selectedAnswer: selectedAnswer,
                    elapsedSeconds: elapsed
                ),
                accessToken: accessToken
            )
            verdicts[card.cardId] = verdict
            UINotificationFeedbackGenerator().notificationOccurred(
                verdict.correct ? .success : .warning
            )
            return verdict
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func advanceOrComplete(total: Int) {
        guard activeCardIndex + 1 < total else {
            isCompleted = true
            return
        }
        activeCardIndex += 1
        resetTimer()
    }

    func resetRun() {
        verdicts = [:]
        selectedAnswers = [:]
        activeCardIndex = 0
        isCompleted = false
        errorMessage = nil
        resetTimer()
    }
}
