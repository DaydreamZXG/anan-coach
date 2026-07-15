import AuthenticationServices
import SwiftUI

// MARK: - 欢迎页（登录门控：未登录时全屏呈现，iPhone/iPad 通用）
//
// 情绪定位：科技感 + 温柔陪伴。
// 视觉：大 D logo 居中，背景柔光渐变，品牌蓝紫渐变贯穿。
// 边界：玩笑止于称呼，不承诺上岸、不编造数据。

struct WelcomeView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var appeared = false
    @State private var blessingIndex = Int.random(in: 0..<WelcomeView.blessings.count)

    private static let blessings: [String] = [
        "距离为人民服务，又近了一步。",
        "今天的题，都是未来的底气。",
        "编制不会辜负每一个较真的人。",
        "别人在刷手机，您在刷题——这就是差距。",
        "上岸的路上，搭子陪您走全程。",
    ]

    var body: some View {
        ZStack {
            backgroundGlow

            VStack(spacing: 0) {
                Spacer()

                heroSection
                    .offset(y: appeared ? 0 : 16)
                    .opacity(appeared ? 1 : 0)

                Spacer()

                signInSection
                    .offset(y: appeared ? 0 : 20)
                    .opacity(appeared ? 1 : 0)
            }
            .padding(.horizontal, sizeClass == .regular ? 80 : 32)
            .padding(.bottom, sizeClass == .regular ? 72 : 48)
            .padding(.top, sizeClass == .regular ? 60 : 20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(DaziPalette.paper)
        .onAppear {
            withAnimation(.spring(response: 0.7, dampingFraction: 0.85).delay(0.15)) {
                appeared = true
            }
        }
    }

    // MARK: 背景柔光渐变
    private var backgroundGlow: some View {
        GeometryReader { proxy in
            ZStack {
                DaziPalette.paper

                // 左上蓝紫光晕
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                DaziPalette.accentBlue.opacity(0.18),
                                Color.clear,
                            ],
                            center: .center,
                            startRadius: 20,
                            endRadius: proxy.size.width * 0.45
                        )
                    )
                    .frame(width: proxy.size.width * 0.7, height: proxy.size.width * 0.7)
                    .position(x: proxy.size.width * 0.15, y: proxy.size.height * 0.20)
                    .blur(radius: 4)

                // 右下紫色光晕
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                DaziPalette.accentPurple.opacity(0.15),
                                Color.clear,
                            ],
                            center: .center,
                            startRadius: 20,
                            endRadius: proxy.size.width * 0.40
                        )
                    )
                    .frame(width: proxy.size.width * 0.6, height: proxy.size.width * 0.6)
                    .position(x: proxy.size.width * 0.85, y: proxy.size.height * 0.80)
                    .blur(radius: 4)
            }
            .ignoresSafeArea()
        }
    }

    // MARK: 英雄区：logo + 标题 + 标语
    private var heroSection: some View {
        VStack(spacing: sizeClass == .regular ? 32 : 24) {
            Image("Logo")
                .resizable()
                .scaledToFit()
                .frame(width: sizeClass == .regular ? 160 : 120, height: sizeClass == .regular ? 160 : 120)
                .scaleEffect(appeared ? 1 : 0.6)
                .opacity(appeared ? 1 : 0)
                .animation(
                    .spring(response: 0.8, dampingFraction: 0.75).delay(0.10),
                    value: appeared
                )

            VStack(spacing: sizeClass == .regular ? 14 : 10) {
                Text("公考搭子")
                    .font(sizeClass == .regular ? .system(size: 44, weight: .bold) : .largeTitle.bold())
                    .foregroundStyle(DaziPalette.ink)

                Text("一个懂你为什么错的备考教练")
                    .font(sizeClass == .regular ? .title3 : .headline)
                    .fontWeight(.regular)
                    .foregroundStyle(.secondary)
            }

            Text(Self.blessings[blessingIndex])
                .font(sizeClass == .regular ? .title3 : .subheadline)
                .foregroundStyle(DaziPalette.brand)
                .fontWeight(.medium)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                .background(
                    Capsule()
                        .fill(DaziPalette.brand.opacity(0.08))
                )
                .padding(.top, sizeClass == .regular ? 8 : 4)
        }
        .frame(maxWidth: 560)
    }

    // MARK: 登录区
    private var signInSection: some View {
        VStack(spacing: 14) {
            SignInWithAppleButton(.signIn) { request in
                appState.prepareAppleSignIn(request)
            } onCompletion: { result in
                Task { await appState.completeAppleSignIn(result) }
            }
            .signInWithAppleButtonStyle(.black)
            .frame(height: 54)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .shadow(color: DaziPalette.accentPurple.opacity(0.15), radius: 12, x: 0, y: 6)

            if let message = appState.errorMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(DaziPalette.alert)
                    .multilineTextAlignment(.center)
            }

            Text("登录即代表同意以 Apple 账号创建您的备考档案")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: 420)
    }
}

#Preview("iPhone · 日间") {
    WelcomeView()
        .environmentObject(AppState())
        .preferredColorScheme(.light)
}

#Preview("iPhone · 夜间") {
    WelcomeView()
        .environmentObject(AppState())
        .preferredColorScheme(.dark)
}
