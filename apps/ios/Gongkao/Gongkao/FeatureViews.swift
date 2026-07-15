import AuthenticationServices
import SwiftUI

private struct ScrollOffsetKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

struct TodayView: View {
    @EnvironmentObject private var appState: AppState
    @State private var askContext: AskContext?
    @State private var showCapture = false
    @State private var scrollTopY: CGFloat = 0

    /// iPhone 顶部问候语：保持陪伴感，不回到旧官场主叙事。
    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<12: return "早，鹿鸣把今日上岸路线排好了"
        case 12..<18: return "午后好，先把今日路线推进一段"
        default: return "晚上好，今天还可以稳稳推进一段"
        }
    }

    struct AskContext: Identifiable, Hashable {
        let nodeId: String?
        let prefill: String?
        var id: String { "\(nodeId ?? "-")|\(prefill ?? "-")" }
    }

    /// 已加载时始终显示顶部分类标签；任务卡按后端 moduleName 过滤。
    private var showCategoryBar: Bool {
        if case .loaded = appState.loadState {
            return true
        }
        return false
    }

    /// 按选中板块过滤今日任务；meta 卡片只作为补充提醒，不抢首屏路线。
    private var visibleCards: [FeedCard] {
        guard let feed = appState.feed else { return [] }
        guard let module = appState.selectedModule else { return feed.cards }
        return feed.cards.filter { card in
            guard isTaskCard(card) else { return true }
            return module.matches(moduleName: card.payload?.moduleName)
        }
    }

    private var visibleTasks: [BackendTodayTask] {
        guard let today = appState.today else { return [] }
        return today.tasks.filter { $0.isVisible(for: appState.selectedModule) }
    }

    private func isTaskCard(_ card: FeedCard) -> Bool {
        switch card.type {
        case .taskPractice, .taskEssay, .taskMethodCard: return true
        default: return false
        }
    }

    private var supplementalCards: [FeedCard] {
        visibleCards.filter { card in
            switch card.type {
            case .goalGap, .coachBrief, .taskPractice, .taskEssay, .taskMethodCard:
                return false
            default:
                return true
            }
        }
    }

    /// 顶部分类标签的显现强度：初始（顶部）变淡，下滑后加深显现，保证第一眼清爽
    private var categoryBarOpacity: Double {
        let t = min(1, max(0, -scrollTopY / 48))
        return 0.35 + 0.65 * t
    }

    /// 安静的一行问候（替代原 title2 粗体大标题，避免顶部信息窒息）
    private var greetingLine: some View {
        Text(greeting)
            .font(.callout.weight(.medium))
            .foregroundStyle(GuzhuPalette.faintInk)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    var body: some View {
        VStack(spacing: 0) {
            if showCategoryBar {
                CategoryTabBar(selectedModule: $appState.selectedModule)
                    .opacity(categoryBarOpacity)
                    .padding(.top, 6)
            }
            ScrollView {
                GeometryReader { geo in
                    Color.clear
                        .preference(
                            key: ScrollOffsetKey.self,
                            value: geo.frame(in: .named("todayScroll")).minY
                        )
                }
                .frame(height: 0)
                VStack(alignment: .leading, spacing: 24) {
                switch appState.loadState {
                case .idle, .loading:
                    LoadingPanel(title: "正在读取今日计划")
                case .authRequired:
                    AuthRequiredPanel()
                case .profileRequired(let message):
                    ProfileRequiredPanel(message: message)
                case .failed(let message):
                    ErrorBanner(message: message) {
                        Task { await appState.loadInitialData() }
                }
                case .loaded:
                    greetingLine
                    if let today = appState.today {
                        LumingTodayRouteView(
                            plan: today,
                            tasks: visibleTasks,
                            completedIDs: appState.completedTaskIDs
                        ) { task in
                            appState.openTask(task)
                        }

                        if !supplementalCards.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Label("补充提醒", systemImage: "bell.badge")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundStyle(DaziPalette.ink)
                                FeedCardList(
                                    cards: supplementalCards,
                                    onOpenTask: { taskId in
                                        if let task = appState.today?.tasks.first(where: { $0.id == taskId }) {
                                            appState.openTask(task)
                                        }
                                    },
                                    onStartMock: { appState.mockExamRoute = .takeWeekly },
                                    onOpenMockReview: { sessionId in
                                        appState.mockExamRoute = .viewReview(sessionId: sessionId)
                                    },
                                    onAskCompanion: { nodeId, prefill in
                                        askContext = AskContext(nodeId: nodeId, prefill: prefill)
                                    },
                                    onCapture: { showCapture = true }
                                )
                            }
                        }
                    } else if let feed = appState.feed, !feed.cards.isEmpty {
                        FeedCardList(
                            cards: visibleCards,
                            onOpenTask: { taskId in
                                if let task = appState.today?.tasks.first(where: { $0.id == taskId }) {
                                    appState.openTask(task)
                                }
                            },
                            onStartMock: { appState.mockExamRoute = .takeWeekly },
                            onOpenMockReview: { sessionId in
                                appState.mockExamRoute = .viewReview(sessionId: sessionId)
                            },
                            onAskCompanion: { nodeId, prefill in
                                askContext = AskContext(nodeId: nodeId, prefill: prefill)
                            },
                            onCapture: { showCapture = true }
                        )
                    }
                }
            }
            .contentMargins(maxWidth: 920)
            }
            .coordinateSpace(name: "todayScroll")
        }
        .background(DaziPalette.paper)
        .navigationBarTitleDisplayMode(.inline)
        .onPreferenceChange(ScrollOffsetKey.self) { scrollTopY = $0 }
        .fullScreenCover(item: $appState.activeTask) { task in
            // 沉浸式做题：全屏接管，无标签栏无系统导航干扰
            NavigationStack {
                TaskExecutionView(task: task)
            }
        }
        .navigationDestination(item: $askContext) { context in
            CompanionAskView(
                accessToken: appState.authSession?.accessToken ?? "",
                nodeId: context.nodeId,
                prefill: context.prefill,
                rankName: appState.rankProfile?.rankName
            )
        }
        .sheet(isPresented: $showCapture) {
            CaptureQuestionView(accessToken: appState.authSession?.accessToken ?? "")
                .environmentObject(appState)
        }
        .safeAreaInset(edge: .bottom) {
            if case .loaded = appState.loadState, let today = appState.today {
                TodayCTABar(plan: today)
                    .padding(.horizontal, 20)
                    .padding(.top, 4)
            }
        }
    }
}

