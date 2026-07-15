import SwiftUI

struct ReviewCardsView: View {
    let docket: DocketResponse
    let accessToken: String
    var onVerdict: () -> Void = {}
    var onOpenWorkbench: () -> Void = {}
    @StateObject private var store = ReviewCardsStore()

    private var activeCard: DocketCard? {
        guard docket.cards.indices.contains(store.activeCardIndex) else { return nil }
        return docket.cards[store.activeCardIndex]
    }

    var body: some View {
        VStack(spacing: 14) {
            if store.isCompleted {
                CourtReportPanel(
                    payload: ShareReportPayload(
                        docket: docket,
                        verdicts: Array(store.verdicts.values)
                    ),
                    onOpenWorkbench: onOpenWorkbench
                )
            } else if let card = activeCard {
                ReviewCardSurface(
                    index: store.activeCardIndex + 1,
                    total: docket.cards.count,
                    card: card,
                    verdict: store.verdicts[card.cardId],
                    selectedAnswer: store.selectedAnswers[card.cardId],
                    isSubmitting: store.isSubmitting,
                    onSelect: { answer in
                        Task {
                            let verdict = await store.submit(
                                docketId: docket.docketId,
                                card: card,
                                selectedAnswer: answer,
                                accessToken: accessToken
                            )
                            if verdict != nil {
                                onVerdict()
                            }
                        }
                    },
	                    onNext: {
	                        store.advanceOrComplete(total: docket.cards.count)
	                    }
	                )
	                .id(card.cardId)
            } else {
                CourtReportPanel(
                    payload: ShareReportPayload(
                        docket: docket,
                        verdicts: Array(store.verdicts.values)
                    ),
                    onOpenWorkbench: onOpenWorkbench
                )
            }
            if let errorMessage = store.errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(GuzhuPalette.cinnabar)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .onChange(of: store.activeCardIndex) {
            store.resetTimer()
        }
    }
}

private struct ReviewCardSurface: View {
    let index: Int
    let total: Int
    let card: DocketCard
    let verdict: ReviewVerdictResponse?
    let selectedAnswer: String?
    let isSubmitting: Bool
    let onSelect: (String) -> Void
    let onNext: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label(card.title, systemImage: "doc.text")
                    .font(.headline)
                    .foregroundStyle(GuzhuPalette.ink)
                Spacer()
                Text("\(index)/\(total)")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GuzhuPalette.faintInk)
                    .monospacedDigit()
            }
            Text(card.worldLabel)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GuzhuPalette.moduleColor(card.moduleName))
            Text(card.narrative)
                .font(.subheadline)
                .foregroundStyle(GuzhuPalette.faintInk)
            if let material = card.materialText, !material.isEmpty {
                QuestionRichText(
                    plainText: material,
                    htmlText: card.materialHtml,
                    font: .callout,
                    uiFont: .preferredFont(forTextStyle: .callout),
                    lineSpacing: 4,
                    foregroundColor: GuzhuPalette.ink
                )
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(GuzhuPalette.ink.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
            }
            QuestionRichText(
                plainText: card.stem,
                htmlText: card.stemHtml,
                font: .body.weight(.semibold),
                uiFont: .preferredFont(forTextStyle: .body),
                lineSpacing: 5,
                foregroundColor: GuzhuPalette.ink
            )
            VStack(spacing: 10) {
                ForEach(card.options, id: \.label) { option in
                    Button {
                        onSelect(option.label)
                    } label: {
                        HStack(spacing: 10) {
	                            Text(option.label)
	                                .font(.headline)
	                                .frame(width: 28, height: 28)
	                                .background(optionBackground(option.label), in: Circle())
	                            QuestionRichText(
	                                plainText: option.displayText,
	                                htmlText: option.html,
	                                font: .body,
	                                uiFont: .preferredFont(forTextStyle: .body),
	                                lineSpacing: 3,
	                                foregroundColor: GuzhuPalette.ink
	                            )
	                            .frame(maxWidth: .infinity, alignment: .leading)
	                        }
                        .foregroundStyle(GuzhuPalette.ink)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(optionFill(option.label), in: RoundedRectangle(cornerRadius: 8))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .strokeBorder(optionStroke(option.label), lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(verdict != nil || isSubmitting)
                }
            }
            if let verdict {
                HStack(alignment: .center, spacing: 14) {
                    SealStamp(
                        text: verdict.stampText,
                        color: verdict.correct ? GuzhuPalette.celadon : GuzhuPalette.cinnabar,
                        size: 64
                    )
                    VStack(alignment: .leading, spacing: 6) {
                        Text(verdict.correct ? "准" : "驳")
                            .font(.headline)
                            .foregroundStyle(verdict.correct ? GuzhuPalette.celadon : GuzhuPalette.cinnabar)
                        Text(verdict.companionLine)
                            .font(.subheadline)
                            .foregroundStyle(GuzhuPalette.faintInk)
                        Text(verdict.progression.narrative)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GuzhuPalette.ink)
                    }
                }
                Button(action: onNext) {
                    Label(index == total ? "收卷" : "下一封", systemImage: "arrow.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(GuzhuPalette.vermilion)
            }
            Text(card.sourceSummary)
                .font(.caption)
                .foregroundStyle(GuzhuPalette.faintInk)
        }
        .parchmentCard(padding: 16)
    }

    private func optionFill(_ label: String) -> Color {
        guard let verdict, selectedAnswer == label else {
            return GuzhuPalette.card
        }
        return verdict.correct ? GuzhuPalette.celadon.opacity(0.16) : GuzhuPalette.cinnabar.opacity(0.14)
    }

    private func optionStroke(_ label: String) -> Color {
        guard selectedAnswer == label else { return GuzhuPalette.cardStroke }
        return verdict?.correct == true ? GuzhuPalette.celadon : GuzhuPalette.cinnabar
    }

    private func optionBackground(_ label: String) -> Color {
        selectedAnswer == label ? GuzhuPalette.ink.opacity(0.12) : GuzhuPalette.ink.opacity(0.06)
    }
}
