import SwiftUI

struct PositionRow: View {
    let position: PositionSummary
    let selected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(position.name)
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Spacer()
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(DaziPalette.brand)
                    }
                }
                Text(position.departmentFull)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                HStack(spacing: 10) {
                    Label(position.city ?? position.region, systemImage: "mappin")
                    Label("招 \(position.headcount) 人", systemImage: "person")
                    if position.hasExtraExam == true {
                        Label("加试", systemImage: "exclamationmark.bubble")
                            .foregroundStyle(DaziPalette.alert)
                    }
                    if let score = position.entryScore.averageEntryScore {
                        Label(String(format: "近年进面 %.1f", score), systemImage: "chart.line.uptrend.xyaxis")
                    } else {
                        Text("暂无分数数据")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            .padding(14)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(selected ? DaziPalette.brand : .clear, lineWidth: 1.5)
            )
        }
        .buttonStyle(.plain)
    }
}

struct PositionDetailSheet: View {
    let detail: PositionDetail
    let selected: Bool
    let onSelect: () -> Void
    @Environment(\.dismiss) private var dismiss

    private static func paperTypeLabel(_ code: String?) -> String? {
        guard let code, !code.isEmpty else { return nil }
        switch code {
        case "provincial": return "副省级以上（综合管理类）"
        case "prefectural": return "地市级以下（综合管理类）"
        case "administrativeLaw": return "行政执法类"
        default: return code
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(detail.name).font(.title3.bold())
                        Text(detail.departmentFull)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        HStack(spacing: 10) {
                            Label(detail.city ?? detail.region, systemImage: "mappin")
                            Label("招 \(detail.headcount) 人", systemImage: "person")
                            if detail.hasExtraExam {
                                Label("需加试", systemImage: "exclamationmark.bubble")
                                    .foregroundStyle(DaziPalette.alert)
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    OnboardingCard(title: "报考要求") {
                        VStack(alignment: .leading, spacing: 10) {
                            detailRow("地区", detail.city.map { "\(detail.region) · \($0)" } ?? detail.region)
                            detailRow("学历要求", detail.educationMin)
                            detailRow("专业要求", detail.majorRequired)
                            detailRow("政治面貌", detail.politicalReq)
                            detailRow("服务年限", detail.serviceMinYrs.map { "\($0) 年" })
                            detailRow("户口要求", detail.hukouReq)
                            detailRow("卷别", Self.paperTypeLabel(detail.paperType))
                            if let remark = detail.remark, !remark.isEmpty {
                                detailRow("备注", remark)
                            }
                        }
                    }

                    if let entry = detail.entryScore {
                        OnboardingCard(title: "进面分参考") {
                            VStack(alignment: .leading, spacing: 6) {
                                if !entry.years.isEmpty {
                                    Text("年份：\(entry.years.map(String.init).joined(separator: "、"))")
                                        .font(.subheadline)
                                }
                                if let latest = entry.latestEntryScore {
                                    Text(String(format: "去年进面分 %.1f", latest))
                                        .font(.subheadline.monospacedDigit())
                                }
                                if let avg = entry.averageEntryScore {
                                    Text(String(format: "近年进面均分 %.1f", avg))
                                        .font(.subheadline.monospacedDigit())
                                }
                                if let target = entry.targetScore {
                                    Text(String(format: "目标分 %.1f", target))
                                        .font(.subheadline.monospacedDigit())
                                        .foregroundStyle(DaziPalette.brand)
                                }
                                Text("样本数 \(entry.sourceCount)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }

                    Button(action: onSelect) {
                        Text(selected ? "取消选择该岗位" : "选为目标岗位")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(DaziPalette.brand)
                }
                .padding(20)
            }
            .background(DaziPalette.paper)
            .navigationTitle("岗位详情")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func detailRow(_ title: String, _ value: String?) -> some View {
        if let value, !value.isEmpty {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 12)
                Text(value)
                    .font(.subheadline)
                    .multilineTextAlignment(.trailing)
            }
        }
    }
}

struct OnboardingBaselineStep: View {
    @EnvironmentObject private var store: OnboardingStore

    private let modules = ["言语理解与表达", "数量关系", "判断推理", "资料分析", "常识判断"]

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("告诉搭子你现在的水平，第一天的计划就不用瞎猜。")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Picker("基线方式", selection: $store.baselineChoice) {
                Text("自报分数").tag("self_report")
                Text("做摸底卷").tag("placement_test")
                Text("跳过").tag("none")
            }
            .pickerStyle(.segmented)

            switch store.baselineChoice {
            case "self_report":
                OnboardingCard(title: "最近一次考试成绩（考过就填）") {
                    TextField("行测分数，如 58.5", text: $store.xingceText)
                        .keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                    TextField("申论分数，如 55（可不填）", text: $store.shenlunText)
                        .keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                }
                OnboardingCard(title: "自评弱项（可多选）") {
                    ForEach(modules, id: \.self) { module in
                        Toggle(module, isOn: Binding(
                            get: { store.weakModules.contains(module) },
                            set: { on in
                                if on { store.weakModules.insert(module) } else { store.weakModules.remove(module) }
                            }
                        ))
                    }
                }
            case "placement_test":
                PlacementTestPanel()
            default:
                Text("跳过后，搭子第一天按国考高分值模块给出起步安排，并如实告诉你这不是个性化计划。做题数据积累后会自动校准。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            }
        }
    }
}

struct PlacementTestPanel: View {
    @EnvironmentObject private var store: OnboardingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let result = store.placementResult {
                OnboardingCard(title: "摸底结果") {
                    Text(String(format: "预估行测 %.1f 分", result.estimatedXingceScore))
                        .font(.title3.bold().monospacedDigit())
                        .foregroundStyle(DaziPalette.brand)
                    ForEach(result.moduleResults) { module in
                        HStack {
                            Text(module.moduleName).font(.subheadline)
                            Spacer()
                            Text("\(module.correct)/\(module.total)")
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(module.accuracy < 0.5 ? DaziPalette.alert : .secondary)
                        }
                    }
                }
            } else if let placement = store.placement {
                if let question = placement.questions[safe: store.placementIndex] {
                    OnboardingCard(title: "第 \(store.placementIndex + 1)/\(placement.questionCount) 题 · \(question.moduleName ?? "")") {
                        if let material = question.materialText, !material.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("以下材料回答问题")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                ScrollView {
                                    QuestionRichText(
                                        plainText: material,
                                        htmlText: question.materialHtml,
                                        font: .subheadline,
                                        uiFont: .preferredFont(forTextStyle: .subheadline),
                                        lineSpacing: 3,
                                        foregroundColor: DaziPalette.ink
                                    )
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .frame(maxHeight: 200)
                            }
                            .padding(12)
                            .background(DaziPalette.card.opacity(0.6), in: RoundedRectangle(cornerRadius: 10))
                        }
                        QuestionRichText(
                            plainText: question.stem,
                            htmlText: question.stemHtml,
                            font: .subheadline,
                            uiFont: .preferredFont(forTextStyle: .subheadline),
                            lineSpacing: 3,
                            foregroundColor: DaziPalette.ink
                        )
                        ForEach(question.options) { option in
                            Button {
                                store.placementPicks[question.questionId] = option.label
                                if store.placementIndex < placement.questions.count - 1 {
                                    store.placementIndex += 1
                                } else {
                                    Task { await store.submitPlacement() }
                                }
                            } label: {
                                HStack(alignment: .top, spacing: 8) {
                                    Text(option.label).font(.subheadline.bold())
                                    Text(option.displayText).font(.subheadline)
                                        .multilineTextAlignment(.leading)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                                .background(
                                    store.placementPicks[question.questionId] == option.label
                                        ? DaziPalette.brand.opacity(0.12) : Color(.systemFill).opacity(0.4),
                                    in: RoundedRectangle(cornerRadius: 10)
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            } else {
                Button {
                    Task { await store.loadPlacementTest() }
                } label: {
                    Label("开始 30 题摸底（约 35 分钟）", systemImage: "stopwatch")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(store.busy)
            }
        }
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

struct OnboardingTargetStep: View {
    @EnvironmentObject private var store: OnboardingStore

    private var effectiveEntryScore: EntryScoreStats? {
        if let detail = store.selectedPositionDetail,
           store.selectedPosition?.positionId == detail.positionId
        {
            return detail.entryScore
        }
        return store.selectedPosition?.entryScore
    }

    private var suggestedTargetScore: Double? {
        store.selectedDecisionItem?.decisionTargetScore ?? effectiveEntryScore?.targetScore
    }

    private var targetScorePlaceholder: String {
        suggestedTargetScore.map { String(format: "%.1f", $0) } ?? "如 130"
    }

    private var targetScoreHelp: String {
        if store.selectedDecisionItem?.decisionTargetScore != nil {
            return "留空则采用鹿鸣方案中的决策目标分；手动填写会覆盖它。"
        }
        if effectiveEntryScore?.targetScore != nil {
            return "留空则采用岗位近年分数目标；手动填写会覆盖它。"
        }
        return "未选岗位或样本不足时，请手动填写目标总分。"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PositionDecisionReport()

            OnboardingCard(title: "生成首日计划前确认") {
                if let position = store.selectedPosition {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(position.name)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DaziPalette.ink)
                        Text(position.departmentFull)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                        if let target = suggestedTargetScore {
                            Label(String(format: "将按 %.1f 分写入计划目标", target), systemImage: "flag.checkered")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DaziPalette.brand)
                        }
                    }
                } else {
                    Label("还没有选定目标岗位，首日计划会缺少岗位分差锚点。", systemImage: "exclamationmark.triangle")
                        .font(.subheadline)
                        .foregroundStyle(DaziPalette.alert)
                        .fixedSize(horizontal: false, vertical: true)
                }

                TextField(
                    targetScorePlaceholder,
                    text: $store.targetScoreText
                )
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
                Text(targetScoreHelp)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let xingce = Double(store.xingceText) {
                Text(String(format: "你自报行测 %.1f 分，完成后搭子会基于它排第一天的计划。", xingce))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else if let result = store.placementResult {
                Text(String(format: "摸底预估行测 %.1f 分，完成后搭子会基于它排第一天的计划。", result.estimatedXingceScore))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
