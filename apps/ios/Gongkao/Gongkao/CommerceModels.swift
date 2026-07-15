import Foundation

struct CommerceCatalogResponse: Decodable {
    let products: [CommerceProduct]
    let freeQuotas: [CommerceFeatureQuota]
    let proQuotas: [CommerceFeatureQuota]
}

struct CommerceProduct: Decodable, Identifiable {
    var id: String { productId }

    let productId: String
    let tier: String
    let billingPeriod: String
    let title: String
    let subtitle: String
    let priceBand: String
    let sortOrder: Int
    let featureHighlights: [String]

    var periodLabel: String {
        switch billingPeriod {
        case "monthly": "月度"
        case "yearly": "年度"
        default: billingPeriod
        }
    }
}

struct CommerceFeatureQuota: Decodable, Identifiable {
    var id: String { feature }

    let feature: String
    let title: String
    let limit: Int
    let used: Int
    let remaining: Int
    let period: String

    var periodLabel: String {
        switch period {
        case "day": "每日"
        case "month": "每月"
        default: period
        }
    }
}

struct CommerceEntitlementResponse: Decodable {
    let tier: String
    let source: String
    let isActive: Bool
    let subscriptionStart: Date?
    let subscriptionEnd: Date?
    let featureQuotas: [CommerceFeatureQuota]
    let productIds: [String]
    let serverTime: Date

    var isPro: Bool {
        tier == "pro" && isActive
    }
}

struct AppStoreTransactionSubmitRequest: Encodable {
    let productId: String
    let transactionId: String
    let originalTransactionId: String
    let signedTransactionInfo: String
}

struct AppStoreTransactionSubmitResponse: Decodable {
    let transactionId: String
    let originalTransactionId: String
    let verificationStatus: String
    let entitlement: CommerceEntitlementResponse
}
