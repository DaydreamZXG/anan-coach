import SwiftUI

// MARK: - 任务执行分发

struct TaskExecutionView: View {
    let task: BackendTodayTask
    var embedded = false

    var body: some View {
        switch task.kind {
        case "essay_rewrite":
            EssayExecutionView(task: task, embedded: embedded)
        default:
            PracticeExecutionView(task: task, embedded: embedded)
        }
    }
}

// MARK: - 行测执行页（沉浸式全屏）

struct PracticeExecutionView: View {
    @EnvironmentObject private var appState: AppState
    let task: BackendTodayTask
    var embedded = false
    @State private var questionStart = Date()
    @State private var showScratchPad = false

    var body: some View {
        ZStack(alignment: .bottom) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let practice = appState.practiceTask,
                       let question = appState.currentQuestion {
                        progressBar(practice)
                            .padding(.top, 8)
                            .padding(.bottom, 20)

                        questionContent(question)
                            .padding(.bottom, 28)

                        optionList(question)
                            .padding(.bottom, 120)

                        if let feedback = appState.answerFeedback {
                            CoachFeedbackCard(
                                feedback: feedback,
                                isLast: appState.isLastQuestion
                            ) {
                                if appState.isLastQuestion {
                                    appState.completeActiveTask()
                                } else {
                                    appState.moveToNextQuestion()
                                }
                            }
                            .padding(.bottom, 100)
                        }
                    } else if appState.activeOperation == task.id {
                        LoadingPanel(title: "正在取题")
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .padding(.top, 120)
                    } else {
                        ErrorBanner(message: appState.errorMessage ?? "题目在路上迷路了，缓口气再试一次。") {
                            Task { await appState.startTask(task) }
                        }
                        .padding(.top, 80)
                        .padding(.horizontal, 24)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentMargins(maxWidth: 860)
            }
            .background(DaziPalette.paper)
            .scrollDismissesKeyboard(.interactively)

            if appState.answerFeedback == nil,
               appState.practiceTask != nil,
               appState.currentQuestion != nil {
                bottomSubmitBar
                    .padding(.horizontal, 24)
                    .padding(.bottom, 28)
                    .background(
                        LinearGradient(
                            colors: [DaziPalette.paper.opacity(0), DaziPalette.paper],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                        .ignoresSafeArea()
                        .allowsHitTesting(false)
                    )
            }
        }
        .background(DaziPalette.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(embedded ? .automatic : .hidden, for: .tabBar)
        .toolbar {
            if !embedded {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        appState.activeTask = nil
                    } label: {
                        Image(systemName: "xmark")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityLabel("退出练习")
                }
            }
            ToolbarItem(placement: .principal) {
                if let practice = appState.practiceTask {
                    HStack(spacing: 6) {
                        TrackBadge(track: practice.track)
                        Text("\(appState.currentQuestionIndex + 1) / \(practice.questionCount)")
                            .font(.footnote.monospacedDigit().weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showScratchPad = true
                } label: {
                    Image(systemName: "pencil.and.scribble")
                        .foregroundStyle(.secondary)
                }
                .accessibilityLabel("草稿纸")
            }
        }
        .toolbarBackground(DaziPalette.paper, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .tint(DaziPalette.brand)
        .overlay {
            if showScratchPad {
                ScratchOverlayView(isPresented: $showScratchPad)
            }
        }
        .onChange(of: appState.currentQuestionIndex) { _, _ in
            questionStart = Date()
        }
    }

    // MARK: 顶部进度条（极简细条）
    private func progressBar(_ practice: PracticeTaskResponse) -> some View {
        VStack(spacing: 8) {
            ProgressView(
                value: Double(appState.currentQuestionIndex + (appState.answerFeedback == nil ? 0 : 1)),
                total: Double(max(practice.questionCount, 1))
            )
            .progressViewStyle(.linear)
            .tint(DaziGradient.brandLinear)
            .scaleEffect(x: 1, y: 2.5, anchor: .center)

            HStack {
                if appState.answerFeedback == nil {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let elapsed = max(0, Int(context.date.timeIntervalSince(questionStart)))
                        Label(
                            String(format: "%02d:%02d", elapsed / 60, elapsed % 60),
                            systemImage: "timer"
                        )
                        .font(.caption.monospacedDigit().weight(.medium))
                        .foregroundStyle(elapsed >= 60 ? DaziPalette.alert : .secondary)
                    }
                }
                Spacer()
                Text("第 \(appState.currentQuestionIndex + 1) 题 · 共 \(practice.questionCount) 题")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: 题干（直接铺纸面，无卡片感）
    private func questionContent(_ question: QuestionDetail) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Text(question.moduleName ?? task.track)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DaziPalette.track(task.track))
                if let type = question.questionType {
                    Text("·")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Text(type)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            QuestionRichText(
                plainText: question.stem,
                htmlText: question.stemHtml,
                font: .title2,
                uiFont: .preferredFont(forTextStyle: .title2),
                lineSpacing: 10,
                foregroundColor: DaziPalette.ink
            )
        }
    }

    // MARK: 选项（大按钮，整行点击）
    private func optionList(_ question: QuestionDetail) -> some View {
        VStack(spacing: 10) {
            ForEach(question.options) { option in
                Button {
                    guard appState.answerFeedback == nil else { return }
                    withAnimation(.snappy(duration: 0.25)) {
                        appState.selectedAnswer = option.label
                    }
                } label: {
                    HStack(alignment: .center, spacing: 14) {
                        optionLabelCircle(option)
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
                            .fill(optionBackground(option))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(optionBorder(option), lineWidth: 1.5)
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func optionLabelCircle(_ option: QuestionOption) -> some View {
        let selected = isSelected(option)
        let feedback = appState.answerFeedback
        let isCorrect = feedback?.correctAnswer == option.label
        let isWrong = feedback != nil && selected && !(feedback?.correct ?? false)

        let bgColor: Color
        let textColor: Color

        if feedback != nil {
            if isCorrect {
                bgColor = DaziPalette.positive
                textColor = .white
            } else if isWrong {
                bgColor = DaziPalette.alert
                textColor = .white
            } else {
                bgColor = DaziPalette.paper
                textColor = .secondary
            }
        } else if selected {
            bgColor = DaziPalette.brand
            textColor = .white
        } else {
            bgColor = DaziPalette.card
            textColor = DaziPalette.ink
        }

        return Text(option.label)
            .font(.headline.monospaced())
            .frame(width: 36, height: 36)
            .background(Circle().fill(bgColor))
            .foregroundStyle(textColor)
    }

    private func optionBackground(_ option: QuestionOption) -> Color {
        let selected = isSelected(option)
        let feedback = appState.answerFeedback
        let isCorrect = feedback?.correctAnswer == option.label
        let isWrong = feedback != nil && selected && !(feedback?.correct ?? false)

        if feedback != nil {
            if isCorrect {
                return DaziPalette.positive.opacity(0.08)
            } else if isWrong {
                return DaziPalette.alert.opacity(0.06)
            }
        }
        return selected ? DaziPalette.brand.opacity(0.06) : DaziPalette.card
    }

    private func optionBorder(_ option: QuestionOption) -> Color {
        let selected = isSelected(option)
        let feedback = appState.answerFeedback
        let isCorrect = feedback?.correctAnswer == option.label
        let isWrong = feedback != nil && selected && !(feedback?.correct ?? false)

        if feedback != nil {
            if isCorrect { return DaziPalette.positive.opacity(0.5) }
            if isWrong { return DaziPalette.alert.opacity(0.5) }
        }
        return selected ? DaziPalette.brand.opacity(0.6) : DaziPalette.cardStroke
    }

    // MARK: 底部提交条（固定悬浮）
    private var bottomSubmitBar: some View {
        Button {
            Task { await appState.submitPracticeAnswer() }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "paperplane.fill")
                    .font(.headline)
                Text("提交答案")
                    .font(.headline.weight(.semibold))
                Spacer()
                if let selected = appState.selectedAnswer {
                    Text("已选 \(selected)")
                        .font(.subheadline.monospaced())
                        .opacity(0.85)
                }
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 22)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .background(
                DaziPalette.highlightGradient,
                in: RoundedRectangle(cornerRadius: 16, style: .continuous)
            )
            .shadow(color: DaziPalette.accentPurple.opacity(0.30), radius: 16, x: 0, y: 8)
        }
        .buttonStyle(.plain)
        .disabled(appState.selectedAnswer == nil || appState.activeOperation == "practice-answer")
        .opacity(appState.selectedAnswer == nil ? 0.5 : 1.0)
    }

    private func isSelected(_ option: QuestionOption) -> Bool {
        appState.selectedAnswer == option.label
    }
}

// MARK: - 提交后反馈（沉浸式 · 对错用色块区分）

struct CoachFeedbackCard: View {
    let feedback: AnswerFeedback
    let isLast: Bool
    let advance: () -> Void
    @State private var showFullExplanation = false

    private static let praise = ["答对了！", "就是这个思路", "稳！", "拿下"]
    private static let comfort = ["先别翻页，看看哪里绕了", "这道有点狡猾，拆开看", "错得值——正好暴露盲区"]

    private var headline: String {
        let seed = abs(feedback.questionId.hashValue)
        return feedback.correct
            ? Self.praise[seed % Self.praise.count]
            : Self.comfort[seed % Self.comfort.count]
    }

    private var accentColor: Color {
        feedback.correct ? DaziPalette.positive : DaziPalette.alert
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: feedback.correct ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .font(.title)
                    .foregroundStyle(accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text(headline)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(DaziPalette.ink)
                    Text("正确答案 \(feedback.correctAnswer ?? "-")")
                        .font(.subheadline.monospaced())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if feedback.xpGained > 0 {
                    Text("+\(feedback.xpGained) XP")
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(GuzhuPalette.cinnabar)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(GuzhuPalette.cinnabar.opacity(0.12))
                        .clipShape(Capsule())
                }
            }
            .padding(.bottom, 4)

            VStack(alignment: .leading, spacing: 10) {
                Text(feedback.nextAction)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(DaziPalette.brand)
                    .lineSpacing(4)

                Label(feedback.sourceSummary, systemImage: "link")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if let explanation = feedback.explanation {
                    DisclosureGroup(isExpanded: $showFullExplanation) {
                        Text(explanation)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineSpacing(5)
                            .padding(.top, 8)
                    } label: {
                        Text("完整解析")
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    .tint(accentColor)
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(accentColor.opacity(0.06))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(accentColor.opacity(0.25), lineWidth: 1)
            )

            Button(action: advance) {
                HStack(spacing: 10) {
                    Image(systemName: isLast ? "checkmark" : "arrow.right")
                        .font(.headline)
                    Text(isLast ? "完成任务" : "下一题")
                        .font(.headline.weight(.semibold))
                    Spacer()
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 22)
                .padding(.vertical, 15)
                .frame(maxWidth: .infinity)
                .background(
                    feedback.correct
                        ? LinearGradient(colors: [DaziPalette.positive, DaziPalette.positive.opacity(0.85)], startPoint: .top, endPoint: .bottom)
                        : DaziPalette.highlightGradient,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                )
                .shadow(
                    color: feedback.correct
                        ? DaziPalette.positive.opacity(0.30)
                        : DaziPalette.accentPurple.opacity(0.30),
                    radius: 14, x: 0, y: 6
                )
            }
            .buttonStyle(.plain)
        }
    }
}

// MARK: - 申论执行页（双栏沉浸式 · 左材料 / 右作答）

struct EssayExecutionView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let task: BackendTodayTask
    var embedded = false
    @State private var activeStage: Stage = .draft

    private enum Stage: String, CaseIterable, Identifiable {
        case draft = "作答"
        case review = "点评"
        case rewrite = "重写"
        case compare = "对比"
        var id: String { rawValue }
    }

    private var stage: Stage {
        if appState.essayRewriteResult != nil { return .compare }
        if appState.essayReview != nil { return .rewrite }
        return .draft
    }

    var body: some View {
        GeometryReader { geo in
            if horizontalSizeClass == .regular {
                HStack(spacing: 0) {
                    leftMaterialPanel
                        .frame(width: min(380, geo.size.width * 0.4))
                        .background(DaziPalette.card)

                    Divider()
                        .overlay(DaziPalette.cardStroke)

                    rightAnswerPanel
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(DaziPalette.paper)
                }
            } else {
                VStack(spacing: 0) {
                    leftMaterialPanel
                        .frame(height: geo.size.height * 0.4)
                        .background(DaziPalette.card)

                    Divider()
                        .overlay(DaziPalette.cardStroke)

                    rightAnswerPanel
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(DaziPalette.paper)
                }
            }
        }
        .background(DaziPalette.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(embedded ? .automatic : .hidden, for: .tabBar)
        .toolbar {
            if !embedded {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        appState.activeTask = nil
                    } label: {
                        Image(systemName: "xmark")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityLabel("退出练习")
                }
            }
            ToolbarItem(placement: .principal) {
                HStack(spacing: 6) {
                    TrackBadge(track: "申论")
                    Text(task.title)
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        }
        .toolbarBackground(DaziPalette.paper, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .tint(DaziPalette.brand)
        .onChange(of: stage) { oldValue, newValue in
            withAnimation(.snappy(duration: 0.3)) {
                activeStage = newValue
            }
        }
    }

    // MARK: 左栏：材料
    private var leftMaterialPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            leftPanelHeader
            Divider().overlay(DaziPalette.cardStroke)
            ScrollView {
                if let essay = appState.essayTask {
                    materialList(essay)
                        .padding(16)
                } else {
                    LoadingPanel(title: "正在读取材料")
                        .padding(.top, 80)
                }
            }
        }
    }

    private var leftPanelHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "doc.plaintext.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DaziPalette.track("申论"))
                Text("给定材料")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                if let essay = appState.essayTask {
                    Text("\(essay.materials.count) 则")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            Text("滚动阅读全部材料，右侧作答")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    private func materialList(_ essay: EssayTaskResponse) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(essay.materials) { material in
                VStack(alignment: .leading, spacing: 6) {
                    Text(material.title)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(DaziPalette.track("申论"))
                    Text(material.text)
                        .font(.body)
                        .fontDesign(.serif)
                        .lineSpacing(6)
                        .foregroundStyle(DaziPalette.ink)
                    if let source = material.source {
                        Text("来源：\(source)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
                )
            }
        }
    }

    // MARK: 右栏：作答/点评/重写/对比
    private var rightAnswerPanel: some View {
        VStack(spacing: 0) {
            rightPanelHeader
            Divider().overlay(DaziPalette.cardStroke)

            if let essay = appState.essayTask {
                questionBanner(essay)
                    .padding(18)

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        stageContent(essay)
                            .padding(.horizontal, 24)
                            .padding(.bottom, 40)
                    }
                    .padding(.top, 8)
                }
            } else {
                Spacer()
                LoadingPanel(title: "正在读取申论任务")
                Spacer()
            }
        }
    }

    private var rightPanelHeader: some View {
        HStack(spacing: 14) {
            TrackBadge(track: "申论")
            Text(task.title)
                .font(.headline)
                .foregroundStyle(DaziPalette.ink)
            Spacer()
            stagePill
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    private var stagePill: some View {
        HStack(spacing: 2) {
            ForEach(Stage.allCases) { item in
                stagePillItem(item)
            }
        }
        .padding(3)
        .background(DaziPalette.card, in: Capsule())
        .overlay(Capsule().strokeBorder(DaziPalette.cardStroke, lineWidth: 1))
    }

    private func stagePillItem(_ item: Stage) -> some View {
        let active = item == activeStage
        return Text(item.rawValue)
            .font(.caption.weight(active ? .bold : .medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .foregroundStyle(active ? .white : .secondary)
            .background(
                DaziPalette.highlightGradient.opacity(active ? 1 : 0),
                in: Capsule()
            )
    }

    private func questionBanner(_ essay: EssayTaskResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(essay.prompt)
                .font(.title2)
                .fontDesign(.serif)
                .lineSpacing(8)
                .foregroundStyle(DaziPalette.ink)

            HStack(spacing: 12) {
                Label("限 \(essay.wordLimit) 字", systemImage: "text.alignleft")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)

                if essay.dimensions.count > 0 {
                    Text("·")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Text("评分维度：\(essay.dimensions.joined(separator: "、"))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func stageContent(_ essay: EssayTaskResponse) -> some View {
        switch stage {
        case .draft:
            draftStage(essay)
        case .review, .rewrite:
            if let review = appState.essayReview {
                reviewAndRewriteStage(review: review, essay: essay)
            }
        case .compare:
            if let result = appState.essayRewriteResult {
                compareStage(result: result, essay: essay)
            }
        }
    }

    // MARK: 阶段 1：初稿作答
    private func draftStage(_ essay: EssayTaskResponse) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("写下你的答案")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Text("\(appState.essayAnswer.count) / \(essay.wordLimit) 字")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(
                        appState.essayAnswer.count > essay.wordLimit
                            ? DaziPalette.alert : .secondary
                    )
            }

            ZStack(alignment: .topLeading) {
                if appState.essayAnswer.isEmpty {
                    Text("读材料，划出要点，在这里组织语言写答案…")
                        .font(.body)
                        .foregroundStyle(.tertiary)
                        .padding(.top, 14)
                        .padding(.leading, 14)
                }
                TextEditor(text: $appState.essayAnswer)
                    .font(.body)
                    .lineSpacing(6)
                    .scrollContentBackground(.hidden)
                    .padding(10)
            }
            .frame(minHeight: 220)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
            )

            Button {
                Task { await appState.reviewEssay() }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "sparkles")
                        .font(.headline)
                    Text("请搭子点评初稿")
                        .font(.headline.weight(.semibold))
                    Spacer()
                    if appState.activeOperation == "essay-review" {
                        ProgressView()
                            .tint(.white)
                            .controlSize(.small)
                    }
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 22)
                .padding(.vertical, 15)
                .frame(maxWidth: .infinity)
                .background(
                    DaziPalette.highlightGradient,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                )
                .shadow(color: DaziPalette.accentPurple.opacity(0.30), radius: 14, x: 0, y: 6)
            }
            .buttonStyle(.plain)
            .disabled(appState.activeOperation == "essay-review" || appState.essayAnswer.isEmpty)
            .opacity(appState.essayAnswer.isEmpty ? 0.5 : 1.0)
        }
    }

    // MARK: 阶段 2-3：点评 + 重写
    private func reviewAndRewriteStage(review: EssayReviewResponse, essay: EssayTaskResponse) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            EssayReviewCard(review: review)

            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("按点评重写", systemImage: "pencil.line")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DaziPalette.ink)
                    Spacer()
                    Text("\(appState.essayRewrite.count) / \(essay.wordLimit) 字")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                ZStack(alignment: .topLeading) {
                    if appState.essayRewrite.isEmpty {
                        Text("对照点评，在这里重写一版…")
                            .font(.body)
                            .foregroundStyle(.tertiary)
                            .padding(.top, 14)
                            .padding(.leading, 14)
                    }
                    TextEditor(text: $appState.essayRewrite)
                        .font(.body)
                        .lineSpacing(6)
                        .scrollContentBackground(.hidden)
                        .padding(10)
                }
                .frame(minHeight: 180)
                .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
                )

                Button {
                    Task { await appState.compareEssayRewrite() }
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.headline)
                        Text("提交重写对比")
                            .font(.headline.weight(.semibold))
                        Spacer()
                        if appState.activeOperation == "essay-rewrite" {
                            ProgressView()
                                .tint(.white)
                                .controlSize(.small)
                        }
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, 22)
                    .padding(.vertical, 14)
                    .frame(maxWidth: .infinity)
                    .background(
                        DaziPalette.highlightGradient,
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                    )
                    .shadow(color: DaziPalette.accentPurple.opacity(0.30), radius: 14, x: 0, y: 6)
                }
                .buttonStyle(.plain)
                .disabled(appState.activeOperation == "essay-rewrite" || appState.essayRewrite.isEmpty)
                .opacity(appState.essayRewrite.isEmpty ? 0.5 : 1.0)
            }
        }
    }

    // MARK: 阶段 4：对比完成
    private func compareStage(result: EssayRewriteResponse, essay: EssayTaskResponse) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            EssayRewriteResultView(result: result)

            Button {
                appState.completeActiveTask()
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "checkmark")
                        .font(.headline)
                    Text("完成任务")
                        .font(.headline.weight(.semibold))
                    Spacer()
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 22)
                .padding(.vertical, 15)
                .frame(maxWidth: .infinity)
                .background(
                    LinearGradient(colors: [DaziPalette.positive, DaziPalette.positive.opacity(0.85)], startPoint: .top, endPoint: .bottom),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                )
                .shadow(color: DaziPalette.positive.opacity(0.30), radius: 14, x: 0, y: 6)
            }
            .buttonStyle(.plain)
        }
    }
}

