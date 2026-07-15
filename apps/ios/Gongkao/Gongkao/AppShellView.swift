import SwiftUI
import UserNotifications

struct AppShellView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var appState = AppState()
    @StateObject private var commerceStore = CommerceStore()
    @State private var selection: AppSection = .today

    var body: some View {
        Group {
            if !appState.isAuthenticated {
                WelcomeView()
            } else if !appState.learnerMemoryLoaded {
                LoadingShell()
            } else if needsOnboarding {
                OnboardingFlowView(accessToken: appState.authSession?.accessToken ?? "")
            } else if horizontalSizeClass == .regular {
                iPadShell
            } else {
                iPhoneShell
            }
        }
        .tint(GuzhuPalette.cinnabar)
        .environmentObject(appState)
        .environmentObject(commerceStore)
        .onAppear { ReturnNotificationManager.requestAuthorization() }
        .onChange(of: scenePhase) {
            if scenePhase == .background {
                let daysLeft = appState.feed?.cards.compactMap { $0.payload?.daysLeft }.first
                ReturnNotificationManager.scheduleReturnReminder(
                    daysLeft: daysLeft,
                    scoreGap: appState.today?.target.scoreGap
                )
            }
        }
        .task {
            if appState.isAuthenticated {
                await appState.loadLearnerMemory()
                await commerceStore.load(
                    accessToken: appState.authSession?.accessToken,
                    userId: appState.authSession?.user.userId
                )
            } else {
                commerceStore.clear()
            }
            await appState.loadInitialData()
        }
        .onChange(of: appState.authSession?.accessToken) { _, token in
            Task {
                if token == nil {
                    commerceStore.clear()
                } else {
                    await commerceStore.load(
                        accessToken: token,
                        userId: appState.authSession?.user.userId
                    )
                }
            }
        }
    }

    private var needsOnboarding: Bool {
        appState.isAuthenticated
            && appState.learnerMemory != nil
            && !appState.hasCompleteProfile
    }

    private var iPhoneShell: some View {
        VStack(spacing: 0) {
            StatusBarView(
                rank: appState.rankProfile,
                scoreGap: appState.today?.target.scoreGap,
                todayTaskCount: appState.today?.tasks.count ?? 0,
                completedTaskCount: appState.completedTaskIDs.count,
                xpProgress: appState.rankXpProgress,
                nextRankHint: appState.rankNextHint,
                onRankTap: { appState.showRankDetail = true }
            )
            TabView(selection: $selection) {
                ForEach(AppSection.allCases) { section in
                    NavigationStack {
                        sectionView(section)
                    }
                    .tabItem {
                        Label(section.title, systemImage: section.symbol)
                    }
                    .tag(section)
                }
            }
            .tint(GuzhuPalette.cinnabar)
        }
        .background(GuzhuPalette.paper)
        .fullScreenCover(item: $appState.mockExamRoute) { mode in
            MockExamFlowView(
                mode: mode,
                accessToken: appState.authSession?.accessToken ?? ""
            )
            .environmentObject(appState)
        }
        .fullScreenCover(isPresented: $appState.showRankDetail) {
            if let rank = appState.rankProfile {
                RankDetailView(rank: rank, configs: appState.rankConfigs ?? [])
            }
        }
        .overlay {
            if let payload = appState.celebration {
                CelebrationOverlay(payload: payload) {
                    appState.celebration = nil
                }
            }
        }
        .overlay {
            if let payload = appState.rankUp {
                RankUpOverlay(payload: payload) {
                    appState.rankUp = nil
                }
            }
        }
    }

    private var iPadShell: some View {
        NavigationSplitView {
            iPadSidebar
        } detail: {
            VStack(spacing: 0) {
                StatusBarView(
                    rank: appState.rankProfile,
                    scoreGap: appState.today?.target.scoreGap,
                    todayTaskCount: appState.today?.tasks.count ?? 0,
                    completedTaskCount: appState.completedTaskIDs.count,
                    xpProgress: appState.rankXpProgress,
                    nextRankHint: appState.rankNextHint,
                    onRankTap: { appState.showRankDetail = true }
                )
                Group {
                    switch selection {
                    case .today:
                        StudyDeskView()
                    case .workbench:
                        NavigationStack { WorkbenchView() }
                    case .profile:
                        NavigationStack { ProfileView() }
                    }
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .tint(GuzhuPalette.cinnabar)
        .background(GuzhuPalette.paper)
        .fullScreenCover(item: $appState.activeTask) { task in
            NavigationStack {
                TaskExecutionView(task: task)
            }
        }
        .fullScreenCover(item: $appState.mockExamRoute) { mode in
            MockExamFlowView(
                mode: mode,
                accessToken: appState.authSession?.accessToken ?? ""
            )
            .environmentObject(appState)
        }
        .fullScreenCover(isPresented: $appState.showRankDetail) {
            if let rank = appState.rankProfile {
                RankDetailView(rank: rank, configs: appState.rankConfigs ?? [])
            }
        }
        .overlay {
            if let payload = appState.celebration {
                CelebrationOverlay(payload: payload) {
                    appState.celebration = nil
                }
            }
        }
        .overlay {
            if let payload = appState.rankUp {
                RankUpOverlay(payload: payload) {
                    appState.rankUp = nil
                }
            }
        }
    }

    // MARK: - iPad 侧边栏

    private var iPadSidebar: some View {
        List(selection: Binding<AppSection?>(
            get: { selection },
            set: { if let value = $0 { selection = value } }
        )) {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        Image("Logo")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 32, height: 32)
                        Text("公考搭子")
                            .font(.title2.weight(.bold))
                            .foregroundStyle(GuzhuPalette.ink)
                    }
                    Text(sidebarGreeting)
                        .font(.headline)
                        .foregroundStyle(GuzhuPalette.ink)
                    if let today = appState.today {
                        Text("今日路线 \(today.tasks.count) 项 · 已完成 \(appState.completedTaskIDs.count) 项")
                            .font(.caption)
                            .foregroundStyle(GuzhuPalette.faintInk)
                    } else {
                        Text("鹿鸣正在为你排今日路线")
                            .font(.caption)
                            .foregroundStyle(GuzhuPalette.faintInk)
                    }
                }
                .padding(.vertical, 6)
                .listRowBackground(Color.clear)
            }

            Section("上岸") {
                ForEach(AppSection.allCases) { section in
                    Label(guzhuTitle(section), systemImage: section.symbol)
                        .tag(section)
                        .foregroundStyle(GuzhuPalette.ink)
                }
            }

            Section("快捷动作") {
                Button {
                    appState.mockExamRoute = .takeWeekly
                } label: {
                    Label("整卷模考", systemImage: "timer")
                        .foregroundStyle(GuzhuPalette.ink)
                }
                Button {
                    selection = .workbench
                } label: {
                    Label("错因复盘", systemImage: "tray.full")
                        .foregroundStyle(GuzhuPalette.ink)
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("公考搭子")
        .scrollContentBackground(.hidden)
        .background(GuzhuPalette.paper)
    }

    private func guzhuTitle(_ section: AppSection) -> String {
        switch section {
        case .today: return "今日路线"
        case .workbench: return "工作台"
        case .profile: return "我的"
        }
    }

    private var sidebarGreeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<12: return "早，鹿鸣已排好路线"
        case 12..<18: return "午后好，继续推进"
        default: return "晚上好，稳稳收尾"
        }
    }

    @ViewBuilder
    private func sectionView(_ section: AppSection) -> some View {
        switch section {
        case .today:
            TodayView()
                .background(GuzhuPalette.paper)
        case .workbench:
            WorkbenchView()
                .background(GuzhuPalette.paper)
        case .profile:
            ProfileView()
                .background(GuzhuPalette.paper)
        }
    }
}

