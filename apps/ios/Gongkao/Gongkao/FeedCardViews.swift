import SwiftUI

/// 卡片流渲染器：服务端已按 priority 排序，客户端不重排（规格第 3 节）。
struct FeedCardList: View {
    let cards: [FeedCard]
    var onOpenTask: ((String) -> Void)?
    var onStartMock: (() -> Void)?
    var onOpenMockReview: ((String) -> Void)?
    var onAskCompanion: ((String?, String?) -> Void)?
    var onCapture: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(cards) { card in
                FeedCardView(
                    card: card,
                    onOpenTask: onOpenTask,
                    onStartMock: onStartMock,
                    onOpenMockReview: onOpenMockReview,
                    onAskCompanion: onAskCompanion,
                    onCapture: onCapture
                )
            }
        }
    }
}

struct FeedCardView: View {
    let card: FeedCard
    var onOpenTask: ((String) -> Void)?
    var onStartMock: (() -> Void)?
    var onOpenMockReview: ((String) -> Void)?
    var onAskCompanion: ((String?, String?) -> Void)?
    var onCapture: (() -> Void)?

    var body: some View {
        Group {
            switch card.type {
            case .goalGap:
                GoalGapCard(card: card)
            case .coachBrief:
                CoachBriefFeedCard(card: card)
            case .taskPractice, .taskEssay, .taskMethodCard:
                TaskFeedCard(card: card, onOpenTask: onOpenTask)
            case .mockExamDue:
                MockDueCard(card: card, onStartMock: onStartMock)
            case .mockReviewPending:
                MockReviewPendingCard(card: card, onOpenMockReview: onOpenMockReview)
            case .wrongCluster:
                WrongClusterCard(card: card)
            case .streakRestart:
                StreakRestartCard(card: card, onOpenTask: onOpenTask)
            case .calibrationNote:
                CalibrationNoteCard(card: card)
            case .quotaNotice:
                QuotaNoticeCard(card: card)
            case .companionPrompt:
                CompanionPromptCard(card: card, onAskCompanion: onAskCompanion)
            case .externalCaptureNudge:
                ExternalNudgeCard(card: card, onCapture: onCapture)
            case nil:
                FallbackFeedCard(card: card)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(card.reason)")
    }
}

// MARK: - 通用卡壳（reason 脚注 + dataLine 数据来源，诚实纪律的展示层）

struct FeedCardShell<Content: View>: View {
    let card: FeedCard
    var showReason = true
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            content
            if showReason {
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.reason)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let dataLine = card.dataLine {
                        Text(dataLine)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panelStyle()
    }
}

// MARK: - 降级卡（词表外类型，禁止崩溃）

struct FallbackFeedCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card) {
            VStack(alignment: .leading, spacing: 6) {
                Label(card.payload?.title ?? "新的搭子内容", systemImage: "sparkles.rectangle.stack")
                    .font(.headline)
                Text(card.payload?.body ?? "当前版本暂不支持展示这项内容，升级 App 后可见。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - 各卡型组件

private struct GoalGapCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                if let days = card.payload?.daysLeft {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(days)")
                            .font(.title2.bold().monospacedDigit())
                        Text("天倒计时")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Divider().frame(height: 32)
                VStack(alignment: .leading, spacing: 2) {
                    if let target = card.payload?.targetScore, let est = card.payload?.estimatedScore {
                        Text(String(format: "%.1f → %.1f", est, target))
                            .font(.title3.bold().monospacedDigit())
                            .foregroundStyle(DaziPalette.brand)
                    }
                    Text(card.payload?.positionName ?? card.reason)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
            }
        }
    }
}

private struct CoachBriefFeedCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    BreathingIcon(systemName: "sparkles")
                    Text("搭子说")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DaziPalette.brand)
                }
                Text(card.payload?.text ?? card.reason)
                    .font(.subheadline)
                    .lineSpacing(4)
                if card.payload?.planBasis == "rules_v1" {
                    Text("基于规则排程")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }
}

private struct TaskFeedCard: View {
    let card: FeedCard
    var onOpenTask: ((String) -> Void)?

    private var symbol: String {
        switch card.type {
        case .taskEssay: "square.and.pencil"
        case .taskMethodCard: "rectangle.stack.badge.play"
        default: "checklist"
        }
    }

    private var title: String {
        if let name = card.payload?.moduleName, let count = card.payload?.questionCount {
            return "\(name) · \(count) 题"
        }
        if let cardTitle = card.payload?.cardTitle { return "方法卡 · \(cardTitle)" }
        if let type = card.payload?.questionType { return "申论 · \(type)" }
        return card.payload?.title ?? "训练任务"
    }

