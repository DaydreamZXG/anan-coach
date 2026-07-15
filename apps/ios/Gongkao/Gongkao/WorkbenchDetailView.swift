import SwiftUI

@MainActor
final class WorkbenchDetailStore: ObservableObject {
    @Published var questions: [QuestionDetail] = []
    @Published var loading = false
    @Published var errorMessage: String?
    @Published var retryFeedback: [String: AnswerFeedback] = [:]

    private let client = APIClient()
    private let accessToken: String

    init(accessToken: String) {
        self.accessToken = accessToken
    }

    func loadQuestions(ids: [String]) async {
        guard questions.isEmpty, !ids.isEmpty else { return }
        loading = true
        defer { loading = false }
        // 并发请求所有题目，按原索引排序后回填，避免 N+1 串行延迟
        let client = self.client
        let token = self.accessToken
        do {
            let ordered = try await withThrowingTaskGroup(of: (Int, QuestionDetail).self) { group in
                for (index, id) in ids.enumerated() {
                    group.addTask {
                        let question: QuestionDetail = try await client.get(
                            "/api/v1/knowledge/questions/\(id)", accessToken: token
                        )
                        return (index, question)
                    }
                }
                var collected: [(Int, QuestionDetail)] = []
                for try await item in group {
                    collected.append(item)
                }
                return collected.sorted { $0.0 < $1.0 }.map { $0.1 }
            }
            questions = ordered
            errorMessage = nil
        } catch {
            errorMessage = "题目加载失败：\(error.localizedDescription)"
        }
    }

    func submitRetry(taskId: String, questionId: String, answer: String) async {
        do {
            let feedback: AnswerFeedback = try await client.post(
                "/api/v1/practice/tasks/\(taskId)/answers",
                body: AnswerSubmission(questionId: questionId, selectedAnswer: answer),
                accessToken: accessToken
            )
            retryFeedback[questionId] = feedback
        } catch {
            errorMessage = "提交失败：\(error.localizedDescription)"
        }
    }
}

struct WorkbenchObjectDetailView: View {
    @EnvironmentObject private var appState: AppState
    let object: WorkbenchObject
    @StateObject private var store: WorkbenchDetailStore

    init(object: WorkbenchObject, accessToken: String) {
        self.object = object
        _store = StateObject(wrappedValue: WorkbenchDetailStore(accessToken: accessToken))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                content
            }
            .padding(20)
            .contentMargins(maxWidth: 920)
        }
        .background(DaziPalette.paper)
        .navigationTitle(object.kind)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    CompanionAskView(
                        accessToken: appState.authSession?.accessToken ?? "",
                        nodeId: object.payload?.nodeId,
                        prefill: "关于「\(object.title)」：",
                        rankName: appState.rankProfile?.rankName
                    )
                } label: {
                    Label("就这个问搭子", systemImage: "bubble.left.and.text.bubble.right")
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(object.title)
                .font(.title3.weight(.bold))
                .textSelection(.enabled)
            Text(object.subtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch object.type {
        case "docket_review":
            DocketReviewDetail(object: object, store: store)
        case "practice_review":
            PracticeReviewDetail(object: object, store: store)
        case "mock_exam_review":
            MockReviewDetail(payload: object.payload)
        case "external_wrong_question":
            ExternalWrongDetail(payload: object.payload)
        case "essay_review":
            EssayReviewPointer(payload: object.payload)
        default:
            Text("该对象类型暂无详情视图。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }
}

private struct DocketReviewDetail: View {
    let object: WorkbenchObject
    @ObservedObject var store: WorkbenchDetailStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let wrong = object.payload?.wrongCount {
                Label(
                    wrong > 0 ? "本卷驳 \(wrong) 封，先复看这些卷宗。" : "本卷全准，已归入府衙案牍。",
                    systemImage: wrong > 0 ? "exclamationmark.seal" : "checkmark.seal"
                )
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(wrong > 0 ? GuzhuPalette.cinnabar : GuzhuPalette.celadon)
            }

            if store.loading {
                LoadingPanel(title: "正在调阅卷宗")
            } else if let message = store.errorMessage {
                ErrorBanner(message: message) {
                    Task { await store.loadQuestions(ids: object.payload?.questionIds ?? []) }
                }
            } else if store.questions.isEmpty {
                Text("没有可显示的卷宗。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(store.questions, id: \.questionId) { question in
                    DocketReviewQuestionCard(question: question)
                }
            }
        }
        .task {
            await store.loadQuestions(ids: object.payload?.questionIds ?? [])
        }
    }
}

private struct DocketReviewQuestionCard: View {
    let question: QuestionDetail

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(question.moduleName ?? "行测")
                .font(.caption.weight(.semibold))
                .foregroundStyle(GuzhuPalette.moduleColor(question.moduleName ?? ""))

            if let material = question.materialText, !material.isEmpty {
                QuestionRichText(
                    plainText: material,
                    htmlText: question.materialHtml,
                    font: .subheadline,
                    uiFont: .preferredFont(forTextStyle: .subheadline),
                    lineSpacing: 3,
                    foregroundColor: GuzhuPalette.ink
                )
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GuzhuPalette.ink.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
            }

            QuestionRichText(
                plainText: question.stem,
                htmlText: question.stemHtml,
                font: .subheadline.weight(.semibold),
                uiFont: .preferredFont(forTextStyle: .subheadline),
                lineSpacing: 4,
                foregroundColor: GuzhuPalette.ink,
                textSelectionEnabled: true
            )

            ForEach(question.options) { option in
                HStack(alignment: .top, spacing: 8) {
                    Text(option.label)
                        .font(.subheadline.bold())
                        .frame(width: 24, height: 24)
                        .background(GuzhuPalette.ink.opacity(0.06), in: Circle())
                    QuestionRichText(
                        plainText: option.displayText,
                        htmlText: option.optionHtml,
                        font: .subheadline,
                        uiFont: .preferredFont(forTextStyle: .subheadline),
                        lineSpacing: 3,
                        foregroundColor: GuzhuPalette.ink
                    )
                    Spacer(minLength: 0)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GuzhuPalette.card, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .parchmentCard(padding: 14)
    }
}

// MARK: - 行测复盘详情（题目清单 + 逐题重练）

private struct PracticeReviewDetail: View {
    let object: WorkbenchObject
    @ObservedObject var store: WorkbenchDetailStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let wrong = object.payload?.wrongCount, wrong > 0 {
                Label("本组错 \(wrong) 题，逐题重练一遍，重练记录会计入你的真实数据。", systemImage: "arrow.triangle.2.circlepath")
                    .font(.subheadline)
                    .foregroundStyle(DaziPalette.brand)
            }

            if store.loading {
                LoadingPanel(title: "正在读取题目")
            } else if let message = store.errorMessage {
                ErrorBanner(message: message) {
                    Task { await store.loadQuestions(ids: object.payload?.questionIds ?? []) }
                }
            } else if store.questions.isEmpty {
                Text("没有可显示的题目。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(store.questions, id: \.questionId) { question in
                    RetryQuestionCard(
                        question: question,
                        taskId: object.payload?.taskId ?? "",
                        feedback: store.retryFeedback[question.questionId]
                    ) { answer in
                        Task {
                            await store.submitRetry(
                                taskId: object.payload?.taskId ?? "",
                                questionId: question.questionId,
                                answer: answer
                            )
                        }
                    }
                }
            }
        }
        .task {
            await store.loadQuestions(ids: object.payload?.questionIds ?? [])
        }
    }
}

