import SwiftUI

enum LumingMascotState {
    case idle
    case thinking
    case confident
    case alert
    case celebrate
    case gentle
}

struct LumingMascotView: View {
    var state: LumingMascotState = .idle
    var size: CGFloat = 76

    var body: some View {
        LumingVector(state: state)
            .frame(width: size, height: size)
            .accessibilityLabel("鹿鸣")
    }
}

private struct LumingVector: View {
    let state: LumingMascotState

    private var accent: Color {
        switch state {
        case .alert:
            return DaziPalette.alert
        case .celebrate:
            return GuzhuPalette.ochre
        case .confident:
            return DaziPalette.positive
        case .gentle:
            return GuzhuPalette.azurite
        case .thinking, .idle:
            return DaziPalette.brand
        }
    }

    var body: some View {
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            ZStack {
                Circle()
                    .fill(accent.opacity(0.12))
                    .frame(width: side, height: side)

                antlers(side: side)
                    .stroke(
                        GuzhuPalette.ochre,
                        style: StrokeStyle(lineWidth: side * 0.055, lineCap: .round, lineJoin: .round)
                    )

                ears(side: side)

                RoundedRectangle(cornerRadius: side * 0.30, style: .continuous)
                    .fill(GuzhuPalette.card)
                    .frame(width: side * 0.58, height: side * 0.52)
                    .overlay(
                        RoundedRectangle(cornerRadius: side * 0.30, style: .continuous)
                            .strokeBorder(GuzhuPalette.cardStroke, lineWidth: 1)
                    )
                    .offset(y: side * 0.07)

                face(side: side)
                    .offset(y: side * 0.08)

                routeMark(side: side)
                    .stroke(accent, style: StrokeStyle(lineWidth: side * 0.025, lineCap: .round, lineJoin: .round))
                    .offset(y: side * 0.28)
            }
            .frame(width: side, height: side)
            .position(x: proxy.size.width / 2, y: proxy.size.height / 2)
        }
    }

    private func antlers(side: CGFloat) -> Path {
        Path { path in
            path.move(to: CGPoint(x: side * 0.40, y: side * 0.33))
            path.addLine(to: CGPoint(x: side * 0.31, y: side * 0.18))
            path.move(to: CGPoint(x: side * 0.31, y: side * 0.18))
            path.addLine(to: CGPoint(x: side * 0.21, y: side * 0.13))
            path.move(to: CGPoint(x: side * 0.31, y: side * 0.18))
            path.addLine(to: CGPoint(x: side * 0.34, y: side * 0.07))

            path.move(to: CGPoint(x: side * 0.60, y: side * 0.33))
            path.addLine(to: CGPoint(x: side * 0.69, y: side * 0.18))
            path.move(to: CGPoint(x: side * 0.69, y: side * 0.18))
            path.addLine(to: CGPoint(x: side * 0.79, y: side * 0.13))
            path.move(to: CGPoint(x: side * 0.69, y: side * 0.18))
            path.addLine(to: CGPoint(x: side * 0.66, y: side * 0.07))
        }
    }

    private func routeMark(side: CGFloat) -> Path {
        Path { path in
            path.move(to: CGPoint(x: side * 0.36, y: side * 0.50))
            path.addCurve(
                to: CGPoint(x: side * 0.64, y: side * 0.50),
                control1: CGPoint(x: side * 0.44, y: side * 0.42),
                control2: CGPoint(x: side * 0.56, y: side * 0.58)
            )
        }
    }

    @ViewBuilder
    private func ears(side: CGFloat) -> some View {
        Capsule()
            .fill(GuzhuPalette.card)
            .frame(width: side * 0.18, height: side * 0.28)
            .rotationEffect(.degrees(-28))
            .offset(x: -side * 0.30, y: -side * 0.05)
            .overlay(
                Capsule()
                    .fill(GuzhuPalette.ochre.opacity(0.18))
                    .frame(width: side * 0.08, height: side * 0.17)
                    .rotationEffect(.degrees(-28))
                    .offset(x: -side * 0.30, y: -side * 0.05)
            )

        Capsule()
            .fill(GuzhuPalette.card)
            .frame(width: side * 0.18, height: side * 0.28)
            .rotationEffect(.degrees(28))
            .offset(x: side * 0.30, y: -side * 0.05)
            .overlay(
                Capsule()
                    .fill(GuzhuPalette.ochre.opacity(0.18))
                    .frame(width: side * 0.08, height: side * 0.17)
                    .rotationEffect(.degrees(28))
                    .offset(x: side * 0.30, y: -side * 0.05)
            )
    }

    @ViewBuilder
    private func face(side: CGFloat) -> some View {
        HStack(spacing: side * 0.16) {
            eye(side: side)
            eye(side: side)
        }
        .offset(y: -side * 0.03)

        Capsule()
            .fill(GuzhuPalette.ochre.opacity(0.22))
            .frame(width: side * 0.20, height: side * 0.12)
            .offset(y: side * 0.07)

        Circle()
            .fill(DaziPalette.ink)
            .frame(width: side * 0.035, height: side * 0.035)
            .offset(y: side * 0.055)

        if state == .alert {
            RoundedRectangle(cornerRadius: side * 0.01)
                .fill(DaziPalette.ink.opacity(0.7))
                .frame(width: side * 0.12, height: side * 0.025)
                .offset(y: side * 0.15)
        } else {
            Circle()
                .trim(from: 0.10, to: 0.40)
                .stroke(DaziPalette.ink.opacity(0.72), lineWidth: side * 0.022)
                .frame(width: side * 0.18, height: side * 0.16)
                .offset(y: side * 0.11)
        }

        if state == .thinking {
            Circle()
                .fill(accent)
                .frame(width: side * 0.055, height: side * 0.055)
                .offset(x: side * 0.34, y: -side * 0.16)
        }

        if state == .celebrate {
            Image(systemName: "sparkle")
                .font(.system(size: side * 0.18, weight: .bold))
                .foregroundStyle(accent)
                .offset(x: side * 0.34, y: -side * 0.18)
        }
    }

    private func eye(side: CGFloat) -> some View {
        Capsule()
            .fill(DaziPalette.ink)
            .frame(
                width: state == .gentle ? side * 0.07 : side * 0.055,
                height: side * 0.075
            )
    }
}

