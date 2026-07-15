import SwiftUI

// MARK: - 契约模型（对齐 backend/app/domains/mock_exam/schemas.py）

struct MockExamSession: Decodable {
    let sessionId: String
    let status: String
    let title: String
    let durationMinutes: Int
    let questionCount: Int
    let blueprint: [MockModuleBlueprint]
    let questions: [QuestionDetail]
    let sourceGrounding: String
}

struct MockModuleBlueprint: Decodable {
    let moduleName: String
    let questionCount: Int
}

struct MockExamReview: Decodable {
    let sessionId: String
    let estimatedScore: Double
    let estimateSource: String
    let submittedAt: String
    let viewed: Bool
    let modules: [MockModuleReview]
    let wrongClusters: [MockWrongCluster]
    let nextWeekReason: String
}

struct MockModuleReview: Decodable {
    let moduleName: String
    let correct: Int
    let total: Int
    let accuracy: Double
    let changeFromPrevious: Double?
}

struct MockWrongCluster: Decodable {
    let nodeId: String
    let displayPath: String
    let wrongCount: Int
    let methodCardIds: [String]
    let fixAction: String
}

private struct MockSubmitRequest: Encodable {
    struct Answer: Encodable {
        let questionId: String
        let selectedAnswer: String
    }
    let answers: [Answer]
}

private struct MockSubmitResponse: Decodable {
    let sessionId: String
    let status: String
    let review: MockExamReview
}

// MARK: - 状态机

@MainActor
final class MockExamStore: ObservableObject {
    enum Phase {
        case loading
        case ready(MockExamSession)
        case review(MockExamReview)
        case failed(String)
    }

    @Published var phase: Phase = .loading
    @Published var answers: [String: String] = [:]
    @Published var currentIndex = 0
    @Published var secondsLeft = 0
    @Published var submitting = false
    @Published var confirmSubmit = false
    @Published var showScratchPad = false

    private let client = APIClient()
    private let accessToken: String
    private var timer: Timer?

    init(accessToken: String) {
        self.accessToken = accessToken
    }

    @MainActor deinit {
        // 用户直接 dismiss（不交卷）时，store 随视图销毁；确保倒计时 Timer 不泄漏
        timer?.invalidate()
        timer = nil
    }

    func startWeekly() async {
        phase = .loading
        do {
            let session: MockExamSession = try await client.post(
                "/api/v1/mock-exams/weekly",
                body: EmptyBody(),
                accessToken: accessToken
            )
            secondsLeft = session.durationMinutes * 60
            phase = .ready(session)
            startTimer()
        } catch {
            phase = .failed("模考创建失败：\(error.localizedDescription)")
        }
    }

    func openReview(sessionId: String) async {
        phase = .loading
        do {
            let review: MockExamReview = try await client.post(
                "/api/v1/mock-exams/\(sessionId)/review/view",
                body: EmptyBody(),
                accessToken: accessToken
            )
            phase = .review(review)
        } catch {
            phase = .failed("复盘加载失败：\(error.localizedDescription)")
        }
    }

    func submit() async {
        guard case .ready(let session) = phase, !submitting else { return }
        submitting = true
        defer { submitting = false }
        stopTimer()
        do {
            // 后端要求所有 assigned 题都有答案；未答的补占位 "_"（按错题计分）
            let payload = MockSubmitRequest(
                answers: session.questions.map { q in
                    let ans = answers[q.questionId] ?? "_"
                    return .init(questionId: q.questionId, selectedAnswer: ans)
                }
            )
            let response: MockSubmitResponse = try await client.post(
                "/api/v1/mock-exams/\(session.sessionId)/submit",
                body: payload,
                accessToken: accessToken
            )
            await openReview(sessionId: response.sessionId)
        } catch {
            phase = .failed("交卷失败：\(error.localizedDescription)")
        }
    }

    private func startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if self.secondsLeft > 0 {
                    self.secondsLeft -= 1
                } else {
                    self.stopTimer()
                    await self.submit()
                }
            }
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    var clockLabel: String {
        String(format: "%02d:%02d:%02d", secondsLeft / 3600, secondsLeft / 60 % 60, secondsLeft % 60)
    }
}