#Preview {
    AppShellView()
}

private struct LoadingShell: View {
    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("正在准备…")
                .font(.headline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(GuzhuPalette.paper)
    }
}

// MARK: - 归来提醒（用户离开 App 约 24 小时后唤起「回来理政」）
//
// 设计纪律（学习优先）：文案带考试倒计时 + 分差，唤起该有的紧迫感，
// 而非「封地想你」这类情感绑架；不发任何 cosmetic 奖励。

enum ReturnNotificationManager {
    static let reminderID = "guzhi-return-reminder"

    static func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    static func scheduleReturnReminder(
        daysLeft: Int?,
        scoreGap: Double?,
        fireInHours: Int = 24
    ) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [reminderID])

        let fire = Calendar.current.date(
            byAdding: .hour,
            value: fireInHours,
            to: Date()
        ) ?? Date().addingTimeInterval(TimeInterval(fireInHours * 3600))

        let title = "鹿鸣把今日路线排好了"
        var body = "回来推进一段，今天的训练会继续围绕目标分差调整。"
        if let days = daysLeft, days > 0 {
            body = "距国考约 \(days) 天"
            if let gap = scoreGap, gap > 0 {
                body += "，当前距上岸还差 \(String(format: "%.0f", gap)) 分"
            }
            body += "。今日路线已备好，回来再进一步。"
        }

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default

        let comps = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute],
            from: fire
        )
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
        let request = UNNotificationRequest(
            identifier: reminderID,
            content: content,
            trigger: trigger
        )
        center.add(request)
    }
}
