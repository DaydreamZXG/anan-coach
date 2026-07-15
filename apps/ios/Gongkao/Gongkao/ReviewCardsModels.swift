import Foundation

struct ReviewVerdictRequest: Encodable {
    let selectedAnswer: String
    let elapsedSeconds: Int?
}

struct ReviewVerdictResponse: Decodable {
    let verdictId: String
    let docketId: String
    let cardId: String
    let questionId: String
    let selectedAnswer: String
    let correct: Bool
    let stampText: String
    let companionLine: String
    let progression: ProgressionEventResult
    let sourceGrounding: String
    let createdAt: Date
}

struct ProgressionEventResult: Decodable, Equatable {
    let meritDelta: Int
    let fameDelta: Int
    let courtRiskDelta: Int
    let rankChanged: Bool
    let oldRankName: String?
    let newRankName: String?
    let lockedPrivileges: [String]
    let narrative: String
}
