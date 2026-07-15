import SwiftUI

// MARK: - 明暗自适应色辅助

extension Color {
    /// 构造随系统外观自动切换的动态色
    init(light: Color, dark: Color) {
        self.init(UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
    }
}

// MARK: - 渐变令牌（品牌主渐变）

enum DaziGradient {
    /// 古风品牌主渐变：朱砂 → 赭石（统一到「治国」世界观）
    static let brand: [Color] = [
        GuzhuPalette.cinnabar,
        GuzhuPalette.ochre,
    ]

    static var brandLinear: LinearGradient {
        LinearGradient(
            colors: brand,
            startPoint: UnitPoint(x: 0.10, y: 0.30),
            endPoint: UnitPoint(x: 0.90, y: 0.70)
        )
    }

    /// 行动 CTA 渐变：绛红 → 深朱，沉稳不刺眼（替代原高饱和朱砂渐变）
    static var actionLinear: LinearGradient {
        LinearGradient(
            colors: [GuzhuPalette.vermilion, GuzhuPalette.vermilionDeep],
            startPoint: UnitPoint(x: 0.10, y: 0.30),
            endPoint: UnitPoint(x: 0.90, y: 0.70)
        )
    }

    /// 柔和版渐变（用于大面积背景光晕）
    static let soft: [Color] = [
        GuzhuPalette.cinnabar.opacity(0.40),
        GuzhuPalette.ochre.opacity(0.30),
    ]
}

// MARK: - 设计令牌（桥接层：统一到古风 GuzhuPalette）
//
// 说明：本项目历史上有两套设计系统——古风 GuzhuPalette 与现代蓝紫 DaziPalette。
// 为彻底统一全 App 视觉、保留「治国」沉浸感，DaziPalette 现作为 GuzhuPalette 的
// 桥接层：所有语义令牌一律返回古风色值。调用点无需改动即获得一致外观。
// 纪律：新增颜色必须只走 GuzhuPalette；DaziPalette 仅做转发，不再引入新色值。

enum DaziPalette {
    // MARK: 底色 / 卡片 / 正文（转发古风）
    static let paper = GuzhuPalette.paper
    static let card = GuzhuPalette.card
    static let ink = GuzhuPalette.ink

    // MARK: 品牌主色（朱砂红）
    static let brand = GuzhuPalette.cinnabar
    static let accentBlue = GuzhuPalette.azurite
    static let accentPurple = GuzhuPalette.azurite

    // MARK: 强卡片底 / CTA 底（朱砂→赭石渐变，更有质感）
    static let highlightGradient = DaziGradient.brandLinear

    /// 强卡片上的正文色（白字在朱砂渐变上最清晰）
    static let onHighlight = Color.white
    /// 强卡片上的次级文字
    static let onHighlightSecondary = Color.white.opacity(0.75)

    // MARK: 状态色
    /// 正反馈：青瓷绿
    static let positive = GuzhuPalette.celadon
    /// 警示：古风朱红（与 brand 区分）
    static let alert = Color(
        light: Color(red: 0.72, green: 0.21, blue: 0.16),
        dark: Color(red: 0.88, green: 0.38, blue: 0.30)
    )

    // MARK: 描边（转发古风）
    static let cardStroke = GuzhuPalette.cardStroke

    // MARK: 双轨色（转发古风模块色）
    static func track(_ name: String) -> Color {
        GuzhuPalette.moduleColor(name)
    }
}

// MARK: - 纸面卡片（古风官文书卷 · 全 App 唯一卡片语言）
//
// 模仿宣纸/竹简质感：微暖底、墨色描边、低圆角、无重阴影。
// 与现代 SaaS 白卡彻底区分，营造「治国」沉浸感。

struct PaperCard: ViewModifier {
    var padding: CGFloat = 16

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(GuzhuPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(GuzhuPalette.faintInk.opacity(0.25), lineWidth: 1)
            )
    }
}

extension View {
    func paperCard(padding: CGFloat = 16) -> some View {
        modifier(PaperCard(padding: padding))
    }
}

