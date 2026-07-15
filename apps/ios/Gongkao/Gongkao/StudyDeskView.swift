import SwiftUI

struct StudyDeskView: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedObjectID: String?
    @State private var showCoachDrawer = false

    private var selectedObject: WorkbenchObject? {
        appState.workbenchSections
            .flatMap(\.objects)
            .first { $0.id == selectedObjectID }
            ?? appState.workbenchSections.first?.objects.first
    }

    var body: some View {
        GeometryReader { proxy in
            layout(for: proxy.size.width)
                .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .background(DaziPalette.paper)
    }

    @ViewBuilder
    private func layout(for width: CGFloat) -> some View {
        if width >= 1180 {
            wideLayout(width: width)
        } else {
            portraitLayout(width: width)
        }
    }

    private func wideLayout(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            DeskSidebar(selectedObjectID: $selectedObjectID)
                .frame(width: 280)

            Divider()

            DeskCanvas(selectedObject: selectedObject)
                .frame(maxWidth: .infinity)

            Divider()

            DeskCoachPane(selectedObject: selectedObject)
                .frame(width: min(max(width * 0.28, 340), 400))
        }
    }

    private func portraitLayout(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            DeskSidebar(selectedObjectID: $selectedObjectID)
                .frame(width: min(max(width * 0.31, 236), 276))

            Divider()

            DeskCanvas(selectedObject: selectedObject)
                .frame(maxWidth: .infinity)
                .overlay(alignment: .bottomTrailing) {
                    Button {
                        showCoachDrawer = true
                    } label: {
                        Label("搭子", systemImage: "sparkles")
                            .font(.headline)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            .foregroundStyle(.white)
                            .background(DaziPalette.brand, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .padding(20)
                }
        }
        .sheet(isPresented: $showCoachDrawer) {
            DeskCoachPane(selectedObject: selectedObject)
                .presentationDetents([.medium, .large])
        }
    }
}

// MARK: - 左栏：目标 + 今日路径 + 学习对象库

private struct DeskSidebar: View {
    @EnvironmentObject private var appState: AppState
    @Binding var selectedObjectID: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("公考搭子")
                        .font(.title3.weight(.bold))
                    if let target = appState.today?.target {
                        GoalStripView(target: target)
                    }
                }

                if let today = appState.today {
                    TaskPathView(
                        tasks: today.tasks,
                        completedIDs: appState.completedTaskIDs,
                        compact: true
                    ) { task in
                        appState.openTask(task)
                    }
                } else {
                    DeskStatePanel()
                }

                Divider()

                VStack(alignment: .leading, spacing: 10) {
                    Text("学习对象")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.secondary)
                    if appState.workbenchSections.isEmpty {
                        Text("完成任务后生成复盘对象")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(appState.workbenchSections) { section in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(section.title)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                ForEach(section.objects) { object in
                                    DeskObjectButton(object: object, selectedObjectID: $selectedObjectID)
                                }
                            }
                        }
                    }
                }
            }
            .padding(18)
        }
        .background(DaziPalette.paper)
    }
}

private struct DeskObjectButton: View {
    @EnvironmentObject private var appState: AppState
    let object: WorkbenchObject
    @Binding var selectedObjectID: String?

    var body: some View {
        Button {
            selectedObjectID = object.id
            appState.activeTask = nil
        } label: {
            HStack(spacing: 8) {
                Image(systemName: object.symbol)
                    .foregroundStyle(object.color)
                    .frame(width: 20)
                Text(object.title)
                    .font(.subheadline)
                    .lineLimit(1)
                Spacer(minLength: 6)
            }
            .padding(.vertical, 7)
            .padding(.horizontal, 8)
            .background(
                selectedObjectID == object.id && appState.activeTask == nil
                    ? DaziPalette.brand.opacity(0.12)
                    : Color.clear,
                in: RoundedRectangle(cornerRadius: 8)
            )
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 中栏：纯执行画布

private struct DeskCanvas: View {
    @EnvironmentObject private var appState: AppState
    let selectedObject: WorkbenchObject?
    @State private var askContext: TodayView.AskContext?
    @State private var showCapture = false

    var body: some View {
        idleCanvas
            .background(DaziPalette.paper)
        .sheet(item: $askContext) { context in
            NavigationStack {
                CompanionAskView(
                    accessToken: appState.authSession?.accessToken ?? "",
                    nodeId: context.nodeId,
                    prefill: context.prefill,
                    rankName: appState.rankProfile?.rankName
                )
            }
        }
        .sheet(isPresented: $showCapture) {
            CaptureQuestionView(accessToken: appState.authSession?.accessToken ?? "")
                .environmentObject(appState)
        }
    }

    private var idleCanvas: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if let today = appState.today {
                    LumingTodayRouteView(
                        plan: today,
                        tasks: today.tasks,
                        completedIDs: appState.completedTaskIDs
                    ) { task in
                        appState.openTask(task)
                    }

                    Divider()

                    if let feed = appState.feed, !feed.cards.isEmpty {
                        FeedCardList(
                            cards: feed.cards,
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
                                askContext = TodayView.AskContext(nodeId: nodeId, prefill: prefill)
                            },
                            onCapture: { showCapture = true }
                        )

                        Divider()
                    }

                    if let selectedObject {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack(spacing: 8) {
                                Image(systemName: selectedObject.symbol)
                                    .foregroundStyle(selectedObject.color)
                                Text(selectedObject.title)
                                    .font(.headline)
                                Text(selectedObject.kind)
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(selectedObject.color)
                            }
                            Text(selectedObject.subtitle)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        EmptyWorkbenchPanel()
                    }
                } else {
                    DeskStatePanel()
                }
            }
            .padding(24)
            .frame(maxWidth: 760)
            .frame(maxWidth: .infinity)
        }
    }
}

