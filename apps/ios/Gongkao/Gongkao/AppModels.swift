import AuthenticationServices
import Combine
import CryptoKit
import Foundation
import Security
import UIKit

enum AppSection: String, CaseIterable, Identifiable {
    case today
    case workbench
    case profile

    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: "今日"
        case .workbench: "工作台"
        case .profile: "我的"
        }
    }

    var symbol: String {
        switch self {
        case .today: "calendar"
        case .workbench: "rectangle.stack"
        case .profile: "person.crop.circle"
        }
    }
}

enum StudyTrack: String, Identifiable {
    case xingce = "行测"
    case shenlun = "申论"

    var id: String { rawValue }
}

struct WorkbenchPreview: Decodable {
    let headline: String
    let sourceBase: WorkbenchSourceBase
    let sections: [WorkbenchSection]
}

struct WorkbenchSourceBase: Decodable {
    let canonicalQuestions: Int
    let knowledgeChunks: Int
    let positionRecords: Int
    let openWebResearch: Bool
}

struct WorkbenchObject: Identifiable, Decodable {
    let id: String
    let type: String
    let title: String
    let subtitle: String
    let sourceCount: Int
    let payload: WorkbenchObjectPayload?

    var kind: String {
        switch type {
        case "docket_review": "卷宗"
        case "practice_review": "复盘"
        case "essay_review": "申论"
        case "mock_exam_review": "模考"
        case "external_wrong_question": "外部错题"
        default: "对象"
        }
    }

    var symbol: String {
        switch type {
        case "docket_review": "scroll"
        case "practice_review": "checklist"
        case "essay_review": "square.and.pencil"
        case "mock_exam_review": "stopwatch"
        case "external_wrong_question": "camera.viewfinder"
        default: "doc.text"
        }
    }

    var tint: String {
        switch type {
        case "docket_review": "red"
        case "practice_review": "blue"
        case "essay_review": "orange"
        case "mock_exam_review": "indigo"
        case "external_wrong_question": "orange"
        default: "indigo"
        }
    }
}

struct WorkbenchObjectPayload: Decodable {
    // docket_review
    let docketId: String?
    let totalCount: Int?
    // practice_review
    let taskId: String?
    let latestAnsweredAt: String?
    let questionIds: [String]?
    let wrongCount: Int?
    // essay_review
    let attemptId: String?
    let updatedAt: String?
    let hasRewrite: Bool?
    // mock_exam_review
    let sessionId: String?
    let submittedAt: String?
    let viewed: Bool?
    let estimatedScore: Double?
    let nextWeekReason: String?
    // external_wrong_question
    let externalQuestionId: String?
    let nodeId: String?
    let displayPath: String?
    let createdAt: String?
}

struct WorkbenchSection: Identifiable, Decodable {
    let id: String
    let title: String
    let objects: [WorkbenchObject]
}

struct TodayTaskstream: Decodable {
    let date: String
    let headline: String
    let target: TargetContext
    let trackMix: [BackendTrackMix]
    let primaryAction: PrimaryAction
    let rationale: String
    let tasks: [BackendTodayTask]
    let completedTaskIds: [String]
}

struct TargetContext: Decodable {
    let exam: String
    let positionGroup: String
    let scoreGap: Double
}

struct BackendTrackMix: Decodable, Identifiable {
    var id: String { track }
    let track: String
    let minutes: Int
    let reason: String
}

struct PrimaryAction: Decodable {
    let title: String
    let subtitle: String
}

/// 明日政务预告（GET /api/v1/taskstream/tomorrow，不抽题不落库，仅预览）
/// 字段对齐后端 taskstream.schemas.TomorrowPreview
struct TomorrowFocus: Decodable {
    let module: String
    let questionCount: Int
    let reason: String

    enum CodingKeys: String, CodingKey {
        case module
        case questionCount = "questionCount"
        case reason
    }
}

struct TomorrowPreview: Decodable {
    let date: String
    let headline: String
    let target: TargetContext
    let trackMix: [BackendTrackMix]
    let focus: TomorrowFocus
    let primaryAction: PrimaryAction

    enum CodingKeys: String, CodingKey {
        case date, headline, target, focus
        case trackMix = "trackMix"
        case primaryAction = "primaryAction"
    }
}