private struct TodayCTABar: View {
    @EnvironmentObject private var appState: AppState
    let plan: TodayTaskstream

    private var visibleTasks: [BackendTodayTask] {
        plan.tasks.filter { $0.isVisible(for: appState.selectedModule) }
    }

    private var nextTask: BackendTodayTask? {
        visibleTasks.first { !appState.completedTaskIDs.contains($0.id) }
    }

    var body: some View {
        if visibleTasks.isEmpty, let selectedModule = appState.selectedModule {
            PrimaryCTABar(
                title: "今日暂无\(selectedModule.shortName)任务",
                subtitle: "今日安排仍以分差和弱项为准。",
                systemImage: selectedModule.symbol
            )
        } else if let task = nextTask {
            let index = (plan.tasks.firstIndex { $0.id == task.id } ?? 0) + 1
            let verb = appState.completedTaskIDs.isEmpty ? "开始" : "继续"
            PrimaryCTABar(
                title: "\(verb)第 \(index) 项 · \(task.title)",
                subtitle: "\(task.estimatedMinutes) 分钟 · \(task.subtitle)",
                systemImage: "play.fill"
            ) {
                appState.openTask(task)
            }
        } else {
            VStack(spacing: 10) {
                let completionTitle = appState.selectedModule.map { "\($0.shortName)任务已完成" } ?? "今日路线全部完成"
                PrimaryCTABar(
                    title: completionTitle,
                    subtitle: plan.primaryAction.subtitle,
                    systemImage: "checkmark.seal.fill"
                )
                if let tomorrow = appState.tomorrowPreview {
                    TomorrowPreviewCard(preview: tomorrow)
                }
            }
        }
    }
}

