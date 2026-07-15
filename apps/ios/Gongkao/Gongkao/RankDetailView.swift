import SwiftUI

/// 品级详情页：把后端已有的品级/XP/解锁数据真正呈现给玩家。
/// 学习优先纪律：只展示正向进度（XP、连续理政、解锁），不突出民心/精力等惩罚观感字段。
struct RankDetailView: View {
    let rank: RankProfile
    let configs: [RankConfigItem]

    @Environment(\.dismiss) private var dismiss

    private var currentConfig: RankConfigItem? {
        configs.first { $0.rankLevel == rank.rankLevel }
    }

    private var nextConfig: RankConfigItem? {
        configs.first { $0.rankLevel == rank.rankLevel - 1 }
    }

    /// 精确进度：用当前品级阈值与下一品级阈值算出 0...1。
    private var xpFraction: Double {
        guard let next = rank.nextRankXpThreshold,
              let cur = currentConfig?.xpThreshold,
              next > cur else { return 0 }
        let span = Double(next - cur)
        return min(1, max(0, Double(rank.xp - cur) / span))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    headerCard
                    progressCard
                    if rank.consecutiveDays > 0 { streakCard }
                    unlocksCard
                    ladderCard
                }
                .padding(16)
            }
            .background(GuzhuPalette.paper)
            .navigationTitle("仕途")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("返回") { dismiss() }
                }
            }
        }
    }

    // MARK: 头部

    private var headerCard: some View {
        VStack(spacing: 10) {
            SealStamp(text: "官", color: GuzhuPalette.cinnabar, size: 64)
            Text(rank.rankName)
                .font(GuzhuFont.title(24))
                .foregroundStyle(GuzhuPalette.ink)
            Text("第 \(rank.rankLevel) 品 · 累计政绩 \(rank.xp) XP")
                .font(GuzhuFont.caption(13))
                .foregroundStyle(GuzhuPalette.faintInk)
        }
        .frame(maxWidth: .infinity)
        .parchmentCard()
    }

    // MARK: 进度

    private var progressCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("晋升进度")
                    .font(GuzhuFont.headline(15))
                    .foregroundStyle(GuzhuPalette.ink)
                Spacer()
                if let next = nextConfig {
                    Text("距 \(next.rankName)")
                        .font(GuzhuFont.caption(12))
                        .foregroundStyle(GuzhuPalette.faintInk)
                }
            }

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(GuzhuPalette.divider)
                    RoundedRectangle(cornerRadius: 6)
                        .fill(DaziGradient.actionLinear)
                        .frame(width: max(0, min(1, xpFraction)) * proxy.size.width)
                        .animation(.easeInOut(duration: 0.6), value: xpFraction)
                }
            }
            .frame(height: 12)

            HStack {
                Text(String(format: "%.0f%%", xpFraction * 100))
                    .font(GuzhuFont.headline(13))
                    .foregroundStyle(GuzhuPalette.cinnabar)
                Spacer()
                if let toNext = rank.xpToNext {
                    Text("还需 \(toNext) XP 升迁")
                        .font(GuzhuFont.caption(12))
                        .foregroundStyle(GuzhuPalette.faintInk)
                }
            }
        }
        .parchmentCard()
    }

    // MARK: 连续理政

    private var streakCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "flame.fill")
                .font(.system(size: 22))
                .foregroundStyle(GuzhuPalette.ochre)
            VStack(alignment: .leading, spacing: 2) {
                Text("连续理政 \(rank.consecutiveDays) 天")
                    .font(GuzhuFont.headline(15))
                    .foregroundStyle(GuzhuPalette.ink)
                Text("日拱一卒，功不唐捐")
                    .font(GuzhuFont.caption(12))
                    .foregroundStyle(GuzhuPalette.faintInk)
            }
            Spacer()
        }
        .parchmentCard()
    }

    // MARK: 解锁

    private var unlocksCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("已得恩典")
                .font(GuzhuFont.headline(15))
                .foregroundStyle(GuzhuPalette.ink)

            // 后端 unlock_features 已是累计解锁（当前品级及以下全部），直接展示
            let current = rank.unlockFeatures
            if current.isEmpty {
                Text("初入仕途，恩典将随品级渐开")
                    .font(GuzhuFont.caption(13))
                    .foregroundStyle(GuzhuPalette.faintInk)
            } else {
                ForEach(current, id: \.self) { feature in
                    HStack(spacing: 8) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(GuzhuPalette.celadon)
                        Text(RankUnlockCatalog.label(for: feature))
                            .font(GuzhuFont.body(14))
                            .foregroundStyle(GuzhuPalette.ink)
                    }
                }
            }

            if let next = nextConfig, !next.unlockFeatures.isEmpty {
                Divider().padding(.vertical, 4)
                Text("升任 \(next.rankName) 将解锁")
                    .font(GuzhuFont.caption(12))
                    .foregroundStyle(GuzhuPalette.cinnabar)
                ForEach(next.unlockFeatures, id: \.self) { feature in
                    HStack(spacing: 8) {
                        Image(systemName: "lock.fill")
                            .foregroundStyle(GuzhuPalette.faintInk)
                        Text(RankUnlockCatalog.label(for: feature))
                            .font(GuzhuFont.body(14))
                            .foregroundStyle(GuzhuPalette.faintInk)
                    }
                }
            }
        }
        .parchmentCard()
    }

    // MARK: 九品阶梯

    private var ladderCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("九品阶梯")
                .font(GuzhuFont.headline(15))
                .foregroundStyle(GuzhuPalette.ink)

            let ordered = configs.sorted { $0.rankLevel < $1.rankLevel }
            ForEach(ordered, id: \.rankCode) { cfg in
                HStack(spacing: 10) {
                    Text("\(cfg.rankLevel)品")
                        .font(GuzhuFont.caption(11))
                        .foregroundStyle(GuzhuPalette.faintInk)
                        .frame(width: 28, alignment: .leading)
                    Text(cfg.rankName)
                        .font(GuzhuFont.body(14))
                        .foregroundStyle(cfg.rankLevel == rank.rankLevel ? GuzhuPalette.cinnabar : GuzhuPalette.ink)
                    Spacer()
                    if cfg.rankLevel == rank.rankLevel {
                        Text("现任")
                            .font(GuzhuFont.caption(11))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(GuzhuPalette.cinnabar)
                            .clipShape(Capsule())
                    } else if cfg.rankLevel < rank.rankLevel {
                        Text("已历")
                            .font(GuzhuFont.caption(11))
                            .foregroundStyle(GuzhuPalette.celadon)
                    } else {
                        Text("\(cfg.xpThreshold) XP")
                            .font(GuzhuFont.caption(11))
                            .foregroundStyle(GuzhuPalette.faintInk)
                    }
                }
            }
        }
        .parchmentCard()
    }
}

/// 解锁功能键 → 友好中文名（未知键回退原值）。
enum RankUnlockCatalog {
    static func label(for key: String) -> String {
        let map: [String: String] = [
            "wrong_question_book": "错题案卷簿",
            "government_cards": "政务锦囊全库",
            "ai_counselor_basic": "基础 AI 辅政",
            "radar_chart": "能力雷达图",
            "ai_summary": "AI 周报综述",
            "essay_boss": "申论精批",
            "god_view_partial": "封地沙盘（局部）",
            "ai_unlimited": "搭子无限问政",
            "all_unlocked": "全境解锁",
            "companion_extra_quota": "搭子每日多问 1 次",
            "advanced_diagnosis": "弱项深度诊断",
            "method_card_library": "锦囊全库查阅",
            "mock_exam_detailed": "会试逐题精讲",
            "priority_review": "错题优先批改",
            "coach_persona": "幕僚个性化辅政",
        ]
        return map[key] ?? key
    }
}