struct BackendTodayTask: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let kind: String
    let track: String
    let moduleName: String?
    let estimatedMinutes: Int
    let reason: String
    let sourceGrounding: String
    let questionIds: [String]
    let startUrl: String

    var symbol: String {
        switch kind {
        case "xingce_practice": "checklist"
        case "essay_rewrite": "square.and.pencil"
        default: "doc.text"
        }
    }
}

struct PracticeTaskResponse: Decodable {
    let taskId: String
    let title: String
    let track: String
    let questionCount: Int
    let questions: [QuestionDetail]
}

struct QuestionDetail: Decodable, Identifiable {
    var id: String { questionId }
    let questionId: String
    let domain: String
    let moduleName: String?
    let questionType: String?
    let stem: String
    let answer: String?
    let visualRequired: Bool
    let occurrenceCount: Int?
    let options: [QuestionOption]
    let explanation: QuestionExplanation?
    let occurrences: [QuestionOccurrence]
    let tags: [QuestionTag]
    let materialText: String?
    let materialHtml: String?
    let stemHtml: String?
}

struct QuestionOption: Decodable, Identifiable {
    var id: Int { optionIndex }
    let optionIndex: Int
    let label: String
    let optionText: String
    let optionHtml: String?
}

extension QuestionOption {
    /// optionText 里已含 "A、" 前缀，去掉避免和左侧圆圈重复
    var displayText: String {
        var text = optionText.trimmingCharacters(in: .whitespacesAndNewlines)
        let label = label.trimmingCharacters(in: .whitespaces)
        guard !label.isEmpty, text.hasPrefix(label) else { return text }
        text = String(text.dropFirst(label.count))
        let separators: Set<Character> = ["、", ".", "．", "，", ",", ":", "：", ")", "）", " "]
        while let first = text.first, separators.contains(first) {
            text = String(text.dropFirst())
        }
        return text
    }
}

struct QuestionExplanation: Decodable {
    let preferredAnalysis: String
    let analysisSource: String?
    let preferAiAnalysis: Bool
}

struct QuestionOccurrence: Decodable, Identifiable {
    var id: String { occurrenceId }
    let occurrenceId: String
    let province: String?
    let year: Int?
    let number: Int?
    let paperTitle: String?
}

struct QuestionTag: Decodable {
    let tagType: String
    let tagValue: String
}

struct AnswerSubmission: Encodable {
    let questionId: String
    let selectedAnswer: String
}

struct AnswerFeedback: Decodable {
    let taskId: String
    let questionId: String
    let selectedAnswer: String
    let correctAnswer: String?
    let correct: Bool
    let explanation: String?
    let explanationSource: String?
    let sourceCount: Int
    let sourceSummary: String
    let nextAction: String
    let xpGained: Int
}

struct EssayMaterial: Decodable, Identifiable {
    var id: Int { index }
    let index: Int
    let title: String
    let text: String
    let source: String?
}

struct EssayTaskResponse: Decodable {
    let taskId: String
    let questionId: String?
    let title: String
    let material: String
    let materials: [EssayMaterial]
    let materialIncomplete: Bool
    let prompt: String
    let wordLimit: Int
    let dimensions: [String]
}

struct EssayReviewRequest: Encodable {
    let answerText: String
}

struct EssayReviewResponse: Decodable {
    let reviewId: String
    let taskId: String
    let dimensions: [EssayDimensionReview]
    let missingPoints: [String]
    let rewriteInstruction: String
    let quota: AIQuotaState
}

struct EssayDimensionReview: Decodable, Identifiable {
    var id: String { name }
    let name: String
    let score: Int
    let comment: String
    let action: String
}

struct AIQuotaState: Decodable {
    let feature: String
    let used: Int
    let limit: Int
    let date: String
}

struct EssayRewriteRequest: Encodable {
    let reviewId: String
    let originalAnswer: String
    let rewrittenAnswer: String
}

struct EssayRewriteResponse: Decodable {
    let reviewId: String
    let originalLength: Int
    let rewrittenLength: Int
    let comparison: [String]
    let nextAction: String
}

enum AppLoadState: Equatable {
    case idle
    case loading
    case loaded
    case authRequired
    case profileRequired(String)
    case failed(String)
}

