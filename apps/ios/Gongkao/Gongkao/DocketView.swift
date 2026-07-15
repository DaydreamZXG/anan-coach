import SwiftUI

struct DocketView: View {
    @EnvironmentObject private var appState: AppState
    @ObservedObject var store: DocketStore
    let accessToken: String?
    var onOpenWorkbench: () -> Void = {}
    private let bottomTabClearance: CGFloat = 72

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                if let docket = store.docket {
                    DocketCourtHeader(docket: docket) {
                        Task { await store.openCourt(accessToken: accessToken ?? "", force: true) }
                    }
                    ReviewCardsView(
                        docket: docket,
                        accessToken: accessToken ?? "",
                        onVerdict: {
                            Task { await appState.refreshRank() }
                        },
                        onOpenWorkbench: onOpenWorkbench
                    )
                    .id(docket.docketId)
                } else {
                    DocketOpenGate(
                        isOpening: store.isOpening,
                        errorMessage: store.errorMessage
                    ) {
                        Task { await store.openCourt(accessToken: accessToken ?? "", force: true) }
                    }
                }
            }
            .padding(16)
            .padding(.bottom, bottomTabClearance)
        }
        .background(GuzhuPalette.paper)
        .navigationTitle("今日开衙")
        .task(id: accessToken) {
            guard let accessToken, !accessToken.isEmpty else { return }
            await store.openCourt(accessToken: accessToken)
        }
    }
}

private struct DocketOpenGate: View {
    let isOpening: Bool
    let errorMessage: String?
    let open: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("升堂")
                .font(.system(size: 44, weight: .black, design: .serif))
                .foregroundStyle(GuzhuPalette.ink)
            Text("幕僚已在堂下候命。")
                .font(.headline)
                .foregroundStyle(GuzhuPalette.faintInk)
            Button(action: open) {
                Label(isOpening ? "开衙中" : "今日有何政务", systemImage: "scroll")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(GuzhuPalette.vermilion)
            .disabled(isOpening)
            if isOpening {
                ProgressView()
            }
            if let errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(GuzhuPalette.cinnabar)
            }
        }
        .parchmentCard(padding: 18)
    }
}

private struct DocketCourtHeader: View {
    let docket: DocketResponse
    let refresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(docket.rankSnapshot.rankName)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(GuzhuPalette.ink)
                    Text(docket.companionLine)
                        .font(.subheadline)
                        .foregroundStyle(GuzhuPalette.faintInk)
                }
                Spacer()
                Button(action: refresh) {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(GuzhuPalette.cinnabar)
                .accessibilityLabel("重新开衙")
            }
            HStack(spacing: 10) {
                DocketMetric(title: "民心", value: "\(docket.courtState.popularSentiment)")
                DocketMetric(title: "精力", value: "\(docket.rankSnapshot.stamina)")
                DocketMetric(title: "急件", value: "\(docket.cards.count)")
            }
        }
        .parchmentCard(padding: 16)
    }
}

private struct DocketMetric: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value)
                .font(.title3.weight(.bold))
                .foregroundStyle(GuzhuPalette.ink)
                .monospacedDigit()
            Text(title)
                .font(.caption)
                .foregroundStyle(GuzhuPalette.faintInk)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(GuzhuPalette.ink.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
    }
}