// MARK: - 小节头（渐变色竖条 + 衬线标题）

struct DocketHeader: View {
    let title: String
    var subtitle: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Rectangle()
                    .fill(DaziGradient.brandLinear)
                    .frame(width: 3, height: 14)
                    .cornerRadius(1.5)
                Text(title)
                    .font(.subheadline.weight(.bold))
                    .fontDesign(.serif)
                    .foregroundStyle(DaziPalette.ink)
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            Rectangle()
                .fill(DaziPalette.cardStroke)
                .frame(height: 1)
        }
    }
}

// MARK: - 目标条（首屏第一视觉锚）

struct GoalStripView: View {
    let target: TargetContext

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(target.exam) · \(target.positionGroup)")
                    .font(.subheadline.weight(.semibold))
                Text("距离目标分数")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if target.scoreGap > 0 {
                Text("-\(target.scoreGap, specifier: "%.0f") 分")
                    .font(.title.monospacedDigit().weight(.heavy))
                    .foregroundStyle(DaziPalette.alert)
            } else {
                Text("待校准")
                    .font(.headline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - 鹿鸣主判断卡

struct CoachBriefCard: View {
    let plan: TodayTaskstream

    private var totalMinutes: Int {
        plan.trackMix.reduce(0) { $0 + $1.minutes }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.caption)
                Text("鹿鸣判断")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.white.opacity(0.85))
            }

            Text(plan.headline)
                .font(.title2.weight(.bold))

            Text(plan.rationale)
                .font(.callout)
                .lineSpacing(4)
                .foregroundStyle(Color.white.opacity(0.92))

            HStack(spacing: 16) {
                Label("\(totalMinutes) 分钟", systemImage: "clock")
                Label("\(plan.tasks.count) 项任务", systemImage: "list.number")
            }
            .font(.caption.monospacedDigit().weight(.medium))
            .foregroundStyle(Color.white.opacity(0.8))
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .foregroundStyle(.white)
        .background(
            DaziGradient.brandLinear,
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
        .overlay(
            // 左侧朱砂竖线：类似奏折的红线标记
            Rectangle()
                .fill(Color.white.opacity(0.3))
                .frame(width: 3),
            alignment: .leading
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color.white.opacity(0.15), lineWidth: 1)
        )
    }
}

// MARK: - 双轨时间条（按分钟比例分色）

struct TrackTimelineBar: View {
    let items: [BackendTrackMix]
    @State private var expandedTrack: String?

    private var totalMinutes: Int {
        max(items.reduce(0) { $0 + $1.minutes }, 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("双轨配比")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text("共 \(totalMinutes) 分钟")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            GeometryReader { proxy in
                HStack(spacing: 3) {
                    ForEach(items) { item in
                        Button {
                            withAnimation(.snappy) {
                                expandedTrack = expandedTrack == item.track ? nil : item.track
                            }
                        } label: {
                            Text("\(item.track) \(item.minutes)分")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white)
                                .frame(maxWidth: .infinity)
                                .frame(height: 30)
                                .background(
                                    DaziPalette.track(item.track)
                                        .opacity(expandedTrack == nil || expandedTrack == item.track ? 1 : 0.45),
                                    in: RoundedRectangle(cornerRadius: 8)
                                )
                        }
                        .buttonStyle(.plain)
                        .frame(width: segmentWidth(in: proxy.size.width, minutes: item.minutes))
                    }
                }
            }
            .frame(height: 30)

            if let expandedTrack,
               let item = items.first(where: { $0.track == expandedTrack }) {
                Text(item.reason)
                    .font(.subheadline)
                    .lineSpacing(3)
                    .padding(.leading, 10)
                    .overlay(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(DaziPalette.track(item.track))
                            .frame(width: 3)
                    }
                    .transition(.opacity)
            }
        }
    }

    private func segmentWidth(in total: CGFloat, minutes: Int) -> CGFloat {
        let spacing = CGFloat(max(items.count - 1, 0)) * 3
        let available = max(total - spacing, 0)
        return max(available * CGFloat(minutes) / CGFloat(totalMinutes), 64)
    }
}