@MainActor
final class AppState: ObservableObject {
    @Published var loadState: AppLoadState = .idle
    @Published var authSession: AuthSession?
    @Published var learnerMemory: LearnerMemory?
    /// learnerMemory 是否已完成首次加载（含失败），用于避免登录后 onboarding 闪屏
    @Published var learnerMemoryLoaded = false
    @Published var profileTargetExam = ""
    @Published var profileTargetRegion = ""
    @Published var profilePositionGroup = ""
    @Published var profileDailyMinutes = 120
    @Published var today: TodayTaskstream?
    @Published var feed: FeedResponse?
    @Published var mockExamRoute: MockExamFlowView.Mode?
    @Published var workbenchPreview: WorkbenchPreview?
    @Published var practiceTask: PracticeTaskResponse?
    @Published var currentQuestionIndex = 0
    @Published var selectedAnswer: String?
    @Published var answerFeedback: AnswerFeedback?
    @Published var celebration: CelebrationPayload?
    private var sessionCorrect = 0
    private var sessionAnswered = 0
    @Published var essayTask: EssayTaskResponse?
    @Published var essayAnswer = ""
    @Published var essayReview: EssayReviewResponse?
    @Published var essayRewrite = ""
    @Published var essayRewriteResult: EssayRewriteResponse?
    @Published var activeOperation: String?
    @Published var errorMessage: String?
    @Published var activeTask: BackendTodayTask?
    @Published var completedTaskIDs: Set<String> = []
    @Published var rankProfile: RankProfile?
    /// 九品配置（用于计算精确晋升进度与阶梯展示）
    @Published var rankConfigs: [RankConfigItem]?
    /// 是否展示品级详情页（点顶部品级徽章触发）
    @Published var showRankDetail: Bool = false
    /// 升迁庆祝弹层（品级提升时由 refreshRank 设置）
    @Published var rankUp: RankUpPayload?
    /// 明日政务预告（会话结束钩子），nil 表示未加载/无数据
    @Published var tomorrowPreview: TomorrowPreview?

    /// 底部板块快捷栏的选中项；nil 表示「全部」（不设默认过滤，避免隐藏其他板块政务）
    @Published var selectedModule: GuzhuModule? = nil

    private let client: APIClient
    private var currentAppleNonce: String?

    init(client: APIClient = APIClient()) {
        self.client = client
        self.authSession = KeychainStore.loadAuthSession()
    }

    var currentQuestion: QuestionDetail? {
        guard let practiceTask, practiceTask.questions.indices.contains(currentQuestionIndex) else {
            return nil
        }
        return practiceTask.questions[currentQuestionIndex]
    }

    var isLastQuestion: Bool {
        guard let practiceTask else { return false }
        return currentQuestionIndex >= practiceTask.questions.count - 1
    }

    var isAuthenticated: Bool {
        authSession != nil
    }

    var hasCompleteProfile: Bool {
        learnerMemory?.profile.onboardingComplete == true
    }