struct PositionDecisionReport: View {
    @EnvironmentObject private var store: OnboardingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if store.decidingPositions {
                ShanganLoadingCard()
            } else if let decision = store.positionDecision {
                let featured = featuredItem(in: decision)
                ShanganPlanHeader(decision: decision, featuredItem: featured)
                DecisionScorePanel(decision: decision, featuredItem: featured)
                if let featured {
                    DecisionEvidencePanel(item: featured)
                }
                PositionBucketBoard(decision: decision)
                ConditionsAndActionsPanel(decision: decision)
                regenerateButton
            } else {
                ShanganInputCard()
            }
        }
        .task {
            if store.positionDecision == nil,
               store.positionDecisionScore != nil,
               !store.decidingPositions
            {
                await store.loadPositionDecision()
            }
        }
    }

    private func featuredItem(in decision: PositionDecisionResponse) -> PositionDecisionItem? {
        let items = decision.recommendationBuckets.flatMap(\.items)
        if let selected = store.selectedDecisionItem,
           items.contains(where: { $0.position.positionId == selected.position.positionId })
        {
            return selected
        }
        if let positionId = store.selectedPosition?.positionId,
           let selectedFromManualPick = items.first(where: { $0.position.positionId == positionId })
        {
            return selectedFromManualPick
        }
        return decision.recommendationBuckets.first(where: { $0.bucket == "balanced" })?.items.first
            ?? decision.recommendationBuckets.first(where: { $0.bucket == "safe" })?.items.first
            ?? items.first
    }

    private var regenerateButton: some View {
        Button {
            Task { await store.loadPositionDecision() }
        } label: {
            Label("重新生成上岸方案", systemImage: "arrow.clockwise")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .disabled(store.decidingPositions || store.positionDecisionScore == nil)
    }
}

private struct ShanganPlanHeader: View {
    let decision: PositionDecisionResponse
    let featuredItem: PositionDecisionItem?

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            LumingMascotView(state: mascotState, size: 82)
                .layoutPriority(1)

            VStack(alignment: .leading, spacing: 8) {
                Label("鹿鸣给你的 AI 上岸方案", systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.white.opacity(0.82))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                Text(verdictTitle)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)

                Text(decision.summary)
                    .font(.callout)
                    .foregroundStyle(Color.white.opacity(0.90))
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

    private var verdictTitle: String {
        guard let gap = featuredItem?.scoreGap else {
            return "先确认岗位，再定训练路线"
        }
        if gap <= 0 {
            return "这条路可以稳扎稳打"
        }
        if gap <= 8 {
            return "这条路要把分差补扎实"
        }
        return "这条路能冲，但要换成拉分打法"
    }

    private var mascotState: LumingMascotState {
        guard let risk = featuredItem?.riskLevel else { return .thinking }
        switch risk {
        case "low":
            return .confident
        case "high":
            return .alert
        default:
            return .gentle
        }
    }
}

