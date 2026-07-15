import SwiftUI

// MARK: - 设计系统预览（DEBUG 入口）
//
// 集中展示搭子设计系统 v2（蓝紫渐变主题）的令牌与核心组件，供视觉方向确认。
// 入口：ProfileView → "设计系统预览（开发）"。

struct DesignSystemPreview: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                heroSection
                tokenSection
                logoSection
                typographySection
                cardSection
                componentSection
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 24)
            .frame(maxWidth: 920, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(DaziPalette.paper)
        .navigationTitle("设计系统预览")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: 顶部：logo + 标语 + 当前模式
    private var heroSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 16) {
                Image("Logo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 64, height: 64)
                VStack(alignment: .leading, spacing: 4) {
                    Text("公考搭子")
                        .font(.title.weight(.bold))
                        .foregroundStyle(DaziPalette.ink)
                    Text(scheme == .dark ? "深夜书房 · 夜间模式" : "晨光科技 · 日间模式")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            Text("一个懂你为什么错的备考教练。蓝紫渐变，干净有力量。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineSpacing(3)
        }
        .paperCard(padding: 20)
    }

    // MARK: 令牌色卡
    private var tokenSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            DocketHeader(title: "语义令牌", subtitle: "明暗自适应")

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 10)], spacing: 10) {
                tokenSwatch("paper 底色", DaziPalette.paper)
                tokenSwatch("card 卡面", DaziPalette.card)
                tokenSwatch("ink 正文", DaziPalette.ink)
                tokenSwatch("brand 主色", DaziPalette.brand)
                tokenSwatch("accentBlue 蓝", DaziPalette.accentBlue)
                tokenSwatch("accentPurple 紫", DaziPalette.accentPurple)
                tokenSwatch("positive 正反馈", DaziPalette.positive)
                tokenSwatch("alert 警示", DaziPalette.alert)
                tokenSwatch("cardStroke 描边", DaziPalette.cardStroke)
                tokenSwatch("onHighlight", DaziPalette.onHighlight)
                tokenSwatch("行测 track", DaziPalette.track("行测"))
                tokenSwatch("申论 track", DaziPalette.track("申论"))
            }
        }
    }

    private func tokenSwatch(_ name: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(color)
                .frame(height: 44)
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
                )
            Text(name)
                .font(.caption2.weight(.medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    // MARK: Logo 尺寸展示
    private var logoSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            DocketHeader(title: "Logo · D字母渐变", subtitle: "无级缩放")

            HStack(alignment: .center, spacing: 20) {
                Spacer()
                DaziLogoView(size: 40)
                DaziLogoView(size: 56)
                DaziLogoView(size: 80)
                DaziLogoView(size: 120)
                Spacer()
            }
            .paperCard(padding: 20)
        }
    }

    // MARK: 字体层级
    private var typographySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            DocketHeader(title: "字体层级", subtitle: "无衬线 · 干净高效")

            VStack(alignment: .leading, spacing: 10) {
                Text("距离为人民服务，又近了一步")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(DaziPalette.ink)
                Text("今日 5 项安排 · 已完成 2 项")
                    .font(.headline)
                    .foregroundStyle(DaziPalette.ink)
                Text("搭子的每个判断都注明数据出处，不拍脑袋。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text("基于规则排程 · 数据来源：考生近 7 日做题记录")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .paperCard(padding: 16)
        }
    }

    // MARK: 卡片语言
    private var cardSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            DocketHeader(title: "卡片语言", subtitle: "12px continuous · 唯一节奏")

            VStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("普通卡 panelStyle")
                        .font(.headline)
                    Text("极淡描边 + 柔和阴影分层，明暗自适应。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .panelStyle()

                VStack(alignment: .leading, spacing: 6) {
                    Text("纸面卡 paperCard")
                        .font(.headline)
                    Text("与 panelStyle 同源，padding 可调。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .paperCard(padding: 16)
            }
        }
    }

    // MARK: 组件样例
    private var componentSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            DocketHeader(title: "核心组件", subtitle: "渐变底强卡 · CTA · 双轨")

            VStack(spacing: 14) {
                CoachBriefCard(plan: DesignSystemFixtures.briefPlan)

                TrackTimelineBar(items: DesignSystemFixtures.trackMix)

                PrimaryCTABar(
                    title: "开始第 3 项 · 言语理解与表达",
                    subtitle: "45 分钟 · 逻辑填空实词辨析",
                    systemImage: "play.fill"
                ) {}

                PrimaryCTABar(
                    title: "今日政务全部完成",
                    subtitle: "明天见",
                    systemImage: "checkmark.seal.fill"
                )
            }
        }
    }
}

// MARK: - 预览用 Mock 数据

enum DesignSystemFixtures {
    static let briefPlan = TodayTaskstream(
        date: "2026-07-06",
        headline: "今天补言语的逻辑填空",
        target: .init(exam: "国考", positionGroup: "综合管理类", scoreGap: 12),
        trackMix: trackMix,
        primaryAction: .init(title: "开始", subtitle: "第 1 项"),
        rationale: "近 7 日错题集中在实词辨析，搭子判断这是提分最快的缺口。先把方法卡过一遍，再做 8 题定点训练。",
        tasks: [],
        completedTaskIds: []
    )

    static let trackMix: [BackendTrackMix] = [
        .init(track: "行测", minutes: 45, reason: "言语理解 · 逻辑填空实词辨析。近 7 日该子模块错题 12 题，是当前最大缺口。"),
        .init(track: "申论", minutes: 30, reason: "申论素材积累。今天聚焦乡村振兴主题的规范表达。"),
    ]
}

#Preview("日间") {
    NavigationStack {
        DesignSystemPreview()
    }
    .preferredColorScheme(.light)
}

#Preview("夜间") {
    NavigationStack {
        DesignSystemPreview()
    }
    .preferredColorScheme(.dark)
}
