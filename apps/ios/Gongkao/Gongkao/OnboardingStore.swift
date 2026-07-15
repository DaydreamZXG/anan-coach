import Foundation

enum OnboardingStep: Int, CaseIterable {
    case conditions, timeBudget, position, baseline, targetScore

    var title: String {
        switch self {
        case .conditions: return "报考条件"
        case .timeBudget: return "学习时间"
        case .position: return "目标岗位"
        case .baseline: return "能力基线"
        case .targetScore: return "目标确认"
        }
    }
}

@MainActor
final class OnboardingStore: ObservableObject {
    @Published var step: OnboardingStep = .conditions
    // 步骤 1：硬条件
    @Published var education = "本科"
    @Published var selectedMajor: MajorItem?
    @Published var freshGraduate = false
    @Published var politicalStatus = "群众"
    @Published var grassrootsYears = 0
    // 步骤 2：时间预算
    @Published var studyMode = "parttime"
    @Published var dailyMinutes = 120
    // 步骤 3：目标岗位
    @Published var positions: [PositionSummary] = []
    @Published var selectedPosition: PositionSummary?
    @Published var selectedDecisionItem: PositionDecisionItem?
    @Published var searching = false
    @Published var positionDecision: PositionDecisionResponse?
    @Published var decidingPositions = false
    @Published var provinces: [ProvinceInfo] = []
    @Published var majorTree: [MajorCategory] = []
    @Published var selectedProvince: ProvinceInfo?
    @Published var selectedCity: CityInfo?
    @Published var selectedDistrict: DistrictInfo?
    @Published var selectedPositionDetail: PositionDetail?
    // 步骤 4：能力基线
    @Published var baselineChoice = "self_report"
    @Published var xingceText = ""
    @Published var shenlunText = ""
    @Published var weakModules: Set<String> = []
    @Published var placement: PlacementTestTask?
    @Published var placementIndex = 0
    @Published var placementPicks: [String: String] = [:]
    @Published var placementResult: PlacementSubmitResponse?
    // 步骤 5：目标分
    @Published var targetScoreText = ""
    // 通用
    @Published var busy = false
    @Published var errorMessage: String?

    let client = APIClient()
    let accessToken: String

    init(accessToken: String) {
        self.accessToken = accessToken
    }

    var canLeaveConditions: Bool {
        selectedMajor != nil
    }

    var positionDecisionScore: Double? {
        let shenlun = Double(shenlunText)
        if let placement = placementResult, let shenlun {
            return placement.estimatedXingceScore + shenlun
        }
        if let xingce = Double(xingceText), let shenlun {
            return xingce + shenlun
        }
        return nil
    }

    func saveProfile() async -> Bool {
        var update = OnboardingProfileUpdate(
            education: education,
            major: selectedMajor?.name ?? "",
            freshGraduate: freshGraduate,
            politicalStatus: politicalStatus,
            grassrootsYears: grassrootsYears
        )
        update.studyMode = studyMode
        update.dailyMinutes = dailyMinutes
        update.targetPositionId = selectedPosition?.positionId
        if let score = Double(targetScoreText), score > 0 {
            update.targetScore = score
        } else if let decisionTarget = selectedDecisionItem?.decisionTargetScore {
            update.targetScore = decisionTarget
        } else if let auto = selectedPosition?.entryScore.targetScore {
            update.targetScore = auto
        }
        if let result = placementResult {
            update.baselineSource = "placement_test"
            update.baselineXingceScore = result.estimatedXingceScore
            update.baselineShenlunScore = Double(shenlunText)
        } else if Double(xingceText) != nil || Double(shenlunText) != nil {
            update.baselineSource = "self_report"
            update.baselineXingceScore = Double(xingceText)
            update.baselineShenlunScore = Double(shenlunText)
            update.baselineWeakModules = Array(weakModules)
        } else {
            update.baselineSource = "none"
        }
        do {
            busy = true
            defer { busy = false }
            let _: LearnerProfile = try await client.put(
                "/api/v1/onboarding/profile", body: update, accessToken: accessToken
            )
            return true
        } catch {
            errorMessage = "保存失败：\(error.localizedDescription)"
            return false
        }
    }