private struct RetryQuestionCard: View {
    let question: QuestionDetail
    let taskId: String
    let feedback: AnswerFeedback?
    let onSubmit: (String) -> Void
    @State private var picked: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(question.moduleName ?? "行测")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DaziPalette.brand)
                Spacer()
                if let feedback {
                    Label(
                        feedback.correct ? "重练答对" : "仍然答错",
                        systemImage: feedback.correct ? "checkmark.circle.fill" : "xmark.circle.fill"
                    )
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(feedback.correct ? DaziPalette.positive : DaziPalette.alert)
                }
            }

            Text(question.stem)
                .font(.subheadline)
                .lineSpacing(3)
                .textSelection(.enabled)

            ForEach(question.options) { option in
                Button {
                    guard feedback == nil else { return }
                    picked = option.label
                    onSubmit(option.label)
                } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Text(option.label)
                            .font(.subheadline.bold())
                        Text(option.optionText)
                            .font(.subheadline)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 0)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(optionBackground(option.label), in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .disabled(feedback != nil)
            }

            if let feedback, let explanation = feedback.explanation {
                DisclosureGroup("查看解析") {
                    Text(explanation)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineSpacing(3)
                        .padding(.top, 6)
                }
                .font(.caption.weight(.semibold))
            }
        }
        .panelStyle()
    }

    private func optionBackground(_ label: String) -> Color {
        guard let feedback else {
            return picked == label ? DaziPalette.brand.opacity(0.12) : Color(.systemFill).opacity(0.4)
        }
        if label == feedback.correctAnswer {
            return DaziPalette.positive.opacity(0.15)
        }
        if label == feedback.selectedAnswer, !feedback.correct {
            return DaziPalette.alert.opacity(0.15)
        }
        return Color(.systemFill).opacity(0.4)
    }
}

// MARK: - 模考复盘详情

private struct MockReviewDetail: View {
    let payload: WorkbenchObjectPayload?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let score = payload?.estimatedScore {
                VStack(alignment: .leading, spacing: 4) {
                    Text("预估行测分")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(String(format: "%.1f", score))
                        .font(.system(size: 40, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundStyle(DaziPalette.brand)
                }
                .panelStyle()
            }

            if let reason = payload?.nextWeekReason, !reason.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Label("下周计划依据", systemImage: "arrow.turn.down.right")
                        .font(.headline)
                    Text(reason)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineSpacing(3)
                }
                .panelStyle()
            }

            if payload?.viewed == false {
                Text("本复盘计入模考任务完成条件，已随本次打开自动标记。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - 外部错题详情

private struct ExternalWrongDetail: View {
    let payload: WorkbenchObjectPayload?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let path = payload?.displayPath {
                VStack(alignment: .leading, spacing: 6) {
                    Label("知识点定位", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                        .font(.headline)
                    Text(path)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .panelStyle()
            }
            Text("来自你的拍照/截图录入，已计入该知识点的错题记录，参与每日计划的聚合分析。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - 申论复盘指引

private struct EssayReviewPointer: View {
    @EnvironmentObject private var appState: AppState
    let payload: WorkbenchObjectPayload?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(payload?.hasRewrite == true
                ? "本次申论已完成点评与重写。完整的五维点评与重写对比请在申论任务页查看。"
                : "本次申论已完成点评，尚未重写。到申论任务页完成重写可获得对比反馈。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineSpacing(3)

            if let task = appState.today?.tasks.first(where: { $0.track == "shenlun" }) {
                Button {
                    appState.openTask(task)
                } label: {
                    Label("前往申论任务", systemImage: "arrow.right.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(DaziPalette.brand)
            }
        }
        .panelStyle()
    }
}