// MARK: - 草稿蒙版（半透明覆盖在题目上，演算时可对照题干）

struct ScratchOverlayView: View {
    @Binding var isPresented: Bool
    @State private var lines: [[CGPoint]] = []
    @State private var currentLine: [CGPoint] = []

    var body: some View {
        ZStack(alignment: .top) {
            Canvas { context, _ in
                for line in lines + (currentLine.isEmpty ? [] : [currentLine]) {
                    guard let first = line.first else { continue }
                    var path = Path()
                    path.move(to: first)
                    for point in line.dropFirst() {
                        path.addLine(to: point)
                    }
                    context.stroke(
                        path,
                        with: .color(DaziPalette.brand),
                        style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round)
                    )
                }
            }
            .background(DaziPalette.paper.opacity(0.62))
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        currentLine.append(value.location)
                    }
                    .onEnded { _ in
                        if !currentLine.isEmpty {
                            lines.append(currentLine)
                            currentLine = []
                        }
                    }
            )

            HStack(spacing: 14) {
                Label("草稿蒙版", systemImage: "pencil.and.scribble")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    if !lines.isEmpty { lines.removeLast() }
                } label: {
                    Image(systemName: "arrow.uturn.backward")
                }
                .accessibilityLabel("撤销")
                .disabled(lines.isEmpty)
                Button("清空") {
                    lines = []
                    currentLine = []
                }
                .disabled(lines.isEmpty && currentLine.isEmpty)
                Button("收起") { isPresented = false }
                    .fontWeight(.semibold)
                    .tint(DaziPalette.brand)
            }
            .font(.subheadline)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.bar)
        }
        .transition(.opacity)
    }
}