// MARK: - 今日路线（垂直步骤流）

enum TaskStepState {
    case done
    case current
    case upcoming
}

struct TaskPathView: View {
    let tasks: [BackendTodayTask]
    let completedIDs: Set<String>
    var compact = false
    let onSelect: (BackendTodayTask) -> Void

    private var currentID: String? {
        tasks.first { !completedIDs.contains($0.id) }?.id
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !compact {
                Text("今日路线")
                    .font(.footnote.weight(.semibold))
                    .fontDesign(.serif)
                    .foregroundStyle(GuzhuPalette.faintInk)
                    .padding(.bottom, 12)
            }
            ForEach(Array(tasks.enumerated()), id: \.element.id) { index, task in
                TaskStepNode(
                    index: index + 1,
                    task: task,
                    state: state(for: task),
                    isLast: index == tasks.count - 1,
                    compact: compact
                ) {
                    onSelect(task)
                }
            }
        }
    }

    private func state(for task: BackendTodayTask) -> TaskStepState {
        if completedIDs.contains(task.id) { return .done }
        if task.id == currentID { return .current }
        return .upcoming
    }
}

struct TaskStepNode: View {
    let index: Int
    let task: BackendTodayTask
    let state: TaskStepState
    let isLast: Bool
    var compact = false
    let action: () -> Void

