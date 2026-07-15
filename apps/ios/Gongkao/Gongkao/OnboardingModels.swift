import Foundation

struct OnboardingProfileUpdate: Encodable {
    let education: String
    let major: String
    let freshGraduate: Bool
    let politicalStatus: String
    let grassrootsYears: Int
    var studyMode: String?
    var dailyMinutes: Int?
    var targetPositionId: String?
    var targetScore: Double?
    var baselineSource: String?
    var baselineXingceScore: Double?
    var baselineShenlunScore: Double?
    var baselineWeakModules: [String] = []
}

struct OnboardingCompleteResponse: Decodable {
    let profile: LearnerProfile
    let firstDayPlanUrl: String
}

struct CohortAnnualScore: Decodable {
    let year: Int
    let averageEntryScore: Double
    let sampleCount: Int
}

struct TargetScoreBasis: Decodable {
    let targetScore: Double?
    let basisLevel: String?
    let samplePositionCount: Int
    let scoreSampleCount: Int
    let annualScores: [CohortAnnualScore]
    let driftPerYear: Double?
    let latestAverageScore: Double?
    let safetyMargin: Double
}

struct EntryScoreStats: Decodable {
    let years: [Int]
    let sourceCount: Int
    let averageEntryScore: Double?
    let latestEntryScore: Double?
    let targetScore: Double?
    let targetScoreBasis: TargetScoreBasis?
}

struct PositionSummary: Decodable, Identifiable {
    var id: String { positionId }
    let positionId: String
    let year: Int
    let positionCode: String
    let name: String
    let departmentFull: String
    let systemCode: String
    let region: String
    let city: String?
    let headcount: Int
    let educationMin: String?
    let majorRequired: String?
    let politicalReq: String?
    let serviceMinYrs: Int?
    let remark: String?
    let entryScore: EntryScoreStats
    let eligibilityBasis: [String]
    let hasExtraExam: Bool?
}

struct PositionSearchResponse: Decodable {
    let items: [PositionSummary]
    let limit: Int
    let unverifiedConditions: [String]
}

struct PositionDecisionResponse: Decodable {
    let generatedAt: String
    let currentScore: Double
    let riskPreference: String
    let summary: String
    let recommendationBuckets: [PositionDecisionBucket]
    let unverifiedConditions: [String]
    let nextActions: [String]
}

struct PositionDecisionBucket: Decodable, Identifiable {
    var id: String { bucket }
    let bucket: String
    let title: String
    let subtitle: String
    let items: [PositionDecisionItem]
}

struct PositionDecisionItem: Decodable, Identifiable {
    var id: String { position.positionId }
    let position: PositionSummary
    let bucket: String
    let decisionLabel: String
    let decisionTargetScore: Double?
    let decisionScoreBasis: PositionDecisionScoreBasis?
    let scoreGap: Double?
    let admissionChanceScore: Int
    let chanceLabel: String
    let riskLevel: String
    let riskTags: [String]
    let reasons: [String]
    let nextAction: String
}

struct PositionDecisionScoreBasis: Decodable {
    let basisType: String
    let targetScore: Double
    let sampleCount: Int
    let samplePositionCount: Int
    let latestScoreYear: Int?
    let averageEntryScore: Double?
    let safetyMargin: Double
    let confidence: String
    let matchedDimensions: [String]
    let evidenceSummary: String
}

struct MajorTreeResponse: Decodable {
    let categories: [MajorCategory]
}

struct MajorCategory: Decodable, Identifiable, Hashable {
    var id: String { code }
    let code: String
    let name: String
    let subCategories: [MajorSubCategory]
}

struct MajorSubCategory: Decodable, Identifiable, Hashable {
    var id: String { code }
    let code: String
    let name: String
    let majors: [MajorItem]
}

struct MajorItem: Decodable, Identifiable, Hashable {
    var id: String { code }
    let code: String
    let name: String
}

struct ProvinceListResponse: Decodable {
    let provinces: [ProvinceInfo]
}

struct ProvinceInfo: Decodable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let cities: [CityInfo]
}

struct CityInfo: Decodable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let districts: [DistrictInfo]
}

struct DistrictInfo: Decodable, Identifiable, Hashable {
    var id: String { name }
    let name: String
}

struct PositionDetail: Decodable, Identifiable {
    var id: String { positionId }
    let positionId: String
    let year: Int
    let positionCode: String
    let name: String
    let departmentFull: String
    let systemCode: String
    let region: String
    let city: String?
    let paperType: String?
    let headcount: Int
    let educationMin: String?
    let majorRequired: String?
    let politicalReq: String?
    let serviceMinYrs: Int?
    let hukouReq: String?
    let remark: String?
    let hasExtraExam: Bool
    let entryScore: EntryScoreStats?
}

struct PlacementQuestionSpec: Decodable {
    let moduleName: String
    let count: Int
}

struct PlacementTestTask: Decodable {
    let taskId: String
    let title: String
    let durationMinutes: Int
    let questionCount: Int
    let modulePlan: [PlacementQuestionSpec]
    let questions: [QuestionDetail]
}

struct PlacementAnswer: Encodable {
    let questionId: String
    let selectedAnswer: String
}

struct PlacementSubmitRequest: Encodable {
    let answers: [PlacementAnswer]
}

struct PlacementModuleResult: Decodable, Identifiable {
    var id: String { moduleName }
    let moduleName: String
    let correct: Int
    let total: Int
    let accuracy: Double
}

struct PlacementSubmitResponse: Decodable {
    let taskId: String
    let estimatedXingceScore: Double
    let baselineSource: String
    let moduleResults: [PlacementModuleResult]
}