private struct ModuleEmptyPanel: View {
    let module: GuzhuModule

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("今日暂无\(module.shortName)任务", systemImage: module.symbol)
                .font(.headline)
                .foregroundStyle(module.color)
            Text("今日安排仍以分差和弱项为准。")
                .font(.subheadline)
                .foregroundStyle(GuzhuPalette.faintInk)
        }
        .panelStyle()
    }
}

private extension BackendTodayTask {
    var participatesInModuleFilter: Bool {
        switch kind {
        case "xingce_practice", "essay_rewrite", "method_card":
            return true
        default:
            return false
        }
    }

    func isVisible(for selectedModule: GuzhuModule?) -> Bool {
        guard let selectedModule else { return true }
        guard participatesInModuleFilter else { return true }
        return selectedModule.matches(moduleName: moduleName)
    }
}

struct WorkbenchView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("学习工作台")
                        .font(.largeTitle.weight(.bold))
                    Text("岗位报告、错因复盘、申论重写和模考分析都会归档在这里。")
                        .foregroundStyle(.secondary)
                }

                if appState.workbenchSections.isEmpty {
                    EmptyWorkbenchPanel()
                } else {
                    ForEach(appState.workbenchSections) { section in
                        WorkbenchSectionView(section: section)
                    }
                }

                SourceBasePanel(sourceBase: appState.workbenchPreview?.sourceBase)
            }
            .contentMargins(maxWidth: 980)
        }
        .navigationTitle("工作台")
        .background(DaziPalette.paper)
        .task {
            await appState.loadInitialData(force: true)
        }
    }
}

struct EmptyWorkbenchPanel: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("暂无案牍", systemImage: "tray")
                .font(.headline)
            Text("批完一次卷宗或呈上一份奏折后，这里会生成可调阅的真实复盘。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .panelStyle()
    }
}

struct WorkbenchSectionView: View {
    @EnvironmentObject private var appState: AppState
    let section: WorkbenchSection

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(section.title)
                .font(.title3.weight(.semibold))

            VStack(spacing: 10) {
                ForEach(section.objects) { object in
                    NavigationLink {
                        WorkbenchObjectDetailView(
                            object: object,
                            accessToken: appState.authSession?.accessToken ?? ""
                        )
                    } label: {
                        WorkbenchObjectRow(object: object)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

struct WorkbenchObjectRow: View {
    let object: WorkbenchObject

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: object.symbol)
                .font(.title3)
                .foregroundStyle(object.color)
                .frame(width: 38, height: 38)
                .background(object.color.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(object.title)
                        .font(.headline)
                    Text(object.kind)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(object.color)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(object.color.opacity(0.12), in: Capsule())
                }
                Text(object.subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Label("\(object.sourceCount)", systemImage: "doc.text")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)

            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .panelStyle()
    }
}

struct EssayReviewCard: View {
    let review: EssayReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("搭子的五维点评", systemImage: "sparkles")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Text("今日 \(review.quota.used)/\(review.quota.limit) 次")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 10) {
                ForEach(review.dimensions) { item in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(item.name)
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(DaziPalette.ink)
                                Spacer()
                                ScoreDots(score: item.score, total: 5)
                            }
                            Text(item.comment)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .lineSpacing(2)
                            Text(item.action)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(DaziPalette.brand)
                        }
                    }
                    if item.id != review.dimensions.last?.id {
                        Divider().overlay(DaziPalette.cardStroke)
                    }
                }
            }
            .padding(14)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
            )

            if !review.missingPoints.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(DaziPalette.alert)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("可能缺失的要点")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DaziPalette.alert)
                        Text(review.missingPoints.joined(separator: "、"))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    DaziPalette.alert.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                )
            }

            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "pencil.line")
                    .font(.caption)
                    .foregroundStyle(DaziPalette.brand)
                Text(review.rewriteInstruction)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(DaziPalette.brand)
                    .lineSpacing(2)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                DaziPalette.brand.opacity(0.08),
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
        }
    }
}

struct ScoreDots: View {
    let score: Int
    let total: Int

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<total, id: \.self) { i in
                Circle()
                    .fill(
                        i < score
                            ? DaziPalette.brand
                            : DaziPalette.cardStroke
                    )
                    .frame(width: 7, height: 7)
            }
        }
    }
}

