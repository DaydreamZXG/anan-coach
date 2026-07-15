import SwiftUI

struct OnboardingFlowView: View {
    @EnvironmentObject private var appState: AppState
    @StateObject private var store: OnboardingStore
    @State private var showInvestiture = false
    var onDone: (() -> Void)?

    init(accessToken: String, onDone: (() -> Void)? = nil) {
        self.onDone = onDone
        _store = StateObject(wrappedValue: OnboardingStore(accessToken: accessToken))
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                stepIndicator
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        stepBody
                    }
                    .padding(20)
                }
                footer
            }
            .background(DaziPalette.paper)
            .navigationTitle(store.step.title)
            .navigationBarTitleDisplayMode(.inline)
        }
        .environmentObject(store)
        .fullScreenCover(isPresented: $showInvestiture) {
            InvestitureView {
                showInvestiture = false
                onDone?()
            }
        }
    }

    private var stepIndicator: some View {
        HStack(spacing: 6) {
            ForEach(OnboardingStep.allCases, id: \.rawValue) { item in
                Capsule()
                    .fill(item.rawValue <= store.step.rawValue ? DaziPalette.brand : Color(.systemFill))
                    .frame(height: 4)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .accessibilityLabel("第 \(store.step.rawValue + 1) 步，共 \(OnboardingStep.allCases.count) 步")
    }

    @ViewBuilder
    private var stepBody: some View {
        switch store.step {
        case .conditions: OnboardingConditionsStep()
        case .timeBudget: OnboardingTimeBudgetStep()
        case .position: OnboardingPositionStep()
        case .baseline: OnboardingBaselineStep()
        case .targetScore: OnboardingTargetStep()
        }
    }

    private var footer: some View {
        VStack(spacing: 8) {
            if let message = store.errorMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(DaziPalette.alert)
            }
            HStack(spacing: 12) {
                if store.step != .conditions {
                    Button("上一步") {
                        withAnimation(.snappy) {
                            store.step = OnboardingStep(rawValue: store.step.rawValue - 1) ?? .conditions
                        }
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                Button(action: advance) {
                    if store.busy {
                        ProgressView().tint(.white)
                    } else {
                        Text(store.step == .targetScore ? "完成，生成首日计划" : "下一步")
                            .fontWeight(.semibold)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(DaziPalette.brand)
                .disabled(store.busy || (store.step == .conditions && !store.canLeaveConditions))
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
        .background(.bar)
    }

    private func advance() {
        if store.step == .targetScore {
            Task {
                if await store.complete() {
                    await appState.loadLearnerMemory()
                    await appState.loadInitialData()
                    showInvestiture = true
                }
            }
            return
        }
        if store.step == .conditions {
            Task { await store.searchPositions() }
        }
        if store.step == .baseline, store.positionDecisionScore != nil {
            Task { await store.loadPositionDecision() }
        }
        withAnimation(.snappy) {
            store.step = OnboardingStep(rawValue: store.step.rawValue + 1) ?? .targetScore
        }
    }
}
