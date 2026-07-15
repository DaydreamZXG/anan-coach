import SwiftUI
import UIKit

struct ShareReportPayload: Equatable {
    let dateText: String
    let rankName: String
    let totalCount: Int
    let correctCount: Int
    let wrongCount: Int
    let meritDelta: Int
    let fameDelta: Int
    let companionLine: String
    let sourceLine: String

    init(docket: DocketResponse, verdicts: [ReviewVerdictResponse]) {
        var verdictByCardId: [String: ReviewVerdictResponse] = [:]
        for verdict in verdicts {
            verdictByCardId[verdict.cardId] = verdict
        }
        let orderedVerdicts = docket.cards.compactMap { verdictByCardId[$0.cardId] }
        let correct = orderedVerdicts.filter(\.correct).count
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy年M月d日"
        dateText = formatter.string(from: docket.openedAt)
        rankName = docket.rankSnapshot.rankName
        totalCount = docket.cards.count
        correctCount = correct
        wrongCount = max(0, orderedVerdicts.count - correct)
        meritDelta = orderedVerdicts.reduce(0) { $0 + $1.progression.meritDelta }
        fameDelta = orderedVerdicts.reduce(0) { $0 + $1.progression.fameDelta }
        companionLine = orderedVerdicts.last?.companionLine ?? docket.companionLine
        sourceLine = "\(docket.cards.count) 道真题 · \(docket.sourceGrounding)"
    }
}

struct CourtReportPanel: View {
    let payload: ShareReportPayload
    var onOpenWorkbench: () -> Void = {}
    @State private var shareImage: CourtReportImage?

    var body: some View {
        VStack(spacing: 14) {
            ShareReportView(payload: payload)
            Button(action: onOpenWorkbench) {
                Label(primaryActionTitle, systemImage: "tray.full")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(GuzhuPalette.vermilion)
            Button {
                shareImage = renderCourtReportImage(payload: payload)
            } label: {
                Label("呈送战报", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(GuzhuPalette.cinnabar)
        }
        .sheet(item: $shareImage) { image in
            ActivityShareSheet(items: [image.image])
        }
    }

    private var primaryActionTitle: String {
        payload.wrongCount > 0 ? "去府衙复盘错卷" : "去府衙归档战报"
    }
}

struct ShareReportView: View {
    let payload: ShareReportPayload

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("今日政务战报")
                        .font(.title2.weight(.black))
                        .foregroundStyle(GuzhuPalette.ink)
                    Text(payload.dateText)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GuzhuPalette.faintInk)
                }
                Spacer()
                SealStamp(text: "结", color: GuzhuPalette.cinnabar, size: 58)
            }

            Text(payload.rankName)
                .font(.system(size: 34, weight: .black, design: .serif))
                .foregroundStyle(GuzhuPalette.ink)

            HStack(spacing: 10) {
                reportMetric("准", "\(payload.correctCount)")
                reportMetric("驳", "\(payload.wrongCount)")
                reportMetric("政绩", "+\(payload.meritDelta)")
                reportMetric("民心", signed(payload.fameDelta))
            }

            Text(payload.companionLine)
                .font(.callout.weight(.semibold))
                .foregroundStyle(GuzhuPalette.ink)
                .fixedSize(horizontal: false, vertical: true)

            Text(payload.sourceLine)
                .font(.caption)
                .foregroundStyle(GuzhuPalette.faintInk)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .parchmentCard(padding: 18)
    }

    private func reportMetric(_ title: String, _ value: String) -> some View {
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

    private func signed(_ value: Int) -> String {
        value >= 0 ? "+\(value)" : "\(value)"
    }
}

struct CourtReportImage: Identifiable {
    let id = UUID()
    let image: UIImage
}

@MainActor
private func renderCourtReportImage(payload: ShareReportPayload) -> CourtReportImage? {
    let content = ShareReportView(payload: payload)
        .frame(width: 390)
        .padding(18)
        .background(GuzhuPalette.paper)
    let renderer = ImageRenderer(content: content)
    renderer.scale = UIScreen.main.scale
    guard let image = renderer.uiImage else { return nil }
    return CourtReportImage(image: image)
}

private struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(
        _ uiViewController: UIActivityViewController,
        context: Context
    ) {}
}