struct EssayRewriteResultView: View {
    let result: EssayRewriteResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("重写对比", systemImage: "checkmark.seal.fill")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.positive)
                Spacer()
                Text("\(result.originalLength) → \(result.rewrittenLength) 字")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach(result.comparison, id: \.self) { item in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(DaziPalette.positive)
                        Text(item)
                            .font(.subheadline)
                            .foregroundStyle(DaziPalette.ink)
                    }
                }
            }

            Text(result.nextAction)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(DaziPalette.brand)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    DaziPalette.brand.opacity(0.06),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                )
        }
        .padding(16)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }
}

struct SourceBasePanel: View {
    let sourceBase: WorkbenchSourceBase?

    var body: some View {
        if let sourceBase {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 12) {
                MetricTile(
                    title: "题库",
                    value: sourceBase.canonicalQuestions.formatted(),
                    detail: "压实题目"
                )
                MetricTile(
                    title: "知识片段",
                    value: sourceBase.knowledgeChunks.formatted(),
                    detail: "可检索材料"
                )
                MetricTile(
                    title: "岗位记录",
                    value: sourceBase.positionRecords.formatted(),
                    detail: "自持来源"
                )
            }
        }
    }
}

struct ProfileView: View {
    @EnvironmentObject private var appState: AppState
    @State private var editingGoal = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let session = appState.authSession {
                    AccountPanel(session: session)
                    GoalSummaryPanel(editingGoal: $editingGoal)
                    StudyStatsPanel()
                } else {
                    SignInWithAppleButton(.signIn) { request in
                        appState.prepareAppleSignIn(request)
                    } onCompletion: { result in
                        Task { await appState.completeAppleSignIn(result) }
                    }
                    .signInWithAppleButtonStyle(.black)
                    .frame(height: 48)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }

                CommercePaywallPanel(
                    accessToken: appState.authSession?.accessToken,
                    userId: appState.authSession?.user.userId
                )

                #if DEBUG
                NavigationLink {
                    FeedPreviewView()
                } label: {
                    Label("Feed 渲染器预览（开发）", systemImage: "hammer")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                NavigationLink {
                    DesignSystemPreview()
                } label: {
                    Label("设计系统预览（开发）", systemImage: "paintpalette")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                #endif
            }
            .contentMargins(maxWidth: 760)
        }
        .navigationTitle("我的")
        .background(DaziPalette.paper)
        .sheet(isPresented: $editingGoal) {
            OnboardingFlowView(
                accessToken: appState.authSession?.accessToken ?? "",
                onDone: { editingGoal = false }
            )
            .environmentObject(appState)
        }
        .task {
            if appState.isAuthenticated, appState.learnerMemory == nil {
                await appState.loadLearnerMemory()
            }
        }
    }
}

private struct AccountPanel: View {
    @EnvironmentObject private var appState: AppState
    let session: AuthSession

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "person.crop.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.indigo)
                VStack(alignment: .leading, spacing: 3) {
                    Text(session.user.displayName ?? "Apple 账号")
                        .font(.headline)
                    if let email = session.user.email {
                        Text(email)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button {
                    Task { await appState.signOut() }
                } label: {
                    Label("退出", systemImage: "rectangle.portrait.and.arrow.right")
                }
                .buttonStyle(.bordered)
            }
        }
        .panelStyle()
    }
}

private struct GoalSummaryPanel: View {
    @EnvironmentObject private var appState: AppState
    @Binding var editingGoal: Bool

