import SwiftUI

struct LumingTodayRouteView: View {
    let plan: TodayTaskstream
    let tasks: [BackendTodayTask]
    let completedIDs: Set<String>
    let onSelect: (BackendTodayTask) -> Void

    private var nextTask: BackendTodayTask? {
        tasks.first { !completedIDs.contains($0.id) }
    }

    private var completedCount: Int {
        tasks.filter { completedIDs.contains($0.id) }.count
    }

    private var totalMinutes: Int {
        plan.trackMix.reduce(0) { $0 + $1.minutes }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            routeHeader
            metricGrid
            if let nextTask {
                nextTaskCard(nextTask)
            }
            TrackTimelineBar(items: plan.trackMix)
            if tasks.isEmpty {
                emptyRoute
            } else {
                TaskPathView(
                    tasks: tasks,
                    completedIDs: completedIDs
                ) { task in
                    onSelect(task)
                }
            }
        }
    }

    private var routeHeader: some View {
        HStack(alignment: .center, spacing: 14) {
            LumingMascotView(state: mascotState, size: 84)
                .layoutPriority(1)

            VStack(alignment: .leading, spacing: 8) {
                Label("鹿鸣排好的今日上岸路线", systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.white.opacity(0.82))
                    .lineLimit(1)
                    .minimumScaleFactor(0.84)

                Text(headerTitle)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)

                Text(plan.rationale)
                    .font(.callout)
                    .foregroundStyle(Color.white.opacity(0.9))
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            LinearGradient(
                colors: [GuzhuPalette.vermilion, GuzhuPalette.ochre],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ),
            in: RoundedRectangle(cornerRadius: 8, style: .continuous)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color.white.opacity(0.16), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
    }

    private var metricGrid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 108), spacing: 10)], alignment: .leading, spacing: 10) {
            TodayRouteMetric(
                title: "目标",
                value: plan.target.positionGroup,
                caption: plan.target.exam,
                tint: DaziPalette.brand
            )
            TodayRouteMetric(
                title: "分差",
                value: scoreGapText(plan.target.scoreGap),
                caption: plan.target.scoreGap > 0 ? "今日任务围绕它排" : "继续保持稳定",
                tint: scoreGapTint
            )
            TodayRouteMetric(
                title: "今日路线",
                value: "\(tasks.count) 项",
                caption: "\(totalMinutes) 分钟 · 已完成 \(completedCount)",
                tint: GuzhuPalette.azurite
            )
        }
    }

    private func nextTaskCard(_ task: BackendTodayTask) -> some View {
        Button {
            onSelect(task)
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Label("下一项", systemImage: "play.fill")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(DaziPalette.brand)
                    Spacer()
                    Text("\(task.estimatedMinutes) 分钟")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Text(task.title)
                    .font(.headline)
                    .foregroundStyle(DaziPalette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(task.reason)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                Label(task.sourceGrounding, systemImage: "checkmark.seal")
                    .font(.caption)
                    .foregroundStyle(GuzhuPalette.azurite)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(DaziPalette.brand.opacity(0.32), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private var emptyRoute: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("今日路线已清", systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundStyle(DaziPalette.positive)
            Text(plan.primaryAction.subtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var headerTitle: String {
        if tasks.isEmpty {
            return "今天先复盘，不再硬塞任务"
        }
        if plan.target.scoreGap <= 0 {
            return "今天守住优势，别让强项掉线"
        }
        return "今天先补最影响上岸分差的一段"
    }

    private var mascotState: LumingMascotState {
        if tasks.isEmpty {
            return .celebrate
        }
        if plan.target.scoreGap > 12 {
            return .alert
        }
        if plan.target.scoreGap <= 0 {
            return .confident
        }
        return .gentle
    }

    private var scoreGapTint: Color {
        plan.target.scoreGap > 0 ? DaziPalette.alert : DaziPalette.positive
    }

    private func scoreGapText(_ gap: Double) -> String {
        if gap > 0 {
            return String(format: "差 %.1f", gap)
        }
        if gap < 0 {
            return String(format: "余 %.1f", abs(gap))
        }
        return "持平"
    }
}

private struct TodayRouteMetric: View {
    let title: String
    let value: String
    let caption: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline.weight(.heavy))
                .foregroundStyle(tint)
                .lineLimit(2)
                .minimumScaleFactor(0.74)
                .fixedSize(horizontal: false, vertical: true)
            Text(caption)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 100, alignment: .leading)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(tint.opacity(0.25), lineWidth: 1)
        )
    }
}
