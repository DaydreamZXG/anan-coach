import SwiftUI
import UIKit

// MARK: - 升迁庆祝（游戏最大爽点：品级提升全屏弹层）

struct RankUpPayload: Identifiable {
    let oldRankName: String
    let newRankName: String
    let newRankLevel: Int
    let unlocked: [String]

    var id: String { "rankup-\(newRankName)" }
}

// MARK: - 完成庆祝层（情绪价值层核心：真实成就 + 热烈反馈，不做虚假夸奖）

struct CelebrationPayload: Identifiable {
    let correct: Int
    let total: Int
    let taskTitle: String

    var id: String { "\(taskTitle)-\(correct)-\(total)" }

    /// 文案分档基于真实正确率：高分热烈、中分肯定努力、低分温柔接住。
    /// 纪律：不撒谎——低正确率绝不说"表现很棒"。
    var headline: String {
        guard total > 0 else { return "县令，这一步走完了" }
        let rate = Double(correct) / Double(total)
        switch rate {
        case 0.85...: return "县令，漂亮！稳稳拿下"
        case 0.6..<0.85: return "县令，这把稳了，继续这么攒"
        default: return "县令，难的地方你没绕开"
        }
    }

    var subline: String {
        guard total > 0 else { return "县令，把任务做完本身，就是今天最难的部分。" }
        let rate = Double(correct) / Double(total)
        switch rate {
        case 0.85...:
            return "县令，\(total) 题对 \(correct)，这个知识点你已经握在手里了。"
        case 0.6..<0.85:
            return "县令，\(total) 题对 \(correct)。错的那几道搭子记下了，下次计划会帮你补回来。"
        default:
            return "县令，\(total) 题对 \(correct)。别慌——每道错题都进了你的地图，弱点暴露得越早越值钱。"
        }
    }

    var isHighScore: Bool {
        total > 0 && Double(correct) / Double(total) >= 0.85
    }
}

struct CelebrationOverlay: View {
    let payload: CelebrationPayload
    let onDismiss: () -> Void
    @State private var appeared = false

    var body: some View {
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
                .onTapGesture { onDismiss() }

            if payload.isHighScore {
                ConfettiField()
                    .allowsHitTesting(false)
                    .ignoresSafeArea()
            }

            VStack(spacing: 16) {
                Image(systemName: payload.isHighScore ? "trophy.fill" : "checkmark.seal.fill")
                    .font(.system(size: 44))
                    .foregroundStyle(payload.isHighScore ? DaziPalette.brand : DaziPalette.positive)
                    .scaleEffect(appeared ? 1 : 0.4)

                VStack(spacing: 8) {
                    Text(payload.headline)
                        .font(.title3.bold())
                        .multilineTextAlignment(.center)
                    Text(payload.subline)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
                }

                Button {
                    onDismiss()
                } label: {
                    Text("收下，继续")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .tint(DaziPalette.brand)
            }
            .padding(24)
            .frame(maxWidth: 340)
            .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 20))
            .padding(32)
            .scaleEffect(appeared ? 1 : 0.85)
            .opacity(appeared ? 1 : 0)
        }
        .onAppear {
            withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
                appeared = true
            }
            let generator = UINotificationFeedbackGenerator()
            generator.notificationOccurred(.success)
        }
    }
}

// MARK: - 升迁庆祝弹层（印章盖下 + 品级变化 + 恩典预告）

struct RankUpOverlay: View {
    let payload: RankUpPayload
    let onDismiss: () -> Void
    @State private var appeared = false
    @State private var stamped = false