    var canSaveProfile: Bool {
        !profileTargetExam.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !profileTargetRegion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !profilePositionGroup.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var workbenchSections: [WorkbenchSection] {
        workbenchPreview?.sections ?? []
    }

    /// 品级进度 0...1（用 rankConfigs 的当前/下一品级阈值精确计算）
    var rankXpProgress: Double? {
        guard let rank = rankProfile, let configs = rankConfigs else { return nil }
        guard let next = rank.nextRankXpThreshold,
              let cur = configs.first(where: { $0.rankLevel == rank.rankLevel })?.xpThreshold,
              next > cur else { return nil }
        return min(1, max(0, Double(rank.xp - cur) / Double(next - cur)))
    }

    /// 下一品级提示文案，如"距八品县丞 380 XP"
    var rankNextHint: String? {
        guard let rank = rankProfile,
              let nextName = rank.nextRankName,
              let toNext = rank.xpToNext else { return nil }
        return "距\(nextName) \(toNext) XP"
    }

    func openTask(_ task: BackendTodayTask) {
        switch task.kind {
        case "mock_exam":
            // 模考任务走专属沉浸流程，不进带练页（带练页无此任务的取题逻辑）
            mockExamRoute = .takeWeekly
        case "mock_exam_review":
            // startUrl 形如 /api/v1/mock-exams/{sessionId}/review
            let parts = task.startUrl.split(separator: "/")
            if let index = parts.firstIndex(of: "mock-exams"), parts.indices.contains(index + 1) {
                mockExamRoute = .viewReview(sessionId: String(parts[index + 1]))
            }
        default:
            activeTask = task
            Task { await startTask(task) }
        }
    }

    func completeActiveTask() {
        if let task = activeTask {
            completedTaskIDs.insert(task.id)
            celebration = CelebrationPayload(
                correct: sessionCorrect,
                total: sessionAnswered,
                taskTitle: task.title
            )
        }
        sessionCorrect = 0
        sessionAnswered = 0
        activeTask = nil
        // 任务完成后刷新品级：若期间触发升迁，弹出晋升庆祝
        Task { await refreshRank() }
    }

    /// 重新拉取品级档案，检测升迁并触发庆祝弹层。
    /// 注意：品级 level 数值越小品级越高（9=九品最低，1=一品最高），promotion = level 下降。
    func refreshRank() async {
        guard let accessToken = authSession?.accessToken else { return }
        do {
            let profile: RankProfile = try await client.get(
                "/api/v1/rank/profile",
                accessToken: accessToken
            )
            let oldLevel = rankProfile?.rankLevel
            let oldName = rankProfile?.rankName ?? profile.rankName
            rankProfile = profile
            if let oldLevel, profile.rankLevel < oldLevel {
                let unlocked = rankConfigs?
                    .first { $0.rankLevel == profile.rankLevel }?
                    .unlockFeatures ?? []
                rankUp = RankUpPayload(
                    oldRankName: oldName,
                    newRankName: profile.rankName,
                    newRankLevel: profile.rankLevel,
                    unlocked: unlocked
                )
            }
        } catch {
            // 品级刷新失败不影响主流程
        }
    }

    func loadInitialData(force: Bool = false) async {
        guard force || loadState != .loaded else { return }
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        loadState = .loading
        let loadedToday: TodayTaskstream? = try? await client.get(
            "/api/v1/taskstream/today",
            accessToken: accessToken
        )
        self.today = loadedToday
        self.completedTaskIDs = Set(loadedToday?.completedTaskIds ?? [])

        async let workbench: WorkbenchPreview? = try? client.get(
            "/api/v1/workbench/preview",
            accessToken: accessToken
        )
        async let essayTask: EssayTaskResponse? = try? client.get(
            "/api/v1/essay/tasks/today-shenlun-summary-rewrite",
            accessToken: accessToken
        )
        async let rank: RankProfile? = try? client.get(
            "/api/v1/rank/profile",
            accessToken: accessToken
        )
        self.workbenchPreview = await workbench
        self.essayTask = await essayTask
        self.rankProfile = await rank
        self.rankConfigs = try? await client.get(
            "/api/v1/rank/configs",
            accessToken: accessToken
        ) as [RankConfigItem]
        loadState = .loaded

        // Legacy workbench/iPad inputs stay non-blocking. The iPhone home is DocketView.
        self.feed = try? await client.get(
            "/api/v1/feed/today",
            accessToken: accessToken
        ) as FeedResponse
        self.tomorrowPreview = try? await client.get(
            "/api/v1/taskstream/tomorrow",
            accessToken: accessToken
        ) as TomorrowPreview
        if !force,
           loadedToday == nil,
           self.workbenchPreview == nil,
           self.essayTask == nil,
           self.rankProfile == nil {
            do {
                let _: TodayTaskstream = try await client.get(
                    "/api/v1/taskstream/today",
                    accessToken: accessToken
                )
            } catch {
                handleAuthenticatedError(error, profileMessage: "先在「我的」保存备考目标，再生成今日计划。")
            }
        }
    }

    func startTask(_ task: BackendTodayTask) async {
        switch task.kind {
        case "xingce_practice":
            await loadPracticeTask(task)
        case "essay_rewrite":
            if essayTask == nil {
                await loadInitialData()
            }
        default:
            break
        }
    }

    func loadPracticeTask(_ task: BackendTodayTask) async {
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        activeOperation = task.id
        defer { activeOperation = nil }
        do {
            let response: PracticeTaskResponse = try await client.get(
                task.startUrl,
                accessToken: accessToken
            )
            practiceTask = response
            currentQuestionIndex = 0
            selectedAnswer = nil
            answerFeedback = nil
        } catch {
            handleAuthenticatedError(error, profileMessage: "先保存备考目标，再开始练习。")
        }
    }

    func submitPracticeAnswer() async {
        guard let practiceTask, let question = currentQuestion, let selectedAnswer else { return }
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        activeOperation = "practice-answer"
        defer { activeOperation = nil }
        do {
            let feedback: AnswerFeedback = try await client.post(
                "/api/v1/practice/tasks/\(practiceTask.taskId)/answers",
                body: AnswerSubmission(questionId: question.questionId, selectedAnswer: selectedAnswer),
                accessToken: accessToken
            )
            answerFeedback = feedback
            sessionAnswered += 1
            if feedback.correct { sessionCorrect += 1 }
        } catch {
            handleAuthenticatedError(error, profileMessage: "先保存备考目标，再提交答案。")
        }
    }

    func moveToNextQuestion() {
        guard let practiceTask else { return }
        if currentQuestionIndex + 1 < practiceTask.questions.count {
            currentQuestionIndex += 1
            selectedAnswer = nil
            answerFeedback = nil
        }
    }

    func reviewEssay() async {
        guard let essayTask else { return }
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        activeOperation = "essay-review"
        defer { activeOperation = nil }
        do {
            let review: EssayReviewResponse = try await client.post(
                "/api/v1/essay/tasks/\(essayTask.taskId)/review",
                body: EssayReviewRequest(answerText: essayAnswer),
                accessToken: accessToken
            )
            essayReview = review
            essayRewriteResult = nil
        } catch {
            handleAuthenticatedError(error, profileMessage: "先保存备考目标，再使用 AI 批改。")
        }
    }

    func compareEssayRewrite() async {
        guard let essayTask, let essayReview else { return }
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        activeOperation = "essay-rewrite"
        defer { activeOperation = nil }
        do {
            let response: EssayRewriteResponse = try await client.post(
                "/api/v1/essay/tasks/\(essayTask.taskId)/rewrite",
                body: EssayRewriteRequest(
                    reviewId: essayReview.reviewId,
                    originalAnswer: essayAnswer,
                    rewrittenAnswer: essayRewrite
                ),
                accessToken: accessToken
            )
            essayRewriteResult = response
        } catch {
            handleAuthenticatedError(error, profileMessage: "先保存备考目标，再对比重写稿。")
        }
    }

    func prepareAppleSignIn(_ request: ASAuthorizationAppleIDRequest) {
        guard let nonce = AppState.makeRandomNonce() else {
            errorMessage = "无法生成 Apple 登录 nonce。"
            return
        }
        currentAppleNonce = nonce
        request.requestedScopes = [.fullName, .email]
        request.nonce = AppState.sha256(nonce)
    }

    func completeAppleSignIn(_ result: Result<ASAuthorization, Error>) async {
        activeOperation = "apple-sign-in"
        defer { activeOperation = nil }

        do {
            let authorization = try result.get()
            guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                  let identityTokenData = credential.identityToken,
                  let identityToken = String(data: identityTokenData, encoding: .utf8),
                  let authorizationCodeData = credential.authorizationCode,
                  let authorizationCode = String(data: authorizationCodeData, encoding: .utf8),
                  let nonce = currentAppleNonce
            else {
                throw APIClientError.appleCredentialInvalid
            }

            let request = AppleLoginRequest(
                identityToken: identityToken,
                authorizationCode: authorizationCode,
                nonce: nonce,
                deviceId: try AppState.deviceID(),
                displayName: AppState.displayName(from: credential.fullName)
            )
            let session: AuthSession = try await client.post("/api/v1/auth/apple", body: request)
            try KeychainStore.saveAuthSession(session)
            authSession = session
            currentAppleNonce = nil
            await loadLearnerMemory()
            await loadInitialData(force: true)
        } catch {
            currentAppleNonce = nil
            errorMessage = error.localizedDescription
        }
    }

    func loadLearnerMemory() async {
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            learnerMemoryLoaded = true
            return
        }
        do {
            let memory: LearnerMemory = try await client.get(
                "/api/v1/learner/memory",
                accessToken: accessToken
            )
            learnerMemory = memory
            learnerMemoryLoaded = true
            profileTargetExam = memory.profile.targetExam ?? ""
            profileTargetRegion = memory.profile.targetRegion ?? ""
            profilePositionGroup = memory.profile.positionGroup ?? ""
            profileDailyMinutes = memory.profile.dailyMinutes ?? 120
        } catch {
            handleAuthenticatedError(error, profileMessage: "无法读取学习记忆。")
            learnerMemoryLoaded = true
        }
    }