// MARK: - 模考容器（全屏沉浸）

struct MockExamFlowView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var store: MockExamStore
    private let mode: Mode

    enum Mode {
        case takeWeekly
        case viewReview(sessionId: String)
    }

    init(mode: Mode, accessToken: String) {
        self.mode = mode
        _store = StateObject(wrappedValue: MockExamStore(accessToken: accessToken))
    }
}

extension MockExamFlowView.Mode: Identifiable {
    var id: String {
        switch self {
        case .takeWeekly: "take-weekly"
        case .viewReview(let sessionId): "review-\(sessionId)"
        }
    }
}

extension MockExamFlowView {

    var body: some View {
        NavigationStack {
            Group {
                switch store.phase {
                case .loading:
                    MockExamLoadingView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .ready(let session):
                    MockExamPaperView(session: session, store: store) {
                        dismiss()
                    }
                case .review(let review):
                    MockReviewView(review: review) {
                        Task { await appState.loadInitialData() }
                        dismiss()
                    }
                case .failed(let message):
                    VStack(spacing: 12) {
                        ErrorBanner(message: message) {
                            Task { await load() }
                        }
                        Button("退出") { dismiss() }
                            .foregroundStyle(.secondary)
                    }
                    .padding(20)
                }
            }
            .background(DaziPalette.paper)
            .navigationBarTitleDisplayMode(.inline)
        }
        .interactiveDismissDisabled()
        .task { await load() }
        .confirmationDialog(
            "确认交卷？",
            isPresented: $store.confirmSubmit,
            titleVisibility: .visible
        ) {
            Button("交卷并看复盘", role: .destructive) {
                Task { await store.submit() }
            }
            Button("继续作答", role: .cancel) {}
        } message: {
            Text(unansweredMessage)
        }
    }

    private func load() async {
        switch mode {
        case .takeWeekly:
            await store.startWeekly()
        case .viewReview(let sessionId):
            await store.openReview(sessionId: sessionId)
        }
    }

    private var unansweredMessage: String {
        guard case .ready(let session) = store.phase else { return "" }
        let unanswered = session.questions.count - store.answers.count
        return unanswered > 0 ? "还有 \(unanswered) 题未作答，未答题按错误计分。" : "全部题目已作答。"
    }
}

// MARK: - Loading 页（带情绪价值）

private struct MockExamLoadingView: View {
    @State private var appear = false
    @State private var pulse = false

    private let slogans = [
        "距离上岸，又近了一步",
        "每一道题，都是向上的台阶",
        "稳住节奏，你比想象中更强",
        "今天的努力，是明天的底气",
        "认真做完，就是胜利"
    ]

    @State private var sloganIndex = 0
    @State private var sloganTimer: Timer?

    var body: some View {
        VStack(spacing: 28) {
            Spacer()

            ZStack {
                Circle()
                    .fill(
                        DaziGradient.brandLinear.opacity(0.08)
                    )
                    .frame(width: 160, height: 160)
                    .scaleEffect(pulse ? 1.15 : 1.0)
                    .opacity(pulse ? 0.6 : 1.0)

                Image("Logo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 80, height: 80)
                    .scaleEffect(appear ? 1.0 : 0.8)
                    .opacity(appear ? 1.0 : 0)
            }

            VStack(spacing: 8) {
                Text("正在生成本周模考卷")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(DaziPalette.ink)

                Text(slogans[sloganIndex])
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .opacity(appear ? 1 : 0)
                    .animation(.easeInOut(duration: 0.5).delay(0.3), value: appear)
                    .id(sloganIndex)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            HStack(spacing: 6) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(DaziPalette.brand)
                        .frame(width: 7, height: 7)
                        .opacity(pulse ? 0.3 : 1.0)
                        .animation(
                            .easeInOut(duration: 0.8)
                            .repeatForever()
                            .delay(Double(i) * 0.25),
                            value: pulse
                        )
                }
            }

            Spacer()

            Text("基于你本周的错题和薄弱点定制")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            ZStack {
                DaziPalette.paper
                RadialGradient(
                    colors: [DaziPalette.accentPurple.opacity(0.06), .clear],
                    center: .topTrailing,
                    startRadius: 50,
                    endRadius: 400
                )
                RadialGradient(
                    colors: [DaziPalette.brand.opacity(0.05), .clear],
                    center: .bottomLeading,
                    startRadius: 50,
                    endRadius: 400
                )
            }
        )
        .onAppear {
            withAnimation(.spring(response: 0.6, dampingFraction: 0.7).delay(0.1)) {
                appear = true
            }
            withAnimation(.easeInOut(duration: 1.6).repeatForever(autoreverses: true)) {
                pulse.toggle()
            }
            rotateSlogans()
        }
        .onDisappear {
            sloganTimer?.invalidate()
            sloganTimer = nil
        }
    }

