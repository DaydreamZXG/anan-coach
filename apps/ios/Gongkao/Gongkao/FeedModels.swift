import Foundation

/// 卡片词表 v1（封闭枚举，对齐 genui-card-feed 规格第 4 节）。
/// 服务端下发词表外类型时解码为 nil，渲染层转 fallback——旧客户端对新卡自动降级。
enum FeedCardType: String, Decodable {
    case goalGap = "goal_gap"
    case coachBrief = "coach_brief"
    case taskPractice = "task_practice"
    case taskEssay = "task_essay"
    case taskMethodCard = "task_method_card"
    case mockExamDue = "mock_exam_due"
    case mockReviewPending = "mock_review_pending"
    case wrongCluster = "wrong_cluster"
    case streakRestart = "streak_restart"
    case calibrationNote = "calibration_note"
    case quotaNotice = "quota_notice"
    case companionPrompt = "companion_prompt"
    case externalCaptureNudge = "external_capture_nudge"
}

struct FeedResponse: Decodable {
    let generatedAt: String
    let planBasis: String
    let cards: [FeedCard]
}

struct FeedCard: Decodable, Identifiable {
    let cardId: String
    let rawType: String
    let priority: Int
    let reason: String
    let dataLine: String?
    let payload: FeedCardPayload?

    var id: String { cardId }
    /// 词表内类型；nil = 未知类型 → 渲染 fallback 卡
    var type: FeedCardType? { FeedCardType(rawValue: rawType) }

    enum CodingKeys: String, CodingKey {
        case cardId, priority, reason, dataLine, payload
        case rawType = "type"
    }
}

/// 宽 payload：全部可选，字段因卡型而异（服务端各 type 的 schema 见后端 feed schemas）。
struct FeedCardPayload: Decodable {
    // goal_gap
    let targetScore: Double?
    let estimatedScore: Double?
    let daysLeft: Int?
    let positionName: String?
    // coach_brief
    let text: String?
    let planBasis: String?
    // task_*
    let taskId: String?
    let moduleName: String?
    let questionCount: Int?
    let estMinutes: Int?
    let questionType: String?
    let cardIdRef: String?
    let cardTitle: String?
    // mock_exam_due / mock_review_pending
    let sessionId: String?
    // wrong_cluster
    let nodeId: String?
    let displayPath: String?
    let wrongCount: Int?
    // streak_restart
    let daysIdle: Int?
    let miniTaskId: String?
    // calibration_note
    let statusLabel: String?
    let delta: Double?
    // quota_notice
    let remaining: Int?
    let resetAt: String?
    // companion_prompt
    let suggestedQuestion: String?
    let contextObjectId: String?
    // fallback / 通用
    let title: String?
    let body: String?
    let deeplink: String?

    enum CodingKeys: String, CodingKey {
        case targetScore, estimatedScore, daysLeft, positionName
        case text, planBasis
        case taskId, moduleName, questionCount, estMinutes, questionType, cardTitle
        case cardIdRef = "cardId"
        case sessionId
        case nodeId, displayPath, wrongCount
        case daysIdle, miniTaskId
        case statusLabel, delta
        case remaining, resetAt
        case suggestedQuestion, contextObjectId
        case title, body, deeplink
    }
}