private struct DecisionScorePanel: View {
    let decision: PositionDecisionResponse
    let featuredItem: PositionDecisionItem?

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            DecisionMetricTile(
                title: "当前总分",
                value: String(format: "%.1f", decision.currentScore),
                caption: "行测 + 申论",
                tint: DaziPalette.brand
            )
            DecisionMetricTile(
                title: "决策目标分",
                value: featuredItem?.decisionTargetScore.map { String(format: "%.1f", $0) } ?? "待确认",
                caption: featuredItem?.decisionScoreBasis.map(Self.basisLabel) ?? "选择岗位后确定",
                tint: DaziPalette.positive
            )
            DecisionMetricTile(
                title: "目标分差",
                value: featuredItem?.scoreGap.map(scoreGapText) ?? "待确认",
                caption: featuredItem?.chanceLabel ?? "需要岗位样本",
                tint: riskTint
            )
        }
    }

    private var columns: [GridItem] {
        [GridItem(.adaptive(minimum: 104), spacing: 10)]
    }

    private var riskTint: Color {
        switch featuredItem?.riskLevel {
        case "low":
            return DaziPalette.positive
        case "high":
            return DaziPalette.alert
        default:
            return GuzhuPalette.azurite
        }
    }

    private static func basisLabel(_ basis: PositionDecisionScoreBasis) -> String {
        switch basis.basisType {
        case "position_target_score":
            return "同岗历史分"
        case "regional_major_prediction":
            return "地区专业预测"
        default:
            return "历史样本"
        }
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

private struct DecisionMetricTile: View {
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
                .font(.title3.monospacedDigit().weight(.heavy))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.78)
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
                .strokeBorder(tint.opacity(0.28), lineWidth: 1)
        )
    }
}

private struct DecisionEvidencePanel: View {
    let item: PositionDecisionItem

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "分数依据", systemImage: "doc.text.magnifyingglass")

            if let basis = item.decisionScoreBasis {
                Text(basis.evidenceSummary)
                    .font(.subheadline)
                    .foregroundStyle(DaziPalette.ink)
                    .fixedSize(horizontal: false, vertical: true)

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 118), spacing: 8)], alignment: .leading, spacing: 8) {
                    SmallDecisionChip(basisTypeLabel(basis), systemImage: "chart.xyaxis.line", tint: DaziPalette.brand)
                    SmallDecisionChip("样本 \(basis.sampleCount) 条", systemImage: "number", tint: GuzhuPalette.azurite)
                    SmallDecisionChip("岗位 \(basis.samplePositionCount) 个", systemImage: "building.2", tint: GuzhuPalette.azurite)
                    SmallDecisionChip(confidenceLabel(basis.confidence), systemImage: "checkmark.seal", tint: confidenceTint(basis.confidence))
                    if let year = basis.latestScoreYear {
                        SmallDecisionChip("\(year) 年样本", systemImage: "calendar", tint: GuzhuPalette.ochre)
                    }
                    if basis.safetyMargin > 0 {
                        SmallDecisionChip(String(format: "安全边际 %.1f", basis.safetyMargin), systemImage: "shield", tint: DaziPalette.positive)
                    }
                }

                if !basis.matchedDimensions.isEmpty {
                    Text("匹配维度：\(basis.matchedDimensions.map(dimensionLabel).joined(separator: "、"))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("该岗位分数样本还不足，鹿鸣不会把它包装成确定目标分。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .shanganSurface()
    }

    private func basisTypeLabel(_ basis: PositionDecisionScoreBasis) -> String {
        switch basis.basisType {
        case "position_target_score":
            return "同岗历史"
        case "regional_major_prediction":
            return "地区专业预测"
        default:
            return "历史样本"
        }
    }

    private func confidenceLabel(_ confidence: String) -> String {
        switch confidence {
        case "high":
            return "高置信"
        case "medium":
            return "中置信"
        case "low":
            return "低样本"
        default:
            return "待校验"
        }
    }

    private func confidenceTint(_ confidence: String) -> Color {
        switch confidence {
        case "high":
            return DaziPalette.positive
        case "low":
            return DaziPalette.alert
        default:
            return GuzhuPalette.ochre
        }
    }

    private func dimensionLabel(_ dimension: String) -> String {
        switch dimension {
        case "region":
            return "地区"
        case "city":
            return "城市"
        case "system":
            return "系统"
        case "major":
            return "专业"
        case "education":
            return "学历"
        case "political":
            return "政治面貌"
        case "serviceYears":
            return "基层年限"
        default:
            return dimension
        }
    }
}

private struct PositionBucketBoard: View {
    let decision: PositionDecisionResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle(title: "岗位组合", systemImage: "square.stack.3d.up")