    private func rotateSlogans() {
        sloganTimer = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: true) { _ in
            Task { @MainActor in
                withAnimation(.easeInOut(duration: 0.4)) {
                    sloganIndex = (sloganIndex + 1) % slogans.count
                }
            }
        }
    }
}

// MARK: - 答卷视图（沉浸式 + 双栏资料分析）

private struct MockExamPaperView: View {
    let session: MockExamSession
    @ObservedObject var store: MockExamStore
    let onExit: () -> Void
    @State private var showExitConfirm = false
    @State private var showQuestionNav = false

    private var question: QuestionDetail? {
        guard session.questions.indices.contains(store.currentIndex) else { return nil }
        return session.questions[store.currentIndex]
    }

    private var isMaterialBased: Bool {
        question?.moduleName?.contains("资料分析") == true
    }

    var body: some View {
        VStack(spacing: 0) {
            topStatusBar

            if let question {
                if isMaterialBased {
                    materialBasedLayout(question)
                } else {
                    singleColumnLayout(question)
                }
            }

            bottomNavBar
        }
        .background(DaziPalette.paper)
        .overlay {
            if store.showScratchPad {
                ScratchOverlayView(isPresented: $store.showScratchPad)
            }
        }
        .confirmationDialog(
            "确认退出？",
            isPresented: $showExitConfirm,
            titleVisibility: .visible
        ) {
            Button("退出模考", role: .destructive) {
                onExit()
            }
            Button("继续作答", role: .cancel) {}
        } message: {
            Text("退出后答题进度不会保留，确定要退出吗？")
        }
    }

