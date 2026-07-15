import SwiftUI
import UIKit

// MARK: - 授品仪式（Onboarding 收官：世界观开篇 + 授九品 + 搭子首登场）
//
// 新用户完成摸底与目标设定后，以一场「授品」仪式收束 onboarding：
// 1) 古风世界观开篇 —— 营造「入仕」沉浸感
// 2) 印玺落下 —— 授「九品县令」起始品阶
// 3) 师爷登场 —— 沈砚自报家门，与主公结伴
// 纪律：游戏化只服务坚持；仪式短促克制，不抢学习注意力。

struct InvestitureView: View {
    let onAccept: () -> Void

    @State private var phase = 0
    @State private var stamped = false
    @State private var appeared = false

    var body: some View {
        ZStack {
            // 暗色竹简底，烘托仪式感
            GuzhuPalette.ink.opacity(0.94)
                .ignoresSafeArea()

            VStack(spacing: 26) {
                Spacer(minLength: 0)

                // Phase 1: 世界观开篇
                VStack(spacing: 10) {
                    Text("治 国")
                        .font(.system(size: 34, weight: .heavy, design: .serif))
                        .foregroundStyle(GuzhuPalette.cinnabar)
                        .opacity(phase >= 1 ? 1 : 0)

                    Text("天下承平，科举重开。\n布衣亦可致仕，唯才是举。")
                        .font(.system(size: 15, weight: .regular, design: .serif))
                        .foregroundStyle(GuzhuPalette.paper.opacity(0.8))
                        .multilineTextAlignment(.center)
                        .lineSpacing(5)
                        .opacity(phase >= 2 ? 1 : 0)
                }

                // Phase 2: 印玺落下 —— 授九品
                VStack(spacing: 12) {
                    ZStack {
                        SealStamp(
                            text: "品",
                            color: GuzhuPalette.cinnabar,
                            size: 120
                        )
                        .scaleEffect(stamped ? 1 : 2.6)
                        .opacity(stamped ? 0.92 : 0)
                        .rotationEffect(.degrees(stamped ? 0 : -14))
                    }
                    .frame(width: 130, height: 130)

                    VStack(spacing: 4) {
                        Text("授 九品县令")
                            .font(.system(size: 22, weight: .bold, design: .serif))
                            .foregroundStyle(GuzhuPalette.paper)
                        Text("自此入仕，县中政务皆由主公批阅")
                            .font(.system(size: 13, design: .serif))
                            .foregroundStyle(GuzhuPalette.paper.opacity(0.65))
                    }
                    .opacity(phase >= 3 ? 1 : 0)
                }
                .opacity(phase >= 2 ? 1 : 0)

                // Phase 3: 师爷登场
                HStack(spacing: 14) {
                    ZStack {
                        Circle()
                            .fill(DaziGradient.actionLinear)
                            .frame(width: 50, height: 50)
                        Image(systemName: "person.fill")
                            .font(.system(size: 24, weight: .semibold))
                            .foregroundStyle(.white)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text("沈师爷 · 沈砚")
                            .font(.system(size: 16, weight: .semibold, design: .serif))
                            .foregroundStyle(GuzhuPalette.cinnabar)
                        Text("主公，沈砚在此。往后这案牍，我陪您一道批。")
                            .font(.system(size: 13))
                            .foregroundStyle(GuzhuPalette.paper.opacity(0.82))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(16)
                .background(GuzhuPalette.paper.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .stroke(GuzhuPalette.cinnabar.opacity(0.3), lineWidth: 1)
                )
                .opacity(phase >= 4 ? 1 : 0)
                .offset(y: phase >= 4 ? 0 : 12)

                Spacer(minLength: 0)

                Button {
                    onAccept()
                } label: {
                    Text("领旨上任")
                        .font(.system(size: 17, weight: .semibold, design: .serif))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .tint(GuzhuPalette.cinnabar)
                .opacity(phase >= 4 ? 1 : 0)
                .disabled(phase < 4)
            }
            .padding(28)
            .frame(maxWidth: 380)
        }
        .onAppear {
            runCeremony()
        }
    }

    private func runCeremony() {
        // Phase 1: 标题浮现
        withAnimation(.easeOut(duration: 0.6)) { phase = 1 }
        // Phase 2: 开篇文案
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            withAnimation(.easeOut(duration: 0.6)) { phase = 2 }
        }
        // Phase 2.5: 印玺落下
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            withAnimation(.spring(response: 0.34, dampingFraction: 0.5)) {
                stamped = true
            }
            UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
        }
        // Phase 3: 授品文字
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.1) {
            withAnimation(.easeOut(duration: 0.5)) { phase = 3 }
        }
        // Phase 4: 师爷登场 + 按钮
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.8) {
            withAnimation(.spring(response: 0.5, dampingFraction: 0.8)) { phase = 4 }
        }
    }
}