    var body: some View {
        FeedCardShell(card: card) {
            Button {
                if let taskId = card.payload?.taskId { onOpenTask?(taskId) }
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: symbol)
                        .font(.title3)
                        .foregroundStyle(DaziPalette.brand)
                        .frame(width: 34, height: 34)
                        .background(DaziPalette.brand.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.primary)
                        if let minutes = card.payload?.estMinutes {
                            Text("\(minutes) 分钟")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Image(systemName: "play.circle.fill")
                        .font(.title2)
                        .foregroundStyle(DaziPalette.brand)
                }
            }
            .buttonStyle(.plain)
        }
    }
}

private struct MockDueCard: View {
    let card: FeedCard
    var onStartMock: (() -> Void)?

    var body: some View {
        FeedCardShell(card: card) {
            Button {
                onStartMock?()
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "stopwatch")
                        .font(.title3)
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(DaziPalette.brand, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("本周模考")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.primary)
                        if let minutes = card.payload?.estMinutes {
                            // questionCount 缺失时回退到 135（国考行测惯例题量）
                            let questions = card.payload?.questionCount ?? 135
                            Text("\(questions) 题 · \(minutes) 分钟 · 中途不可暂停")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Image(systemName: "play.circle.fill")
                        .font(.title2)
                        .foregroundStyle(DaziPalette.brand)
                }
            }
            .buttonStyle(.plain)
        }
    }
}

private struct MockReviewPendingCard: View {
    let card: FeedCard
    var onOpenMockReview: ((String) -> Void)?

    var body: some View {
        FeedCardShell(card: card) {
            Button {
                if let sessionId = card.payload?.sessionId {
                    onOpenMockReview?(sessionId)
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.bubble.fill")
                        .foregroundStyle(DaziPalette.alert)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("模考复盘还没看")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.primary)
                        if let score = card.payload?.estimatedScore {
                            Text(String(format: "预估 %.1f 分，看完复盘才算完成本周模考", score))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)
        }
    }
}

private struct WrongClusterCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card) {
            VStack(alignment: .leading, spacing: 4) {
                Label("错题聚集", systemImage: "exclamationmark.triangle")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DaziPalette.alert)
                Text(card.payload?.displayPath ?? "")
                    .font(.subheadline.weight(.medium))
                if let count = card.payload?.wrongCount {
                    Text("累计错 \(count) 题")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct StreakRestartCard: View {
    let card: FeedCard
    var onOpenTask: ((String) -> Void)?

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            VStack(alignment: .leading, spacing: 8) {
                Text(card.payload?.daysIdle.map { "停了 \($0) 天，没关系" } ?? "欢迎回来")
                    .font(.subheadline.weight(.semibold))
                Text(card.reason)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineSpacing(3)
                if let miniTask = card.payload?.miniTaskId {
                    Button {
                        onOpenTask?(miniTask)
                    } label: {
                        Label("先做 5 题找回手感", systemImage: "play.fill")
                            .font(.subheadline.weight(.semibold))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(DaziPalette.brand)
                }
            }
        }
    }
}

private struct CalibrationNoteCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card) {
            HStack(spacing: 10) {
                Image(systemName: "gauge.with.needle")
                    .foregroundStyle(DaziPalette.brand)
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.payload?.statusLabel ?? "能力估计已更新")
                        .font(.subheadline.weight(.medium))
                    if let delta = card.payload?.delta {
                        Text(String(format: delta >= 0 ? "较上次 +%.1f" : "较上次 %.1f", delta))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(delta >= 0 ? DaziPalette.positive : DaziPalette.alert)
                    }
                }
                Spacer()
            }
        }
    }
}

private struct QuotaNoticeCard: View {
    let card: FeedCard

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            HStack {
                Label(
                    card.payload?.remaining.map { "AI 配额剩余 \($0) 次" } ?? "AI 配额",
                    systemImage: "bolt.badge.clock"
                )
                .font(.subheadline)
                Spacer()
                if let reset = card.payload?.resetAt {
                    Text(reset)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct CompanionPromptCard: View {
    let card: FeedCard
    var onAskCompanion: ((String?, String?) -> Void)?

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            Button {
                onAskCompanion?(card.payload?.nodeId, card.payload?.suggestedQuestion)
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "bubble.left.and.text.bubble.right")
                        .foregroundStyle(DaziPalette.brand)
                    Text(card.payload?.suggestedQuestion ?? "有不懂的，问搭子")
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.leading)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)
        }
    }
}

private struct ExternalNudgeCard: View {
    let card: FeedCard
    var onCapture: (() -> Void)?

    var body: some View {
        FeedCardShell(card: card, showReason: false) {
            Button {
                onCapture?()
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "camera.viewfinder")
                        .foregroundStyle(DaziPalette.brand)
                    Text(card.payload?.text ?? "在别处做错的题，拍照收进来一起分析")
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.leading)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)
        }
    }
}