    // MARK: 顶部状态栏
    private var topStatusBar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Button {
                    showExitConfirm = true
                } label: {
                    Image(systemName: "xmark")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 36, height: 36)
                        .background(DaziPalette.card, in: Circle())
                }
                .accessibilityLabel("退出模考")

                VStack(alignment: .leading, spacing: 2) {
                    Text(session.title)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                    Text(progressLabel)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }

                Spacer()

                clockDisplay
                    .frame(maxWidth: .infinity)

                Spacer()

                Button {
                    store.showScratchPad = true
                } label: {
                    Image(systemName: "pencil.and.scribble")
                        .foregroundStyle(.secondary)
                        .frame(width: 36, height: 36)
                        .background(DaziPalette.card, in: Circle())
                }
                .accessibilityLabel("草稿纸")

                Button {
                    store.confirmSubmit = true
                } label: {
                    Text("交卷")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(
                            DaziPalette.highlightGradient,
                            in: Capsule()
                        )
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(DaziPalette.paper)
    }

    private var clockDisplay: some View {
        let urgent = store.secondsLeft < 600
        return HStack(spacing: 6) {
            Image(systemName: "stopwatch.fill")
                .font(.caption.weight(.semibold))
            Text(store.clockLabel)
                .font(.title3.monospacedDigit().weight(.bold))
        }
        .foregroundStyle(urgent ? DaziPalette.alert : DaziPalette.brand)
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(
            Capsule().fill(
                urgent ? DaziPalette.alert.opacity(0.1) : DaziPalette.brand.opacity(0.08)
            )
        )
    }

    private var progressLabel: String {
        let answered = store.answers.count
        let total = session.questions.count
        return "已答 \(answered) / \(total) 题"
    }

    // MARK: 单栏布局（常识/言语/判断/数量）
    private func singleColumnLayout(_ question: QuestionDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                questionHeader(question)
                questionStemView(question)
                optionList(question)
            }
            .padding(24)
            .contentMargins(maxWidth: 760)
            .padding(.bottom, 100)
        }
    }

    // MARK: 双栏布局（资料分析）
    private func materialBasedLayout(_ question: QuestionDetail) -> some View {
        HStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    Label("给定资料", systemImage: "doc.plaintext.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DaziPalette.track("资料分析"))

                    Text("（材料暂由题干关联，后续接入完整材料）")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .padding(.top, 4)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: 340)
            .background(DaziPalette.card)

            Divider().overlay(DaziPalette.cardStroke)

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    questionHeader(question)
                    questionStemView(question)
                    optionList(question)
                }
                .padding(24)
                .padding(.bottom, 100)
            }
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: 题目头部
    private func questionHeader(_ question: QuestionDetail) -> some View {
        HStack(spacing: 10) {
            Text("第 \(store.currentIndex + 1) 题")
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(.secondary)

            if let module = question.moduleName {
                Text("·")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                TrackBadge(track: module)
            }

            if let type = question.questionType {
                Text("·")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                Text(type)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button {
                showQuestionNav.toggle()
            } label: {
                Label("题号导航", systemImage: "list.number")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(DaziPalette.card, in: Capsule())
            }
        }
    }

    // MARK: 题干
    private func questionStemView(_ question: QuestionDetail) -> some View {
        QuestionRichText(
            plainText: question.stem,
            htmlText: question.stemHtml,
            font: .title2,
            uiFont: .preferredFont(forTextStyle: .title2),
            lineSpacing: 10,
            foregroundColor: DaziPalette.ink,
            textSelectionEnabled: true
        )
    }

    // MARK: 选项列表（大按钮，无重复标签）
    private func optionList(_ question: QuestionDetail) -> some View {
        VStack(spacing: 10) {
            ForEach(question.options) { option in
                Button {
                    store.answers[question.questionId] = option.label
                } label: {
                    HStack(alignment: .center, spacing: 14) {
                        optionLabelCircle(option, questionId: question.questionId)
                        Text(option.displayText)
                            .font(.body)
                            .lineSpacing(6)
                            .multilineTextAlignment(.leading)
                            .foregroundStyle(DaziPalette.ink)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 18)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(optionBackground(option, questionId: question.questionId))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(optionBorder(option, questionId: question.questionId), lineWidth: 1.5)
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func optionLabelCircle(_ option: QuestionOption, questionId: String) -> some View {
        let selected = store.answers[questionId] == option.label
        return Text(option.label)
            .font(.headline.monospaced())
            .frame(width: 36, height: 36)
            .background(
                Circle().fill(selected ? DaziPalette.brand : DaziPalette.card)
            )
            .foregroundStyle(selected ? .white : DaziPalette.ink)
    }

    private func optionBackground(_ option: QuestionOption, questionId: String) -> Color {
        store.answers[questionId] == option.label
            ? DaziPalette.brand.opacity(0.06)
            : DaziPalette.card
    }

    private func optionBorder(_ option: QuestionOption, questionId: String) -> Color {
        store.answers[questionId] == option.label
            ? DaziPalette.brand.opacity(0.6)
            : DaziPalette.cardStroke
    }

    // MARK: 底部导航
    private var bottomNavBar: some View {
        HStack {
            Button {
                store.currentIndex = max(0, store.currentIndex - 1)
            } label: {
                Label("上一题", systemImage: "chevron.left")
                    .font(.subheadline.weight(.medium))
            }
            .disabled(store.currentIndex == 0)
            .opacity(store.currentIndex == 0 ? 0.4 : 1)

            Spacer()

            Button {
                showQuestionNav.toggle()
            } label: {
                Text("\(store.currentIndex + 1) / \(session.questions.count)")
                    .font(.subheadline.monospacedDigit().weight(.medium))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button {
                store.currentIndex = min(session.questions.count - 1, store.currentIndex + 1)
            } label: {
                Label("下一题", systemImage: "chevron.right")
                    .font(.subheadline.weight(.medium))
                    .labelStyle(.trailingIcon)
            }
            .disabled(store.currentIndex == session.questions.count - 1)
            .opacity(store.currentIndex == session.questions.count - 1 ? 0.4 : 1)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(DaziPalette.paper)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(DaziPalette.cardStroke)
                .frame(height: 0.5)
        }
        .sheet(isPresented: $showQuestionNav) {
            MockExamQuestionNavView(session: session, store: store, isPresented: $showQuestionNav)
        }
    }
}

private struct TrailingIconLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) {
            configuration.title
            configuration.icon
        }
    }
}

private extension LabelStyle where Self == TrailingIconLabelStyle {
    static var trailingIcon: TrailingIconLabelStyle { TrailingIconLabelStyle() }
}

// MARK: - 题号导航 sheet

private struct MockExamQuestionNavView: View {
    let session: MockExamSession
    @ObservedObject var store: MockExamStore
    @Binding var isPresented: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    let modules = Dictionary(grouping: session.questions) {
                        $0.moduleName ?? "其他"
                    }
                    ForEach(modules.keys.sorted(), id: \.self) { moduleName in
                        let moduleQuestions = modules[moduleName] ?? []
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                TrackBadge(track: moduleName)
                                Spacer()
                                let answered = moduleQuestions.filter { store.answers[$0.questionId] != nil }.count
                                Text("\(answered)/\(moduleQuestions.count)")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }

                            LazyVGrid(
                                columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 8),
                                spacing: 8
                            ) {
                                ForEach(moduleQuestions, id: \.questionId) { q in
                                    let idx = session.questions.firstIndex(where: { $0.questionId == q.questionId }) ?? 0
                                    Button {
                                        store.currentIndex = idx
                                        isPresented = false
                                    } label: {
                                        Text("\(idx + 1)")
                                            .font(.caption.monospacedDigit().weight(.medium))
                                            .frame(maxWidth: .infinity)
                                            .padding(.vertical, 10)
                                            .background(
                                                idx == store.currentIndex
                                                    ? DaziPalette.brand
                                                    : store.answers[q.questionId] != nil
                                                        ? DaziPalette.brand.opacity(0.15)
                                                        : DaziPalette.card
                                            )
                                            .foregroundStyle(
                                                idx == store.currentIndex
                                                    ? .white
                                                    : store.answers[q.questionId] != nil
                                                        ? DaziPalette.brand
                                                        : .primary
                                            )
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                    }
                }
                .padding(20)
            }
            .background(DaziPalette.paper)
            .navigationTitle("题号导航")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { isPresented = false }
                        .font(.subheadline.weight(.semibold))
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

// MARK: - 复盘页（仪表盘双栏布局）

struct MockReviewView: View {
    let review: MockExamReview
    let onClose: () -> Void

    private var totalCorrect: Int { review.modules.reduce(0) { $0 + $1.correct } }
    private var totalCount: Int { review.modules.reduce(0) { $0 + $1.total } }
    private var overallAccuracy: Double { totalCount > 0 ? Double(totalCorrect) / Double(totalCount) : 0 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                scoreHeroCard

                HStack(alignment: .top, spacing: 24) {
                    VStack(alignment: .leading, spacing: 24) {
                        modulePerformanceCard
                    }
                    .frame(maxWidth: .infinity)

                    VStack(alignment: .leading, spacing: 24) {
                        nextWeekCard
                        completeButton
                    }
                    .frame(maxWidth: .infinity)
                }

                if !review.wrongClusters.isEmpty {
                    wrongClustersCard
                }
            }
            .padding(28)
            .contentMargins(maxWidth: 1280)
        }
        .background(DaziPalette.paper)
        .navigationTitle("模考复盘")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: 顶部分数英雄卡（通栏）
    private var scoreHeroCard: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 32) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkles")
                            .font(.title3.weight(.bold))
                            .foregroundStyle(DaziPalette.accentPurple)
                        Text("县令，恭喜完成本周会试")
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(DaziPalette.ink)
                    }
                    Text("交卷即胜利，复盘就是下一步上岸的台阶")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 16) {
                    HStack(alignment: .bottom, spacing: 6) {
                        Text(String(format: "%.1f", review.estimatedScore))
                            .font(.system(size: 72, weight: .bold, design: .rounded).monospacedDigit())
                            .foregroundStyle(DaziGradient.brandLinear)
                        Text("分")
                            .font(.title.weight(.medium))
                            .foregroundStyle(.secondary)
                            .padding(.bottom, 12)
                    }

                    HStack(spacing: 20) {
                        statItem(
                            label: "答对题数",
                            value: "\(totalCorrect) / \(totalCount)",
                            icon: "checkmark.circle.fill",
                            color: DaziPalette.positive
                        )
                        Divider()
                            .frame(height: 36)
                        statItem(
                            label: "正确率",
                            value: String(format: "%.0f%%", overallAccuracy * 100),
                            icon: "percent",
                            color: DaziPalette.brand
                        )
                        if let first = review.modules.first, let change = first.changeFromPrevious {
                            Divider()
                                .frame(height: 36)
                            statItem(
                                label: "较上次",
                                value: "\(change >= 0 ? "+" : "")\(String(format: "%.0f", change * 100))%",
                                icon: change >= 0 ? "arrow.up.right" : "arrow.down.right",
                                color: change >= 0 ? DaziPalette.positive : DaziPalette.alert
                            )
                        }
                    }
                }
            }
            .padding(28)

            Divider()
                .overlay(DaziPalette.cardStroke)

            HStack {
                Label(review.estimateSource, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                Spacer()
                if let submitted = formatDate(review.submittedAt) {
                    Label(submitted, systemImage: "clock")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 14)
        }
        .frame(maxWidth: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(DaziPalette.card)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }

    private func statItem(label: String, value: String, icon: String, color: Color) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.caption.weight(.semibold))
                Text(value)
                    .font(.title3.monospacedDigit().weight(.bold))
            }
            .foregroundStyle(color)

            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func formatDate(_ isoString: String) -> String? {
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: isoString) else { return nil }
        let display = DateFormatter()
        display.dateFormat = "M月d日 HH:mm 交卷"
        return display.string(from: date)
    }

    // MARK: 模块表现
    private var modulePerformanceCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("模块表现", systemImage: "chart.bar.fill")
                    .font(.headline)
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Text("\(review.modules.count) 个模块")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 14) {
                ForEach(review.modules, id: \.moduleName) { module in
                    moduleRow(module)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(DaziPalette.card)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }

    private func moduleRow(_ module: MockModuleReview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center) {
                HStack(spacing: 10) {
                    let accuracy = module.accuracy
                    let dotColor: Color = accuracy >= 0.7 ? DaziPalette.positive :
                        accuracy >= 0.5 ? DaziPalette.brand : DaziPalette.alert
                    Circle()
                        .fill(dotColor)
                        .frame(width: 8, height: 8)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(module.moduleName)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DaziPalette.ink)
                        Text("答对 \(module.correct)/\(module.total) 题")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }

                Spacer()

                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(String(format: "%.0f%%", module.accuracy * 100))
                        .font(.title2.monospacedDigit().weight(.bold))
                        .foregroundStyle(DaziPalette.ink)
                    if let change = module.changeFromPrevious {
                        let changeColor: Color = change >= 0 ? DaziPalette.positive : DaziPalette.alert
                        let changeIcon: String = change >= 0 ? "arrow.up.right" : "arrow.down.right"
                        HStack(spacing: 2) {
                            Image(systemName: changeIcon)
                                .font(.caption2.weight(.bold))
                            Text(String(format: "%.0f%%", abs(change) * 100))
                                .font(.caption.monospacedDigit().weight(.semibold))
                        }
                        .foregroundStyle(changeColor)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(
                            changeColor.opacity(0.1),
                            in: Capsule()
                        )
                    }
                }
            }

            GeometryReader { geo in
                let barColor: Color = module.accuracy >= 0.7 ? DaziPalette.positive :
                    module.accuracy >= 0.5 ? DaziPalette.brand : DaziPalette.alert
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(DaziPalette.cardStroke)
                        .frame(height: 10)

                    RoundedRectangle(cornerRadius: 6)
                        .fill(barColor)
                        .frame(width: max(geo.size.width * module.accuracy, 10), height: 10)
                }
                .frame(height: 10)
            }
            .frame(height: 10)
        }
    }

    // MARK: 错题聚集（两列网格）
    private var wrongClustersCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("错题聚集与修复", systemImage: "wrench.and.screwdriver")
                    .font(.headline)
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Text("\(review.wrongClusters.count) 个薄弱点")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            LazyVGrid(
                columns: Array(repeating: GridItem(.flexible(), spacing: 14), count: 2),
                spacing: 14
            ) {
                ForEach(review.wrongClusters, id: \.nodeId) { cluster in
                    wrongClusterRow(cluster)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(DaziPalette.card)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }

    private func wrongClusterRow(_ cluster: MockWrongCluster) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(DaziPalette.alert)
                    .padding(.top, 2)

                VStack(alignment: .leading, spacing: 4) {
                    Text(cluster.displayPath)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DaziPalette.ink)
                        .lineLimit(2)

                    HStack(spacing: 8) {
                        Label("错 \(cluster.wrongCount) 题", systemImage: "xmark.circle.fill")
                            .font(.caption2)
                            .foregroundStyle(DaziPalette.alert)
                        Spacer(minLength: 0)
                    }
                }
            }

            HStack(spacing: 6) {
                Image(systemName: "lightbulb.fill")
                    .font(.caption2)
                    .foregroundStyle(DaziPalette.accentPurple)
                Text(cluster.fixAction)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                DaziPalette.accentPurple.opacity(0.08),
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            DaziPalette.alert.opacity(0.04),
            in: RoundedRectangle(cornerRadius: 14, style: .continuous)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(DaziPalette.alert.opacity(0.15), lineWidth: 1)
        )
    }

    // MARK: 下周计划
    private var nextWeekCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("下周计划将参考", systemImage: "sparkles")
                    .font(.headline)
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Image(systemName: "wand.and.stars")
                    .font(.caption)
                    .foregroundStyle(DaziPalette.accentPurple)
            }

            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "lightbulb.fill")
                        .font(.subheadline)
                        .foregroundStyle(DaziPalette.accentPurple)
                        .padding(.top, 2)
                    Text(review.nextWeekReason)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineSpacing(6)
                }

                Divider()
                    .overlay(DaziPalette.cardStroke)

                HStack(spacing: 8) {
                    Image(systemName: "target")
                        .font(.caption)
                        .foregroundStyle(DaziPalette.brand)
                    Text("下周将针对性加强薄弱模块")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DaziPalette.brand)
                    Spacer()
                }
                .padding(10)
                .background(
                    DaziPalette.brand.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(DaziPalette.card)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }

    // MARK: 完成按钮
    private var completeButton: some View {
        Button {
            onClose()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.title3)
                Text("完成复盘")
                    .font(.headline.weight(.semibold))
                Spacer()
                Image(systemName: "arrow.right")
                    .font(.subheadline.weight(.semibold))
                    .opacity(0.8)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 24)
            .padding(.vertical, 18)
            .frame(maxWidth: .infinity)
            .background(
                DaziPalette.highlightGradient,
                in: RoundedRectangle(cornerRadius: 18, style: .continuous)
            )
            .shadow(color: DaziPalette.accentPurple.opacity(0.35), radius: 20, x: 0, y: 10)
        }
        .buttonStyle(.plain)
    }
}