    private var profile: LearnerProfile? { appState.learnerMemory?.profile }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("备考目标", systemImage: "scope")
                    .font(.headline)
                Spacer()
                Button("修改") { editingGoal = true }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.brand)
                    .buttonStyle(.plain)
            }

            if let profile, profile.onboardingComplete {
                GoalInfoRow(label: "目标岗位", value: positionLabel(profile))
                if let score = profile.targetScore {
                    GoalInfoRow(label: "目标总分", value: String(format: "%.1f 分", score))
                }
                GoalInfoRow(label: "备考状态", value: profile.studyMode == "fulltime" ? "全职备考" : "在职备考")
                if let minutes = profile.dailyMinutes {
                    GoalInfoRow(label: "每日时长", value: String(format: "%.1f 小时（%d 分钟）", Double(minutes) / 60, minutes))
                }
                GoalInfoRow(label: "能力基线", value: baselineLabel(profile))
            } else {
                Text("还没有完成备考目标设置。设置后搭子才能按你的岗位和水平排计划。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Button {
                    editingGoal = true
                } label: {
                    Label("去设置", systemImage: "arrow.right.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(DaziPalette.brand)
            }
        }
        .panelStyle()
    }

    private func positionLabel(_ profile: LearnerProfile) -> String {
        if profile.targetPositionId != nil {
            return "已选定岗位"
        }
        return "未选岗（国考裸分目标）"
    }

    private func baselineLabel(_ profile: LearnerProfile) -> String {
        switch profile.baselineSource {
        case "self_report":
            if let score = profile.baselineXingceScore {
                return String(format: "自报 行测 %.1f", score)
            }
            return "自报分数"
        case "placement_test":
            if let score = profile.baselineXingceScore {
                return String(format: "摸底 预估 %.1f", score)
            }
            return "摸底测验"
        default:
            return "未设置（做题后自动校准）"
        }
    }
}

private struct GoalInfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .frame(width: 76, alignment: .leading)
            Text(value)
                .font(.subheadline.weight(.medium))
                .monospacedDigit()
            Spacer()
        }
    }
}

private struct StudyStatsPanel: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        if let summary = appState.learnerMemory?.summary {
            VStack(alignment: .leading, spacing: 12) {
                Label("学习数据", systemImage: "chart.bar.fill")
                    .font(.headline)
                HStack(spacing: 0) {
                    StatCell(value: summary.practiceAnswers, label: "累计做题")
                    StatCell(value: summary.wrongAnswers, label: "错题")
                    StatCell(value: summary.essayAttempts, label: "申论练习")
                }
            }
            .panelStyle()
        }
    }
}

private struct StatCell: View {
    let value: Int
    let label: String

    var body: some View {
        VStack(spacing: 4) {
            Text("\(value)")
                .font(.title3.bold().monospacedDigit())
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

struct TrackBadge: View {
    let title: String

    init(track: StudyTrack) {
        self.title = track.rawValue
    }

    init(track: String) {
        self.title = track
    }

    private var tint: Color { DaziPalette.track(title) }

    var body: some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(tint.opacity(0.12), in: Capsule())
    }
}

struct LoadingPanel: View {
    let title: String

    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
            Text(title)
                .font(.headline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panelStyle()
    }
}

struct AuthRequiredPanel: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("需要登录", systemImage: "person.badge.key")
                .font(.headline)
                .foregroundStyle(DaziPalette.brand)
            Text("今日计划、作答记录和 AI 配额只绑定到 Apple 账号。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .panelStyle()
    }
}

struct ProfileRequiredPanel: View {
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("需要备考目标", systemImage: "scope")
                .font(.headline)
                .foregroundStyle(DaziPalette.brand)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .panelStyle()
    }
}

struct ErrorBanner: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("读取失败", systemImage: "exclamationmark.triangle")
                .font(.headline)
                .foregroundStyle(DaziPalette.alert)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Button {
                retry()
            } label: {
                Label("重试", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
        }
        .panelStyle()
    }
}

struct MetricTile: View {
    let title: String
    let value: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.monospacedDigit().weight(.bold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panelStyle()
    }
}

extension WorkbenchObject {
    var color: Color {
        switch tint {
        case "green": DaziPalette.positive
        case "orange": DaziPalette.alert
        case "purple": DaziPalette.accentPurple
        case "red": DaziPalette.alert
        case "indigo": DaziPalette.brand
        default: DaziPalette.accentBlue
        }
    }
}

extension View {
    func panelStyle() -> some View {
        self
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(GuzhuPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(GuzhuPalette.faintInk.opacity(0.25), lineWidth: 1)
            )
    }

    func contentMargins(maxWidth: CGFloat) -> some View {
        self
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
            .frame(maxWidth: maxWidth, alignment: .leading)
    }
}

#Preview {
    TodayView()
        .environmentObject(AppState())
}