// MARK: - 右栏：数据驱动的鹿鸣面板

private struct DeskCoachPane: View {
    @EnvironmentObject private var appState: AppState
    let selectedObject: WorkbenchObject?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                coachJudgment
                Divider()
                sources
                Divider()
                nextStep
                // AI 额度当前仅来自申论点评响应；未做过点评则无数据，隐藏该区域。
                // 后续可接入 bootstrap / learner memory 的额度字段作为统一来源。
                if let quota = appState.essayReview?.quota {
                    Divider()
                    quotaRow(quota)
                }
            }
            .padding(20)
        }
        .background(DaziPalette.paper)
    }

    private var coachJudgment: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("鹿鸣判断", systemImage: "sparkles")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)

            if let feedback = appState.answerFeedback {
                Text(feedback.correct ? "这题答对了" : "这题需要复盘")
                    .font(.headline)
                    .foregroundStyle(feedback.correct ? DaziPalette.positive : DaziPalette.alert)
                Text(feedback.nextAction)
                    .font(.subheadline)
                    .lineSpacing(3)
            } else if let task = appState.activeTask {
                Text(task.title)
                    .font(.headline)
                Text(task.reason)
                    .font(.subheadline)
                    .lineSpacing(3)
            } else if let today = appState.today {
                Text(today.headline)
                    .font(.headline)
                Text(today.rationale)
                    .font(.subheadline)
                    .lineSpacing(3)
            } else {
                switch appState.loadState {
                case .authRequired:
                    Text("需要 Apple 登录后生成计划")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                case .profileRequired:
                    Text("需要先保存备考目标")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                default:
                    Text("等待今日计划")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var sources: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("这些安排从哪来", systemImage: "link")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
            Text("搭子的每个判断都注明数据出处，不拍脑袋。")
                .font(.caption)
                .foregroundStyle(.tertiary)

            if let feedback = appState.answerFeedback {
                sourceRow(feedback.sourceSummary)
                if let explanationSource = feedback.explanationSource {
                    sourceRow("解析来源：\(explanationSource)")
                }
            } else if let task = appState.activeTask {
                sourceRow(task.sourceGrounding)
            } else if let today = appState.today {
                ForEach(today.tasks) { task in
                    sourceRow(task.sourceGrounding)
                }
            }
        }
    }

    private func sourceRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "doc.text")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 2)
            Text(text)
                .font(.subheadline)
                .lineSpacing(2)
        }
    }

    private var nextStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("下一步", systemImage: "arrow.turn.down.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)

            if let feedback = appState.answerFeedback {
                Text(feedback.nextAction)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(DaziPalette.brand)
            } else if let today = appState.today,
                      let next = today.tasks.first(where: { !appState.completedTaskIDs.contains($0.id) }) {
                Button {
                    appState.openTask(next)
                } label: {
                    Label("\(next.title) · \(next.estimatedMinutes)分", systemImage: "play.fill")
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .foregroundStyle(.white)
                        .background(DaziPalette.brand, in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            } else if let tomorrow = appState.tomorrowPreview {
                TomorrowPreviewCard(preview: tomorrow)
            } else {
                Text("今日路线已完成，明天的计划会参考今天的表现。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func quotaRow(_ quota: AIQuotaState) -> some View {
        HStack {
            Label("AI 点评额度", systemImage: "gauge")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Text("\(quota.used)/\(quota.limit)")
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(quota.used >= quota.limit ? DaziPalette.alert : .primary)
        }
    }
}

private struct DeskStatePanel: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        switch appState.loadState {
        case .authRequired:
            AuthRequiredPanel()
        case .profileRequired(let message):
            ProfileRequiredPanel(message: message)
        case .failed(let message):
            ErrorBanner(message: message) {
                Task { await appState.loadInitialData() }
            }
        default:
            LoadingPanel(title: "读取今日计划")
        }
    }
}

// MARK: - 明日路线预告卡（会话结束钩子：把今天的练习转化成明天回来的明确意图）
/// 设计纪律（学习优先）：聚焦"最弱模块补强"，而非发放 cosmetic 奖励；分差由 headline 自带。
struct TomorrowPreviewCard: View {
    let preview: TomorrowPreview

    private var focusColor: Color {
        GuzhuPalette.moduleColor(preview.focus.module)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "flag.checkered")
                    .foregroundStyle(DaziPalette.brand)
                Text("明日路线预告")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                Spacer()
                Text(preview.date)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            Text(preview.headline)
                .font(.headline)
                .foregroundStyle(DaziPalette.ink)
                .lineLimit(2)

            HStack(spacing: 10) {
                Image(systemName: "target")
                    .font(.headline)
                    .foregroundStyle(focusColor)
                    .frame(width: 32, height: 32)
                    .background(focusColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    Text("重点补强 · \(preview.focus.module)")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(DaziPalette.ink)
                    Text("\(preview.focus.questionCount) 题 · \(preview.focus.reason)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer()
            }

            HStack(spacing: 8) {
                ForEach(preview.trackMix) { mix in
                    Label("\(mix.track) \(mix.minutes)分",
                          systemImage: mix.track == "申论" ? "square.and.pencil" : "checklist")
                        .font(.caption)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(DaziPalette.card, in: Capsule())
                        .overlay(Capsule().strokeBorder(DaziPalette.cardStroke))
                }
            }

            Text(preview.primaryAction.subtitle)
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }
}