    func saveLearnerProfile() async {
        guard let accessToken = authSession?.accessToken else {
            loadState = .authRequired
            return
        }
        guard canSaveProfile else {
            errorMessage = "目标考试、地区和岗位方向不能为空。"
            return
        }
        activeOperation = "profile-save"
        defer { activeOperation = nil }
        do {
            let profile: LearnerProfile = try await client.put(
                "/api/v1/learner/profile",
                body: LearnerProfileUpdate(
                    targetExam: profileTargetExam.trimmingCharacters(in: .whitespacesAndNewlines),
                    targetRegion: profileTargetRegion.trimmingCharacters(in: .whitespacesAndNewlines),
                    positionGroup: profilePositionGroup.trimmingCharacters(in: .whitespacesAndNewlines),
                    dailyMinutes: profileDailyMinutes
                ),
                accessToken: accessToken
            )
            learnerMemory = LearnerMemory(
                profile: profile,
                summary: learnerMemory?.summary ?? LearnerMemorySummary(
                    practiceAnswers: 0,
                    wrongAnswers: 0,
                    essayAttempts: 0
                )
            )
            await loadInitialData(force: true)
        } catch {
            handleAuthenticatedError(error, profileMessage: "无法保存备考目标。")
        }
    }

    func signOut() async {
        guard let accessToken = authSession?.accessToken else {
            clearAuthSession()
            return
        }
        do {
            try await client.postEmpty("/api/v1/auth/logout", accessToken: accessToken)
            clearAuthSession()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func handleAuthenticatedError(_ error: Error, profileMessage: String) {
        if case APIClientError.httpStatus(let status, _) = error {
            if status == 401 {
                clearAuthSession()
                loadState = .authRequired
                return
            }
            if status == 409 {
                loadState = .profileRequired(profileMessage)
                errorMessage = profileMessage
                return
            }
            if status == 429 {
                let quotaMessage = "今日免费 AI 额度已用完，可在「我的」查看 AI Pro 权益。"
                loadState = .failed(quotaMessage)
                errorMessage = quotaMessage
                return
            }
        }
        loadState = .failed(error.localizedDescription)
        errorMessage = error.localizedDescription
    }

    private func clearAuthSession() {
        authSession = nil
        learnerMemory = nil
        learnerMemoryLoaded = false
        today = nil
        workbenchPreview = nil
        practiceTask = nil
        currentQuestionIndex = 0
        selectedAnswer = nil
        answerFeedback = nil
        celebration = nil
        essayTask = nil
        essayAnswer = ""
        essayReview = nil
        essayRewrite = ""
        essayRewriteResult = nil
        feed = nil
        mockExamRoute = nil
        tomorrowPreview = nil
        profileTargetExam = ""
        profileTargetRegion = ""
        profilePositionGroup = ""
        activeTask = nil
        completedTaskIDs = []
        sessionCorrect = 0
        sessionAnswered = 0
        activeOperation = nil
        errorMessage = nil
        rankProfile = nil
        rankConfigs = nil
        showRankDetail = false
        rankUp = nil
        loadState = .authRequired
        KeychainStore.deleteAuthSession()
    }

    private static func deviceID() throws -> String {
        guard let identifier = UIDevice.current.identifierForVendor?.uuidString else {
            throw APIClientError.deviceIdentifierUnavailable
        }
        return "ios-\(identifier)"
    }

    private static func displayName(from components: PersonNameComponents?) -> String? {
        guard let components else { return nil }
        let name = PersonNameComponentsFormatter().string(from: components)
        return name.isEmpty ? nil : name
    }

    private static func makeRandomNonce(length: Int = 32) -> String? {
        precondition(length > 0)
        let charset = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var randomBytes = [UInt8](repeating: 0, count: length)
        let status = SecRandomCopyBytes(kSecRandomDefault, randomBytes.count, &randomBytes)
        guard status == errSecSuccess else { return nil }
        return String(randomBytes.map { charset[Int($0) % charset.count] })
    }

    private static func sha256(_ input: String) -> String {
        let digest = SHA256.hash(data: Data(input.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
