import Foundation

struct RankProfile: Decodable {
    let userId: String
    let rankCode: String
    let rankLevel: Int
    let rankName: String
    let xp: Int
    let fame: Int
    let stamina: Int
    let consecutiveDays: Int
    let demerits: Int
    let uiTheme: String
    let nextRankName: String?
    let nextRankXpThreshold: Int?
    let xpToNext: Int?
    let unlockFeatures: [String]

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case rankCode = "rank_code"
        case rankLevel = "rank_level"
        case rankName = "rank_name"
        case xp
        case fame
        case stamina
        case consecutiveDays = "consecutive_days"
        case demerits
        case uiTheme = "ui_theme"
        case nextRankName = "next_rank_name"
        case nextRankXpThreshold = "next_rank_xp_threshold"
        case xpToNext = "xp_to_next"
        case unlockFeatures = "unlock_features"
    }

    /// 判断某项解锁特性是否已获得（后端返回的是累计解锁列表）。
    func isUnlocked(_ feature: String) -> Bool {
        unlockFeatures.contains(feature)
    }
}

struct RankConfigItem: Decodable {
    let rankCode: String
    let rankLevel: Int
    let rankName: String
    let xpThreshold: Int
    let uiTheme: String
    let aiPrivilege: String?
    let unlockFeatures: [String]

    enum CodingKeys: String, CodingKey {
        case rankCode = "rank_code"
        case rankLevel = "rank_level"
        case rankName = "rank_name"
        case xpThreshold = "xp_threshold"
        case uiTheme = "ui_theme"
        case aiPrivilege = "ai_privilege"
        case unlockFeatures = "unlock_features"
    }
}
