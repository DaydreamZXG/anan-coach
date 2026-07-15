import SwiftUI

struct StatusBarView: View {
    let rank: RankProfile?
    let scoreGap: Double?
    let todayTaskCount: Int
    let completedTaskCount: Int
    /// 0...1 的品级进度（由调用方用 rankConfigs 计算）。nil 表示暂不展示进度。
    let xpProgress: Double?
    /// 下一品级提示文案，如"距八品县丞 380 XP"。
    let nextRankHint: String?
    var onRankTap: () -> Void = {}

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                rankBadge
                Spacer()
                scoreGapBadge
                Divider().frame(height: 20)
                progressBadge
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            // 品级经验进度条：让玩家第一次"看见自己在变强"
            if let xpProgress {
                GeometryReader { proxy in
                    ZStack(alignment: .leading) {
                        Rectangle()
                            .fill(GuzhuPalette.divider)
                        Rectangle()
                            .fill(DaziGradient.actionLinear)
                            .frame(width: max(0, min(1, xpProgress)) * proxy.size.width)
                    }
                    .frame(height: 3)
                }
                .frame(height: 3)
                .padding(.horizontal, 16)
                .padding(.bottom, 6)
            }
        }
        .background(GuzhuPalette.card)
        .overlay(
            Rectangle()
                .fill(GuzhuPalette.divider)
                .frame(height: 1),
            alignment: .bottom
        )
    }

    private var rankBadge: some View {
        Button {
            onRankTap()
        } label: {
            HStack(spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .stroke(GuzhuPalette.cinnabar, lineWidth: 1.5)
                        .frame(width: 32, height: 32)
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .stroke(GuzhuPalette.cinnabar.opacity(0.6), lineWidth: 1)
                        .padding(3)
                        .frame(width: 32, height: 32)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(rank?.rankName ?? "九品县令")
                        .font(GuzhuFont.headline(15))
                        .foregroundStyle(GuzhuPalette.ink)
                    if let nextRankHint {
                        Text(nextRankHint)
                            .font(GuzhuFont.caption(10))
                            .foregroundStyle(GuzhuPalette.faintInk)
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }

    // 英雄指标：距上岸还差多少分（后端 TodayTaskstream.target.scoreGap）
    // 民心/精力保留在后端数据里，MVP 不在顶栏展示，避免惩罚/反目标观感。
    private var scoreGapBadge: some View {
        HStack(spacing: 6) {
            Image(systemName: "flag.checkered")
                .foregroundStyle(GuzhuPalette.cinnabar)
                .font(.system(size: 14))
            VStack(alignment: .leading, spacing: 1) {
                Text("距上岸")
                    .font(GuzhuFont.caption(11))
                    .foregroundStyle(GuzhuPalette.faintInk)
                Text(scoreGap.map { String(format: "%.1f 分", $0) } ?? "—")
                    .font(GuzhuFont.headline(14))
                    .foregroundStyle(GuzhuPalette.cinnabar)
            }
        }
    }

    private var progressBadge: some View {
        HStack(spacing: 6) {
            Image(systemName: "scroll.fill")
                .foregroundStyle(GuzhuPalette.azurite)
                .font(.system(size: 14))
            VStack(alignment: .leading, spacing: 1) {
                Text("今日政务")
                    .font(GuzhuFont.caption(11))
                    .foregroundStyle(GuzhuPalette.faintInk)
                Text("\(completedTaskCount)/\(todayTaskCount)")
                    .font(GuzhuFont.headline(14))
                    .foregroundStyle(GuzhuPalette.ink)
            }
        }
    }
}

#Preview {
    VStack {
        StatusBarView(
            rank: RankProfile(
                userId: "1",
                rankCode: "nine_pin_xianling",
                rankLevel: 9,
                rankName: "九品县令",
                xp: 120,
                fame: 58,
                stamina: 85,
                consecutiveDays: 3,
                demerits: 0,
                uiTheme: "bamboo",
                nextRankName: "八品县丞",
                nextRankXpThreshold: 500,
                xpToNext: 380,
                unlockFeatures: []
            ),
            scoreGap: 12.3,
            todayTaskCount: 10,
            completedTaskCount: 3,
            xpProgress: 0.24,
            nextRankHint: "距八品县丞 380 XP",
            onRankTap: {}
        )
        Spacer()
    }
    .background(GuzhuPalette.paper)
}