    var body: some View {
        ZStack {
            Color.black.opacity(0.5)
                .ignoresSafeArea()
                .onTapGesture { onDismiss() }

            VStack(spacing: 18) {
                Text("晋 升")
                    .font(GuzhuFont.title(20))
                    .foregroundStyle(GuzhuPalette.cinnabar)
                    .opacity(appeared ? 1 : 0)

                ZStack {
                    SealStamp(
                        text: "升",
                        color: GuzhuPalette.cinnabar,
                        size: 110
                    )
                    .scaleEffect(stamped ? 1 : 2.4)
                    .opacity(stamped ? 0.9 : 0)
                    .rotationEffect(.degrees(stamped ? 0 : -12))
                }
                .frame(width: 120, height: 120)

                VStack(spacing: 6) {
                    Text(payload.oldRankName)
                        .font(GuzhuFont.body(15))
                        .foregroundStyle(GuzhuPalette.faintInk)
                        .strikethrough()
                    Image(systemName: "arrow.down")
                        .font(.system(size: 14))
                        .foregroundStyle(GuzhuPalette.cinnabar)
                        .rotationEffect(.degrees(180))
                    Text(payload.newRankName)
                        .font(GuzhuFont.title(24))
                        .foregroundStyle(GuzhuPalette.ink)
                }
                .opacity(appeared ? 1 : 0)

                if !payload.unlocked.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("新得恩典")
                            .font(GuzhuFont.caption(12))
                            .foregroundStyle(GuzhuPalette.cinnabar)
                        ForEach(payload.unlocked, id: \.self) { key in
                            HStack(spacing: 6) {
                                Image(systemName: "checkmark.seal.fill")
                                    .foregroundStyle(GuzhuPalette.celadon)
                                Text(RankUnlockCatalog.label(for: key))
                                    .font(GuzhuFont.body(13))
                                    .foregroundStyle(GuzhuPalette.ink)
                            }
                        }
                    }
                    .padding(.top, 4)
                    .opacity(appeared ? 1 : 0)
                }

                Button {
                    onDismiss()
                } label: {
                    Text("领旨谢恩")
                        .font(GuzhuFont.headline(16))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .tint(GuzhuPalette.cinnabar)
                .opacity(appeared ? 1 : 0)
            }
            .padding(28)
            .frame(maxWidth: 340)
            .background(GuzhuPalette.card, in: RoundedRectangle(cornerRadius: 20))
            .padding(32)
            .scaleEffect(appeared ? 1 : 0.9)
            .opacity(appeared ? 1 : 0)
        }
        .onAppear {
            withAnimation(.spring(response: 0.5, dampingFraction: 0.75)) {
                appeared = true
            }
            // 印章盖下：略延迟，营造"落印"感
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
                withAnimation(.spring(response: 0.32, dampingFraction: 0.55)) {
                    stamped = true
                }
            }
            let generator = UINotificationFeedbackGenerator()
            generator.notificationOccurred(.success)
        }
    }
}

// MARK: - 彩带粒子（纯 SwiftUI，仅高分触发）

private struct ConfettiField: View {
    @State private var start = false
    private let pieces: [ConfettiPiece] = (0..<36).map { _ in ConfettiPiece.random() }

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                ForEach(pieces) { piece in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(piece.color)
                        .frame(width: piece.size.width, height: piece.size.height)
                        .rotationEffect(.degrees(start ? piece.endRotation : piece.startRotation))
                        .position(
                            x: proxy.size.width * piece.x,
                            y: start ? proxy.size.height + 40 : -40
                        )
                        .animation(
                            .easeIn(duration: piece.duration).delay(piece.delay),
                            value: start
                        )
                }
            }
        }
        .onAppear { start = true }
    }
}

private struct ConfettiPiece: Identifiable {
    let id = UUID()
    let x: Double
    let size: CGSize
    let color: Color
    let startRotation: Double
    let endRotation: Double
    let duration: Double
    let delay: Double

    static func random() -> ConfettiPiece {
        let palette: [Color] = [
            DaziPalette.brand,
            DaziPalette.positive,
            DaziPalette.brand.opacity(0.6),
            Color(.systemYellow),
        ]
        return ConfettiPiece(
            x: .random(in: 0.05...0.95),
            size: CGSize(width: .random(in: 6...10), height: .random(in: 10...16)),
            color: palette.randomElement() ?? DaziPalette.brand,
            startRotation: .random(in: -90...90),
            endRotation: .random(in: 360...900),
            duration: .random(in: 1.6...2.6),
            delay: .random(in: 0...0.5)
        )
    }
}