    func complete() async -> Bool {
        guard await saveProfile() else { return false }
        do {
            busy = true
            defer { busy = false }
            let _: OnboardingCompleteResponse = try await client.post(
                "/api/v1/onboarding/complete", body: EmptyBody(), accessToken: accessToken
            )
            return true
        } catch {
            errorMessage = "完成失败：\(error.localizedDescription)"
            return false
        }
    }
    func searchPositions() async {
        guard canLeaveConditions else { return }
        searching = true
        positionDecision = nil
        selectedDecisionItem = nil
        defer { searching = false }
        let query = queryString(positionQueryItems(limit: 30))
        do {
            let response: PositionSearchResponse = try await client.get(
                "/api/v1/positions/search?\(query)", accessToken: accessToken
            )
            positions = response.items
            errorMessage = nil
        } catch {
            errorMessage = "岗位查询失败：\(error.localizedDescription)"
        }
    }

    func loadPositionDecision() async {
        guard canLeaveConditions else { return }
        guard let score = positionDecisionScore else {
            positionDecision = nil
            return
        }
        decidingPositions = true
        defer { decidingPositions = false }
        var items = positionQueryItems(limit: 30)
        items.append(URLQueryItem(name: "currentScore", value: score.queryValue))
        items.append(URLQueryItem(name: "riskPreference", value: "balanced"))
        do {
            let response: PositionDecisionResponse = try await client.get(
                "/api/v1/position-decisions/recommendations?\(queryString(items))",
                accessToken: accessToken
            )
            positionDecision = response
            let responseItems = response.recommendationBuckets.flatMap(\.items)
            if let selectedDecisionItem,
               !responseItems.contains(where: { $0.position.positionId == selectedDecisionItem.position.positionId })
            {
                self.selectedDecisionItem = nil
            }
            if positions.isEmpty {
                positions = responseItems.map(\.position)
            }
            errorMessage = nil
        } catch {
            errorMessage = "岗位决策失败：\(error.localizedDescription)"
        }
    }

    private func positionQueryItems(limit: Int) -> [URLQueryItem] {
        var items = [
            URLQueryItem(name: "education", value: education),
            URLQueryItem(name: "major", value: selectedMajor?.name ?? ""),
            URLQueryItem(name: "politicalStatus", value: politicalStatus),
            URLQueryItem(name: "grassrootsYears", value: "\(grassrootsYears)"),
            URLQueryItem(name: "freshGraduate", value: freshGraduate ? "true" : "false"),
            URLQueryItem(name: "limit", value: "\(limit)"),
        ]
        if let province = selectedProvince?.name.trimmingCharacters(in: .whitespaces), !province.isEmpty {
            items.append(URLQueryItem(name: "region", value: province))
        }
        let city = selectedDistrict?.name ?? selectedCity?.name
        if let city = city?.trimmingCharacters(in: .whitespaces), !city.isEmpty {
            items.append(URLQueryItem(name: "city", value: city))
        }
        return items
    }

    private func queryString(_ items: [URLQueryItem]) -> String {
        var components = URLComponents()
        components.queryItems = items
        return components.percentEncodedQuery ?? ""
    }

    func loadRegions() async {
        do {
            let response: ProvinceListResponse = try await client.get(
                "/api/v1/positions/regions", accessToken: accessToken
            )
            provinces = response.provinces
        } catch {
            errorMessage = "地区列表加载失败：\(error.localizedDescription)"
        }
    }

    func loadMajors() async {
        majorTree = MajorCatalog.categories
    }

    func loadPositionDetail(positionId: String) async {
        do {
            let detail: PositionDetail = try await client.get(
                "/api/v1/positions/\(positionId)", accessToken: accessToken
            )
            selectedPositionDetail = detail
        } catch {
            errorMessage = "岗位详情加载失败：\(error.localizedDescription)"
        }
    }

    func loadPlacementTest() async {
        busy = true
        defer { busy = false }
        do {
            placement = try await client.get(
                "/api/v1/onboarding/placement-test", accessToken: accessToken
            )
            placementIndex = 0
            placementPicks = [:]
            placementResult = nil
        } catch {
            errorMessage = "摸底卷加载失败：\(error.localizedDescription)"
        }
    }

    func submitPlacement() async {
        guard placement != nil, !placementPicks.isEmpty else { return }
        busy = true
        defer { busy = false }
        let answers = placementPicks.map { PlacementAnswer(questionId: $0.key, selectedAnswer: $0.value) }
        do {
            placementResult = try await client.post(
                "/api/v1/onboarding/placement-test/answers",
                body: PlacementSubmitRequest(answers: answers),
                accessToken: accessToken
            )
        } catch {
            errorMessage = "摸底判分失败：\(error.localizedDescription)"
        }
    }
}

struct EmptyBody: Encodable {}

private extension Double {
    var queryValue: String {
        String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), self)
    }
}
