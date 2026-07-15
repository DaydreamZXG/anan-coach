import PhotosUI
import SwiftUI
import Vision

// MARK: - 契约模型（对齐 companion 域 external-questions 两步录入）

private struct ExtractedTag: Encodable {
    let moduleName: String
    let tagType: String
    let tagValue: String
    let confidence: Double
}

private struct ResolveRequest: Encodable {
    let stem: String
    let options: [String]
    let selectedAnswer: String?
    let correctAnswer: String?
    let extractedTags: [ExtractedTag]
    let containsImage: Bool
    let extractionConfidence: Double
}

struct ResolveResponse: Decodable {
    let intakeId: String
    let status: String
    let reason: String?
    let candidates: [KnowledgeCandidate]
    let requiresUserConfirmation: Bool
    let quota: AIQuotaState
}

struct KnowledgeCandidate: Decodable, Identifiable {
    let nodeId: String
    let displayPath: String
    let moduleName: String
    let supportStatus: String
    let confidence: Double

    var id: String { nodeId }
}

private struct ConfirmRequest: Encodable {
    let userConfirmed: Bool
    let errorSummary: String
}

struct ConfirmResponse: Decodable {
    let externalQuestionId: String
    let nodeId: String
    let status: String
}

// MARK: - 状态机

@MainActor
final class CaptureQuestionStore: ObservableObject {
    enum Phase {
        case pick
        case recognizing
        case proofread
        case candidates(ResolveResponse)
        case rejected(String)
        case done(ConfirmResponse)
        case failed(String)
    }

    @Published var phase: Phase = .pick
    @Published var ocrText = ""
    @Published var stem = ""
    @Published var optionsText = ""
    @Published var correctAnswer = ""
    @Published var selectedAnswer = ""
    @Published var moduleName = "言语理解与表达"
    @Published var containsImage = false
    @Published var errorSummary = ""
    @Published var submitting = false

    static let modules = ["言语理解与表达", "数量关系", "判断推理", "资料分析", "常识判断"]

    private var ocrConfidence: Double = 0
    private let client = APIClient()
    private let accessToken: String
    private let resolveCommandKey: DurableAPICommandKey

    init(accessToken: String) {
        self.accessToken = accessToken
        resolveCommandKey = DurableAPICommandKey(
            namespace: "external-question-resolve",
            subject: accessToken
        )
    }

    func recognize(imageData: Data) async {
        phase = .recognizing
        guard let uiImage = UIImage(data: imageData), let cgImage = uiImage.cgImage else {
            phase = .failed("照片读取失败，请换一张试试。")
            return
        }
        // OCR 是 CPU 密集型同步操作，放到后台线程避免阻塞主线程
        let result: Result<[OCRLine], Error> = await Task.detached(priority: .userInitiated) {
            let request = VNRecognizeTextRequest()
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.recognitionLevel = .accurate
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
                let observations = request.results ?? []
                let lines = observations.compactMap { $0.topCandidates(1).first }
                return .success(lines.map { OCRLine(string: $0.string, confidence: Double($0.confidence)) })
            } catch {
                return .failure(error)
            }
        }.value

