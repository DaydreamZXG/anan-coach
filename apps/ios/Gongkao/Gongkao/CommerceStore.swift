import Foundation
import StoreKit

@MainActor
final class CommerceStore: ObservableObject {
    @Published var catalog: CommerceCatalogResponse?
    @Published var entitlement: CommerceEntitlementResponse?
    @Published var storeProducts: [Product] = []
    @Published var loading = false
    @Published var purchasingProductId: String?
    @Published var restoring = false
    @Published var message: String?
    @Published var errorMessage: String?

    private let client: APIClient
    private var transactionUpdatesTask: Task<Void, Never>?
    private var currentAccessToken: String?
    private var currentUserId: String?

    init(client: APIClient = APIClient()) {
        self.client = client
        transactionUpdatesTask = observeTransactionUpdates()
    }

    deinit {
        transactionUpdatesTask?.cancel()
    }

    var isPro: Bool {
        entitlement?.isPro == true
    }

    var sortedProducts: [Product] {
        let order = Dictionary(uniqueKeysWithValues: (catalog?.products ?? []).map { ($0.productId, $0.sortOrder) })
        return storeProducts.sorted {
            (order[$0.id] ?? Int.max, $0.displayName) < (order[$1.id] ?? Int.max, $1.displayName)
        }
    }

    func commerceProduct(for productId: String) -> CommerceProduct? {
        catalog?.products.first { $0.productId == productId }
    }

    func load(accessToken: String?, userId: String?) async {
        currentAccessToken = accessToken
        currentUserId = userId
        guard let accessToken else {
            clear()
            return
        }
        loading = true
        defer { loading = false }
        do {
            async let loadedCatalog: CommerceCatalogResponse = client.get(
                "/api/v1/commerce/catalog",
                accessToken: accessToken
            )
            async let loadedEntitlement: CommerceEntitlementResponse = client.get(
                "/api/v1/commerce/entitlement",
                accessToken: accessToken
            )
            let catalog = try await loadedCatalog
            self.catalog = catalog
            self.entitlement = try await loadedEntitlement
            self.storeProducts = try await Product.products(
                for: catalog.products.map(\.productId)
            )
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func purchase(_ product: Product, accessToken: String?, userId: String?) async {
        guard let accessToken, let userId else {
            errorMessage = "需要先用 Apple 登录。"
            return
        }
        guard let appAccountToken = UUID(uuidString: userId) else {
            errorMessage = "当前账号标识不可用于 App Store 交易绑定。"
            return
        }
        purchasingProductId = product.id
        message = nil
        errorMessage = nil
        defer { purchasingProductId = nil }

        do {
            let result = try await product.purchase(options: [.appAccountToken(appAccountToken)])
            switch result {
            case .success(let verification):
                let verified = try verifiedTransaction(verification)
                let response = try await submit(
                    transaction: verified.transaction,
                    signedTransactionInfo: verified.signedTransactionInfo,
                    accessToken: accessToken
                )
                entitlement = response.entitlement
                if response.entitlement.isPro {
                    message = "AI Pro 已开通，鹿鸣会按 Pro 权益安排后续训练。"
                } else {
                    message = "交易已提交，服务器未授予 Pro：\(statusLabel(response.verificationStatus))。"
                }
                await verified.transaction.finish()
            case .userCancelled:
                message = "已取消购买。"
            case .pending:
                message = "交易正在等待 App Store 确认。"
            @unknown default:
                errorMessage = "App Store 返回了暂不支持的交易状态。"
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restorePurchases(accessToken: String?, userId: String?) async {
        guard let accessToken, let userId else {
            errorMessage = "需要先用 Apple 登录。"
            return
        }
        guard UUID(uuidString: userId) != nil else {
            errorMessage = "当前账号标识不可用于 App Store 交易绑定。"
            return
        }
        restoring = true
        message = nil
        errorMessage = nil
        defer { restoring = false }

        do {
            try await AppStore.sync()
            var restored = false
            for await result in Transaction.currentEntitlements {
                let verified = try verifiedTransaction(result)
                let transaction = verified.transaction
                guard commerceProduct(for: transaction.productID) != nil else { continue }
                let response = try await submit(
                    transaction: transaction,
                    signedTransactionInfo: verified.signedTransactionInfo,
                    accessToken: accessToken
                )
                entitlement = response.entitlement
                restored = response.entitlement.isPro || restored
                await transaction.finish()
            }
            message = restored ? "已恢复 AI Pro 权益。" : "未发现可恢复的 AI Pro 订阅。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clear() {
        currentAccessToken = nil
        currentUserId = nil
        catalog = nil
        entitlement = nil
        storeProducts = []
        purchasingProductId = nil
        restoring = false
        message = nil
        errorMessage = nil
    }

    private func observeTransactionUpdates() -> Task<Void, Never> {
        Task { [weak self] in
            for await result in Transaction.updates {
                guard let self else { return }
                do {
                    let verified = try self.verifiedTransaction(result)
                    let transaction = verified.transaction
                    guard self.commerceProduct(for: transaction.productID) != nil else {
                        await transaction.finish()
                        continue
                    }
                    if let accessToken = self.currentAccessToken {
                        let response = try await self.submit(
                            transaction: transaction,
                            signedTransactionInfo: verified.signedTransactionInfo,
                            accessToken: accessToken
                        )
                        self.entitlement = response.entitlement
                    }
                    await transaction.finish()
                    self.message = "App Store 已更新订阅交易，服务器权益已刷新。"
                } catch {
                    self.errorMessage = error.localizedDescription
                }
            }
        }
    }

    private func submit(
        transaction: Transaction,
        signedTransactionInfo: String,
        accessToken: String
    ) async throws -> AppStoreTransactionSubmitResponse {
        try await client.post(
            "/api/v1/commerce/app-store/transactions",
            body: AppStoreTransactionSubmitRequest(
                productId: transaction.productID,
                transactionId: String(transaction.id),
                originalTransactionId: String(transaction.originalID),
                signedTransactionInfo: signedTransactionInfo
            ),
            accessToken: accessToken
        )
    }

    private func verifiedTransaction(
        _ result: VerificationResult<Transaction>
    ) throws -> (transaction: Transaction, signedTransactionInfo: String) {
        switch result {
        case .unverified:
            throw CommerceStoreError.unverifiedTransaction
        case .verified(let safe):
            return (safe, result.jwsRepresentation)
        }
    }

    private func statusLabel(_ status: String) -> String {
        switch status {
        case "verified_expired": "订阅已过期"
        case "verified_revoked": "交易已撤销"
        default: status
        }
    }
}

enum CommerceStoreError: LocalizedError {
    case unverifiedTransaction

    var errorDescription: String? {
        switch self {
        case .unverifiedTransaction:
            "App Store 交易签名未通过本机校验。"
        }
    }
}
