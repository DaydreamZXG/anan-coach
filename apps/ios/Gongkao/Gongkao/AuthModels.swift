import Foundation

struct AppleLoginRequest: Encodable {
    let identityToken: String
    let authorizationCode: String
    let nonce: String
    let deviceId: String
    let displayName: String?
}

struct AuthSession: Codable {
    let accessToken: String
    let tokenType: String
    let expiresAt: String
    let user: AuthenticatedUser
}

struct AuthenticatedUser: Codable {
    let userId: String
    let learnerKey: String
    let displayName: String?
    let email: String?
}

struct LearnerMemory: Decodable {
    let profile: LearnerProfile
    let summary: LearnerMemorySummary
}

struct LearnerProfile: Decodable {
    let targetExam: String?
    let targetRegion: String?
    let positionGroup: String?
    let dailyMinutes: Int?
    let onboardingComplete: Bool
    let education: String?
    let major: String?
    let freshGraduate: Bool?
    let politicalStatus: String?
    let grassrootsYears: Int?
    let studyMode: String?
    let targetPositionId: String?
    let targetScore: Double?
    let baselineSource: String?
    let baselineXingceScore: Double?
    let baselineShenlunScore: Double?
}

struct LearnerProfileUpdate: Encodable {
    let targetExam: String
    let targetRegion: String
    let positionGroup: String
    let dailyMinutes: Int
}

struct LearnerMemorySummary: Decodable {
    let practiceAnswers: Int
    let wrongAnswers: Int
    let essayAttempts: Int
}