            ForEach(decision.recommendationBuckets) { bucket in
                if !bucket.items.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(bucket.title)
                                .font(.headline)
                                .foregroundStyle(DaziPalette.ink)
                            Spacer()
                            Text(bucket.subtitle)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.trailing)
                        }

                        ForEach(Array(bucket.items.prefix(3))) { item in
                            PositionDecisionItemRow(item: item)
                        }

                        if bucket.items.count > 3 {
                            Text("另有 \(bucket.items.count - 3) 个候选岗位可继续比对。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }
}

private struct PositionDecisionItemRow: View {
    @EnvironmentObject private var store: OnboardingStore
    let item: PositionDecisionItem

    private var selected: Bool {
        store.selectedPosition?.positionId == item.position.positionId
    }

    var body: some View {
        Button {
            withAnimation(.snappy) {
                store.selectedPosition = item.position
                store.selectedDecisionItem = item
                store.selectedPositionDetail = nil
            }
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.position.name)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DaziPalette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(item.position.departmentFull)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? DaziPalette.brand : .secondary)
                        .accessibilityHidden(true)
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 8)], alignment: .leading, spacing: 8) {
                    SmallDecisionChip(item.decisionLabel, systemImage: "scope", tint: DaziPalette.brand)
                    if let target = item.decisionTargetScore {
                        SmallDecisionChip(String(format: "目标 %.1f", target), systemImage: "flag.checkered", tint: DaziPalette.positive)
                    }
                    SmallDecisionChip(item.chanceLabel, systemImage: "gauge.with.dots.needle.67percent", tint: riskTint)
                    if let gap = item.scoreGap {
                        SmallDecisionChip(scoreGapText(gap), systemImage: "plus.forwardslash.minus", tint: riskTint)
                    }
                }

                if let basis = item.decisionScoreBasis {
                    Text(basis.evidenceSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }

                ForEach(item.reasons.prefix(2), id: \.self) { reason in
                    Label(reason, systemImage: "checkmark")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Text(item.nextAction)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DaziPalette.brand)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(selected ? DaziPalette.brand : GuzhuPalette.cardStroke, lineWidth: selected ? 1.4 : 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(selected ? "已选目标岗位，\(item.position.name)" : "选择目标岗位，\(item.position.name)")
    }

    private var riskTint: Color {
        switch item.riskLevel {
        case "low":
            return DaziPalette.positive
        case "high":
            return DaziPalette.alert
        default:
            return GuzhuPalette.azurite
        }
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

private struct ConditionsAndActionsPanel: View {
    let decision: PositionDecisionResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !decision.unverifiedConditions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    SectionTitle(title: "需要确认", systemImage: "exclamationmark.triangle")
                    ForEach(decision.unverifiedConditions, id: \.self) { condition in
                        Label(condition, systemImage: "questionmark.circle")
                            .font(.caption)
                            .foregroundStyle(DaziPalette.alert)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            if !decision.nextActions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    SectionTitle(title: "下一步", systemImage: "arrow.forward.circle")
                    ForEach(decision.nextActions.prefix(3), id: \.self) { action in
                        Label(action, systemImage: "checkmark.seal")
                            .font(.caption)
                            .foregroundStyle(DaziPalette.brand)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .shanganSurface()
    }
}

private struct ShanganInputCard: View {
    @EnvironmentObject private var store: OnboardingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                LumingMascotView(state: .thinking, size: 64)
                VStack(alignment: .leading, spacing: 5) {
                    Text("鹿鸣需要你的当前分")
                        .font(.headline)
                        .foregroundStyle(DaziPalette.ink)
                    Text("填完行测和申论，才能按岗位目标分计算稳妥、均衡、冲刺组合。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if store.placementResult == nil, Double(store.xingceText) == nil {
                TextField("当前行测分，如 58.5", text: $store.xingceText)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
            }
            if Double(store.shenlunText) == nil {
                TextField("当前申论分，如 55", text: $store.shenlunText)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
            }

            Button {
                Task { await store.loadPositionDecision() }
            } label: {
                Label("生成 AI 上岸方案", systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)
            .disabled(store.positionDecisionScore == nil)
        }
        .shanganSurface()
    }
}

private struct ShanganLoadingCard: View {
    var body: some View {
        HStack(spacing: 14) {
            LumingMascotView(state: .thinking, size: 70)
            VStack(alignment: .leading, spacing: 8) {
                Text("鹿鸣正在排上岸路线")
                    .font(.headline)
                ProgressView("比对岗位条件、地区专业分数和当前分差…")
                    .font(.caption)
            }
        }
        .shanganSurface()
    }
}

private struct SectionTitle: View {
    let title: String
    let systemImage: String

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.subheadline.weight(.bold))
            .foregroundStyle(DaziPalette.ink)
    }
}

private struct SmallDecisionChip: View {
    let text: String
    let systemImage: String
    let tint: Color

    init(_ text: String, systemImage: String, tint: Color) {
        self.text = text
        self.systemImage = systemImage
        self.tint = tint
    }

    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(tint)
            .lineLimit(1)
            .minimumScaleFactor(0.78)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

private struct ShanganSurface: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(GuzhuPalette.cardStroke, lineWidth: 1)
            )
    }
}

private extension View {
    func shanganSurface() -> some View {
        modifier(ShanganSurface())
    }
}