    private var nodeSize: CGFloat { compact ? 20 : 26 }

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: compact ? 10 : 12) {
                nodeCircle
                content
                    .padding(.bottom, isLast ? 0 : (compact ? 12 : 18))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(alignment: .topLeading) {
                if !isLast {
                    Rectangle()
                        .fill(GuzhuPalette.faintInk.opacity(0.3))
                        .frame(width: 1.5)
                        .padding(.top, nodeSize + 4)
                        .padding(.leading, nodeSize / 2 - 0.75)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var nodeCircle: some View {
        ZStack {
            switch state {
            case .done:
                Circle().fill(DaziPalette.positive)
                Image(systemName: "checkmark")
                    .font(.system(size: compact ? 9 : 11, weight: .bold))
                    .foregroundStyle(.white)
            case .current:
                Circle().fill(DaziGradient.brandLinear)
                Text("\(index)")
                    .font(.system(size: compact ? 10 : 12, weight: .bold).monospacedDigit())
                    .foregroundStyle(.white)
            case .upcoming:
                Circle().strokeBorder(GuzhuPalette.faintInk.opacity(0.5), lineWidth: 1.5)
                Text("\(index)")
                    .font(.system(size: compact ? 10 : 12, weight: .semibold).monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: nodeSize, height: nodeSize)
    }

    @ViewBuilder
    private var content: some View {
        if compact {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(task.title)
                    .font(.footnote.weight(state == .current ? .semibold : .regular))
                    .foregroundStyle(state == .upcoming ? .secondary : .primary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text("\(task.estimatedMinutes)分")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        } else {
            switch state {
            case .current:
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(task.title)
                            .font(.headline)
                            .foregroundStyle(GuzhuPalette.ink)
                        TrackBadge(track: task.track)
                        Spacer()
                        Text("\(task.estimatedMinutes)分")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    Text(task.subtitle)
                        .font(.subheadline)
                        .foregroundStyle(GuzhuPalette.faintInk)
                    Text(task.reason)
                        .font(.subheadline)
                        .lineSpacing(2)
                        .foregroundStyle(GuzhuPalette.faintInk)
                    Label(task.sourceGrounding, systemImage: "link")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GuzhuPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(
                    // 左侧朱砂竖线：表示当前待办，类似官文批红
                    Rectangle()
                        .fill(GuzhuPalette.cinnabar.opacity(0.6))
                        .frame(width: 3),
                    alignment: .leading
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(GuzhuPalette.faintInk.opacity(0.2), lineWidth: 1)
                )
            case .done:
                HStack(alignment: .firstTextBaseline) {
                    Text(task.title)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    TrackBadge(track: task.track)
                    Spacer()
                    Text("已完成")
                        .font(.caption)
                        .foregroundStyle(DaziPalette.positive)
                }
                .padding(.top, 3)
            case .upcoming:
                HStack(alignment: .firstTextBaseline) {
                    Text(task.title)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    TrackBadge(track: task.track)
                    Spacer()
                    Text("\(task.estimatedMinutes)分")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                .padding(.top, 3)
                .opacity(0.8)
            }
        }
    }
}

// MARK: - 主 CTA（官印令牌风 · 朱砂底 + 墨框）
//
// 模仿古代官印/令牌：朱砂渐变底、方形微圆角、内嵌墨线边框。
// action=nil 时为完成态（青瓷绿），不可点击。

struct PrimaryCTABar: View {
    let title: String
    var subtitle: String? = nil
    let systemImage: String
    var action: (() -> Void)? = nil

    var body: some View {
        Button {
            action?()
        } label: {
            HStack(spacing: 12) {
                Image(systemName: systemImage)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(.white)
                    if let subtitle {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.85))
                    }
                }
                Spacer()
                if action != nil {
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.8))
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 13)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                action == nil
                    ? LinearGradient(colors: [GuzhuPalette.celadon, GuzhuPalette.celadon], startPoint: .top, endPoint: .bottom)
                    : DaziGradient.actionLinear,
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(Color.white.opacity(0.2), lineWidth: 1)
                )
            .shadow(
                color: GuzhuPalette.vermilion.opacity(0.20),
                radius: 6, x: 0, y: 3
            )
        }
        .buttonStyle(.plain)
        .disabled(action == nil)
    }
}

// MARK: - 顶部分类标签（小红书式横向滑动 · 替代原底部板块快捷栏）
//
// 设计纪律：原底部 ModuleHotbar 与系统标签栏、底部 CTA 三者堆叠造成导航碰撞，
// 现改为页面顶部固定横向滑动标签；板块过滤消费后端 moduleName，不从标题猜测。
// 选中态为朱砂加粗 + 底部朱砂下划线，未选中为淡墨，与古风统一。

struct CategoryTabBar: View {
    @Binding var selectedModule: GuzhuModule?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 0) {
                tabButton(nil, label: "全部")
                ForEach(GuzhuModule.allCases) { module in
                    tabButton(module, label: module.shortName)
                }
            }
            .padding(.horizontal, 16)
        }
        .frame(height: 44)
        .background(GuzhuPalette.paper)
        .overlay(
            Rectangle()
                .fill(GuzhuPalette.divider)
                .frame(height: 1),
            alignment: .bottom
        )
    }

    private func isSelected(_ module: GuzhuModule?) -> Bool {
        selectedModule == module
    }

    private func tabButton(_ module: GuzhuModule?, label: String) -> some View {
        Button {
            selectedModule = module
        } label: {
            VStack(spacing: 5) {
                Text(label)
                    .font(.subheadline.weight(isSelected(module) ? .bold : .regular))
                    .foregroundStyle(isSelected(module) ? GuzhuPalette.cinnabar : GuzhuPalette.faintInk)
                Rectangle()
                    .fill(isSelected(module) ? GuzhuPalette.cinnabar : Color.clear)
                    .frame(maxWidth: .infinity, minHeight: 2, maxHeight: 2)
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)
            .padding(.bottom, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 呼吸灯图标（搭子说 · 暗示背后 AI 智库实时推演）
//
// 极缓慢的呼吸动效：缓慢放大 + 提亮，暗示鹿鸣正在推演策略。

struct BreathingIcon: View {
    let systemName: String
    @State private var breathe = false

    var body: some View {
        Image(systemName: systemName)
            .font(.caption)
            .foregroundStyle(GuzhuPalette.cinnabar)
            .scaleEffect(breathe ? 1.15 : 0.9)
            .opacity(breathe ? 1.0 : 0.5)
            .animation(
                .easeInOut(duration: 2.6).repeatForever(autoreverses: true),
                value: breathe
            )
            .onAppear { breathe = true }
            .accessibilityLabel("搭子正在为你推演今日策略")
    }
}