        switch result {
        case .success(let lines):
            guard !lines.isEmpty else {
                phase = .failed("没有识别到文字。如果是图形推理题，App 暂不支持录入；如果是文字题，请拍清楚一点。")
                return
            }
            ocrConfidence = lines.map { $0.confidence }.reduce(0, +) / Double(lines.count)
            ocrText = lines.map(\.string).joined(separator: "\n")
            splitStemAndOptions()
            phase = .proofread
        case .failure(let error):
            phase = .failed("识别失败：\(error.localizedDescription)")
        }
    }

    /// OCR 识别行（后台线程与主线程之间传递的不可变载体）
    private struct OCRLine: Sendable {
        let string: String
        let confidence: Double
    }

    /// 粗切题干与选项（A./B./C./D./E. 开头行归入选项），用户在校对表单里修正
    private func splitStemAndOptions() {
        var stemLines: [String] = []
        var optionLines: [String] = []
        for line in ocrText.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.range(of: "^[A-E][.、．。]", options: .regularExpression) != nil {
                optionLines.append(trimmed)
            } else if optionLines.isEmpty {
                stemLines.append(trimmed)
            } else {
                optionLines[optionLines.count - 1] += trimmed
            }
        }
        stem = stemLines.joined(separator: "\n")
        optionsText = optionLines.joined(separator: "\n")
    }

    func resolve() async {
        guard !submitting else { return }
        submitting = true
        defer { submitting = false }
        let options = optionsText
            .split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let payload = ResolveRequest(
            stem: stem.trimmingCharacters(in: .whitespacesAndNewlines),
            options: options,
            selectedAnswer: selectedAnswer.isEmpty ? nil : selectedAnswer,
            correctAnswer: correctAnswer.isEmpty ? nil : correctAnswer,
            extractedTags: [
                // 模块由用户手选，confidence=1.0 如实标注为用户确认，不是模型硬编
                ExtractedTag(moduleName: moduleName, tagType: "module", tagValue: moduleName, confidence: 1.0)
            ],
            containsImage: containsImage,
            extractionConfidence: ocrConfidence
        )
        do {
            let response: ResolveResponse = try await client.post(
                "/api/v1/companion/external-questions/resolve",
                body: payload,
                accessToken: accessToken,
                idempotencyKey: try resolveCommandKey.key(for: payload)
            )
            resolveCommandKey.complete()
            if response.status == "rejected" {
                phase = .rejected(response.reason ?? "这道题暂时无法录入。")
            } else {
                phase = .candidates(response)
            }
        } catch {
            if case APIClientError.httpStatus(let status, _) = error,
               (400..<500).contains(status) {
                resolveCommandKey.complete()
            }
            if case APIClientError.httpStatus(let status, _) = error, status == 429 {
                phase = .failed("今日免费外部错题解析额度已用完，可在「我的」查看 AI Pro 权益。")
            } else {
                phase = .failed("提交失败：\(error.localizedDescription)")
            }
        }
    }

    func confirm(intakeId: String) async {
        guard errorSummary.trimmingCharacters(in: .whitespaces).count >= 2, !submitting else { return }
        submitting = true
        defer { submitting = false }
        do {
            let response: ConfirmResponse = try await client.post(
                "/api/v1/companion/external-questions/\(intakeId)/confirm",
                body: ConfirmRequest(userConfirmed: true, errorSummary: errorSummary),
                accessToken: accessToken
            )
            phase = .done(response)
        } catch {
            phase = .failed("确认失败：\(error.localizedDescription)")
        }
    }
}

// MARK: - 拍照录题页

struct CaptureQuestionView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var store: CaptureQuestionStore
    @State private var pickedItem: PhotosPickerItem?

    init(accessToken: String) {
        _store = StateObject(wrappedValue: CaptureQuestionStore(accessToken: accessToken))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    switch store.phase {
                    case .pick:
                        pickPanel
                    case .recognizing:
                        LoadingPanel(title: "正在识别文字")
                    case .proofread:
                        ProofreadForm(store: store)
                    case .candidates(let response):
                        CandidatesPanel(store: store, response: response)
                    case .rejected(let reason):
                        rejectedPanel(reason)
                    case .done(let response):
                        donePanel(response)
                    case .failed(let message):
                        ErrorBanner(message: message) {
                            store.phase = .pick
                        }
                    }
                }
                .padding(20)
                .contentMargins(maxWidth: 760)
            }
            .background(DaziPalette.paper)
            .navigationTitle("拍照录题")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
            }
        }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self) {
                    await store.recognize(imageData: data)
                }
            }
        }
    }

    private var pickPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("把你在纸质题册或其他 App 里做错的题拍下来，搭子帮你归档到知识点，一起分析。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineSpacing(3)

            PhotosPicker(selection: $pickedItem, matching: .images) {
                Label("选择照片或截图", systemImage: "camera.viewfinder")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)

            Label("图形推理题暂不支持录入", systemImage: "info.circle")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .panelStyle()
    }

    private func rejectedPanel(_ reason: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("这道题没能收进来", systemImage: "hand.raised")
                .font(.headline)
                .foregroundStyle(DaziPalette.alert)
            Text(reason)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineSpacing(3)
            Button("换一张照片") { store.phase = .pick }
                .buttonStyle(.bordered)
        }
        .panelStyle()
    }

    private func donePanel(_ response: ConfirmResponse) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("已收进错题本", systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundStyle(DaziPalette.positive)
            Text("这道题已归档到知识点，会参与你每日计划的聚合分析，也能在工作台里找到它。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineSpacing(3)
            Button {
                Task { await appState.loadInitialData() }
                dismiss()
            } label: {
                Text("完成")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)
        }
        .panelStyle()
    }
}

