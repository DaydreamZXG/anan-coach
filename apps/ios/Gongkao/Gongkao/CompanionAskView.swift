import SwiftUI

// MARK: - 契约模型（对齐 backend/app/domains/companion/schemas.py）

struct CompanionAskResponse: Decodable {
    let scenario: String
    let answer: String
    let citations: [CompanionCitation]
    let nextActions: [String]
    let quota: AIQuotaState
}

struct CompanionCitation: Decodable, Identifiable {
    let kind: String
    let title: String
    let sourceId: String?
    let summary: String

    var id: String { "\(kind)-\(sourceId ?? title)" }

    var kindLabel: String {
        switch kind {
        case "learner_memory": "你的数据"
        case "method_card": "方法卡"
        case "target_position": "目标岗位"
        case "knowledge_map": "知识点"
        default: "依据"
        }
    }
}

private struct CompanionAskRequest: Encodable {
    let scenario: String
    let questionText: String
    let nodeId: String?
}

// MARK: - 状态

@MainActor
final class CompanionAskStore: ObservableObject {
    @Published var scenario = "why_wrong"
    @Published var questionText = ""
    @Published var response: CompanionAskResponse?
    @Published var asking = false
    @Published var errorMessage: String?

    private let client = APIClient()
    private let accessToken: String
    private let commandKey: DurableAPICommandKey
    let nodeId: String?

    init(accessToken: String, nodeId: String? = nil, prefill: String? = nil) {
        self.accessToken = accessToken
        commandKey = DurableAPICommandKey(
            namespace: "companion-ask",
            subject: accessToken
        )
        self.nodeId = nodeId
        if let prefill { questionText = prefill }
    }

    func ask() async {
        let text = questionText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 4, !asking else { return }
        asking = true
        defer { asking = false }
        do {
            let payload = CompanionAskRequest(
                scenario: scenario,
                questionText: text,
                nodeId: nodeId
            )
            response = try await client.post(
                "/api/v1/companion/ask",
                body: payload,
                accessToken: accessToken,
                idempotencyKey: try commandKey.key(for: payload)
            )
            commandKey.complete()
            errorMessage = nil
        } catch {
            if case APIClientError.httpStatus(let status, _) = error,
               (400..<500).contains(status) {
                commandKey.complete()
            }
            if case APIClientError.httpStatus(let status, _) = error, status == 429 {
                errorMessage = "今日免费 AI 问答额度已用完，可在「我的」查看 AI Pro 权益。"
            } else {
                errorMessage = "搭子暂时没回上来：\(error.localizedDescription)"
            }
        }
    }
}

// MARK: - 问搭子页

struct CompanionAskView: View {
    @StateObject private var store: CompanionAskStore
    @FocusState private var inputFocused: Bool
    let rankName: String?

    private static let scenarios: [(id: String, label: String)] = [
        ("why_wrong", "为什么错"),
        ("how_to_learn", "怎么学"),
        ("should_skip_module", "要不要放弃"),
    ]

    init(accessToken: String, nodeId: String? = nil, prefill: String? = nil, rankName: String? = nil) {
        _store = StateObject(
            wrappedValue: CompanionAskStore(accessToken: accessToken, nodeId: nodeId, prefill: prefill)
        )
        self.rankName = rankName
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                CompanionAvatarBanner(rankName: rankName)

                Picker("场景", selection: $store.scenario) {
                    ForEach(Self.scenarios, id: \.id) { item in
                        Text(item.label).tag(item.id)
                    }
                }
                .pickerStyle(.segmented)

                VStack(alignment: .leading, spacing: 8) {
                    TextField("比如：多定义判断总选错，怎么读题干？", text: $store.questionText, axis: .vertical)
                        .lineLimit(3...6)
                        .focused($inputFocused)
                        .padding(12)
                        .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 12))

                    Button {
                        inputFocused = false
                        Task { await store.ask() }
                    } label: {
                        if store.asking {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Label("问搭子", systemImage: "paperplane.fill")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(DaziPalette.brand)
                    .disabled(store.questionText.trimmingCharacters(in: .whitespacesAndNewlines).count < 4 || store.asking)
                }

                if store.response == nil && store.errorMessage == nil {
                    Text("主公今日想从何处入手？沈砚在此候着，问便是。")
                        .font(.callout.italic())
                        .foregroundStyle(.secondary)
                        .padding(.top, 4)
                }

                if let message = store.errorMessage {
                    ErrorBanner(message: message) {
                        Task { await store.ask() }
                    }
                }

                if let response = store.response {
                    CompanionAnswerPanel(response: response)
                }
            }
            .padding(20)
            .contentMargins(maxWidth: 760)
        }
        .background(DaziPalette.paper)
        .navigationTitle("问搭子")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 搭子（师爷）形象横幅

private struct CompanionAvatarBanner: View {
    let rankName: String?

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(DaziGradient.actionLinear)
                    .frame(width: 46, height: 46)
                Image(systemName: "person.fill")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text("沈师爷")
                        .font(.headline)
                        .foregroundStyle(DaziPalette.ink)
                    Text("· 你的备考幕僚")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let rankName {
                    Text("主公现居 · \(rankName)")
                        .font(.caption2)
                        .foregroundStyle(DaziPalette.brand)
                } else {
                    Text("与主公同赴功名")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(DaziPalette.brand.opacity(0.18), lineWidth: 1)
        )
    }
}

private struct CompanionAnswerPanel: View {
    let response: CompanionAskResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "person.fill")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(5)
                        .background(DaziPalette.brand, in: Circle())
                    Text("沈师爷说")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DaziPalette.brand)
                }
                Text(response.answer)
                    .font(.subheadline)
                    .lineSpacing(4)
                    .textSelection(.enabled)
            }
            .panelStyle()

            if !response.citations.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("回答依据")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(response.citations) { citation in
                        HStack(alignment: .top, spacing: 8) {
                            Text(citation.kindLabel)
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(DaziPalette.brand.opacity(0.1), in: Capsule())
                                .foregroundStyle(DaziPalette.brand)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(citation.title)
                                    .font(.caption.weight(.medium))
                                Text(citation.summary)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .panelStyle()
            }

            if !response.nextActions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("接下来可以做")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(response.nextActions, id: \.self) { action in
                        Label(action, systemImage: "arrow.right.circle")
                            .font(.subheadline)
                    }
                }
                .panelStyle()
            }

            Text("AI 配额已用 \(response.quota.used)/\(response.quota.limit) 次")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }
}
