import Foundation

struct DocketResponse: Decodable {
    let docketId: String
    let openedAt: Date
    let sourceGrounding: String
    let companionLine: String
    let rankSnapshot: DocketRankSnapshot
    let courtState: DocketCourtState
    let cards: [DocketCard]
}

struct DocketRankSnapshot: Decodable {
    let rankCode: String
    let rankLevel: Int
    let rankName: String
    let xp: Int
    let fame: Int
    let stamina: Int
    let consecutiveDays: Int
    let uiTheme: String
    let unlockFeatures: [String]
    let nextRankName: String?
    let xpToNext: Int?
}

struct DocketCourtState: Decodable {
    let popularSentiment: Int
    let stability: Int
    let demerits: Int
    let riskLevel: String
    let backgroundTone: String
}

struct DocketCard: Decodable, Identifiable, Equatable {
    let cardId: String
    let type: String
    let track: String
    let interaction: String
    let questionId: String
    let moduleName: String
    let worldLabel: String
    let title: String
    let narrative: String
    let stem: String
    let stemHtml: String?
    let materialText: String?
    let materialHtml: String?
    let options: [DocketOption]
    let sourceGrounding: String
    let sourceSummary: String
    let occurrenceCount: Int?
    let latestYear: Int?

    var id: String { cardId }
}

struct DocketOption: Decodable, Equatable {
    let label: String
    let text: String
    let html: String?
}

extension DocketOption {
    var displayText: String {
        var normalized = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let label = label.trimmingCharacters(in: .whitespaces)
        guard !label.isEmpty, normalized.hasPrefix(label) else { return normalized }
        normalized = String(normalized.dropFirst(label.count))
        let separators: Set<Character> = ["、", ".", "．", "，", ",", ":", "：", ")", "）", " "]
        while let first = normalized.first, separators.contains(first) {
            normalized = String(normalized.dropFirst())
        }
        return normalized
    }
}