// MARK: - 校对表单

private struct ProofreadForm: View {
    @ObservedObject var store: CaptureQuestionStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("识别结果需要你核对——OCR 会有错字，改对了再提交。")
                .font(.caption)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 6) {
                Text("题干")
                    .font(.caption.weight(.semibold))
                TextField("题干", text: $store.stem, axis: .vertical)
                    .lineLimit(4...10)
                    .padding(10)
                    .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 10))
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("选项（每行一个，A./B. 开头）")
                    .font(.caption.weight(.semibold))
                TextField("选项", text: $store.optionsText, axis: .vertical)
                    .lineLimit(4...8)
                    .padding(10)
                    .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 10))
            }

            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("我选的")
                        .font(.caption.weight(.semibold))
                    TextField("如 B", text: $store.selectedAnswer)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("正确答案")
                        .font(.caption.weight(.semibold))
                    TextField("如 C", text: $store.correctAnswer)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                }
                Spacer()
            }

            Picker("模块", selection: $store.moduleName) {
                ForEach(CaptureQuestionStore.modules, id: \.self) { name in
                    Text(name).tag(name)
                }
            }
            .pickerStyle(.menu)

            Toggle("题目含图形/图表", isOn: $store.containsImage)
                .font(.subheadline)

            Button {
                Task { await store.resolve() }
            } label: {
                if store.submitting {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("提交定位知识点")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)
            .disabled(store.stem.trimmingCharacters(in: .whitespaces).count < 8 || store.submitting)
        }
        .panelStyle()
    }
}

// MARK: - 归档确认（服务端已锁定定位，客户端如实展示，不做虚假选择）

private struct CandidatesPanel: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: CaptureQuestionStore
    let response: ResolveResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let top = response.candidates.first {
                VStack(alignment: .leading, spacing: 6) {
                    Label("将归档到", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(top.displayPath)
                        .font(.subheadline.weight(.semibold))
                    Text(String(format: "匹配度 %.0f%%", top.confidence * 100))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(DaziPalette.brand.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            }

            if response.candidates.count > 1 {
                DisclosureGroup("其他相近知识点（仅参考）") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(response.candidates.dropFirst()) { candidate in
                            Text(candidate.displayPath)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 6)
                }
                .font(.caption.weight(.semibold))
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("你觉得自己错在哪？（必填）")
                    .font(.caption.weight(.semibold))
                TextField("比如：把「基期」看成「现期」了", text: $store.errorSummary, axis: .vertical)
                    .lineLimit(2...4)
                    .padding(10)
                    .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 10))
            }

            Button {
                Task { await store.confirm(intakeId: response.intakeId) }
            } label: {
                if store.submitting {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("确认收进错题本")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)
            .disabled(store.errorSummary.trimmingCharacters(in: .whitespaces).count < 2 || store.submitting)

            Button("定位不对，先不收录") { dismiss() }
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)

            Text("AI 配额已用 \(response.quota.used)/\(response.quota.limit) 次")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .panelStyle()
    }
}
