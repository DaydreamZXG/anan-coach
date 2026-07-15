import StoreKit
import SwiftUI

struct CommercePaywallPanel: View {
    @EnvironmentObject private var commerceStore: CommerceStore
    let accessToken: String?
    let userId: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header

            if commerceStore.loading && commerceStore.catalog == nil {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("正在读取 AI Pro 权益")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(DaziPalette.ink)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 8)
            } else {
                entitlementSummary
                quotaComparison
                productList
            }

            if let message = commerceStore.message {
                CommerceNotice(text: message, symbol: "checkmark.seal.fill", tint: DaziPalette.positive)
            }

            if let error = commerceStore.errorMessage {
                CommerceNotice(text: error, symbol: "exclamationmark.triangle.fill", tint: DaziPalette.alert)
            }

            Button {
                Task { await commerceStore.restorePurchases(accessToken: accessToken, userId: userId) }
            } label: {
                if commerceStore.restoring {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else {
                    Label("恢复购买", systemImage: "arrow.clockwise.circle")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.bordered)
            .disabled(commerceStore.restoring || accessToken == nil)
        }
        .panelStyle()
        .task {
            await commerceStore.load(accessToken: accessToken, userId: userId)
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            LumingMascotView(size: 54)
            VStack(alignment: .leading, spacing: 4) {
                Label("AI Pro", systemImage: "sparkles")
                    .font(.headline)
                    .foregroundStyle(DaziPalette.brand)
                Text("鹿鸣把选岗、批改、错题和计划重排放进同一套权益里。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var entitlementSummary: some View {
        let entitlement = commerceStore.entitlement
        return HStack(alignment: .center, spacing: 12) {
            Image(systemName: commerceStore.isPro ? "checkmark.seal.fill" : "seal")
                .font(.title3)
                .foregroundStyle(commerceStore.isPro ? DaziPalette.positive : DaziPalette.brand)
                .frame(width: 32, height: 32)

            VStack(alignment: .leading, spacing: 4) {
                Text(commerceStore.isPro ? "AI Pro 已开通" : "当前为免费版")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                Text(entitlementSubtitle(entitlement))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var quotaComparison: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("今日 AI 权益")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(DaziPalette.ink)

            let quotas = commerceStore.entitlement?.featureQuotas ?? commerceStore.catalog?.freeQuotas ?? []
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 132), spacing: 10)], spacing: 10) {
                ForEach(quotas.prefix(6)) { quota in
                    CommerceQuotaTile(quota: quota)
                }
            }
        }
    }

    @ViewBuilder
    private var productList: some View {
        if commerceStore.isPro {
            EmptyView()
        } else if commerceStore.catalog?.products.isEmpty == false {
            VStack(alignment: .leading, spacing: 10) {
                Text("选择套餐")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DaziPalette.ink)
                ForEach(commerceStore.catalog?.products ?? []) { product in
                    CommerceProductCard(
                        product: product,
                        storeProduct: commerceStore.sortedProducts.first { $0.id == product.productId },
                        purchasing: commerceStore.purchasingProductId == product.productId,
                        accessToken: accessToken,
                        userId: userId
                    )
                }
            }
        }
    }

    private func entitlementSubtitle(_ entitlement: CommerceEntitlementResponse?) -> String {
        guard let entitlement else { return "正在读取服务器权益状态" }
        if entitlement.isPro, let end = entitlement.subscriptionEnd {
            return "有效期至 \(end.formatted(date: .abbreviated, time: .omitted))"
        }
        return "免费额度用完后，AI 功能会由服务器拦截。"
    }
}

private struct CommerceProductCard: View {
    @EnvironmentObject private var commerceStore: CommerceStore
    let product: CommerceProduct
    let storeProduct: Product?
    let purchasing: Bool
    let accessToken: String?
    let userId: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(product.title)
                        .font(.headline)
                        .foregroundStyle(DaziPalette.ink)
                    Text(product.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 12)
                VStack(alignment: .trailing, spacing: 3) {
                    Text(storeProduct?.displayPrice ?? product.priceBand)
                        .font(.headline.monospacedDigit())
                        .foregroundStyle(DaziPalette.brand)
                    Text(product.periodLabel)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            VStack(alignment: .leading, spacing: 7) {
                ForEach(product.featureHighlights.prefix(4), id: \.self) { item in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(DaziPalette.positive)
                        Text(item)
                            .font(.caption)
                            .foregroundStyle(DaziPalette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            Button {
                guard let storeProduct else { return }
                Task { await commerceStore.purchase(storeProduct, accessToken: accessToken, userId: userId) }
            } label: {
                if purchasing {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else {
                    Label(storeProduct == nil ? "等待 App Store 商品" : "开通 \(product.periodLabel)", systemImage: "creditcard")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(DaziPalette.brand)
            .disabled(storeProduct == nil || purchasing || accessToken == nil)
        }
        .padding(14)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(DaziPalette.cardStroke, lineWidth: 1)
        )
    }
}

private struct CommerceQuotaTile: View {
    let quota: CommerceFeatureQuota

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(quota.title)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(quota.remaining)")
                    .font(.title3.weight(.bold).monospacedDigit())
                    .foregroundStyle(quota.remaining > 0 ? DaziPalette.ink : DaziPalette.alert)
                Text("/ \(quota.limit)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Text("\(quota.periodLabel)剩余")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 82, alignment: .leading)
        .background(DaziPalette.paper, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct CommerceNotice: View {
    let text: String
    let symbol: String
    let tint: Color

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
            Text(text)
                .font(.caption)
                .foregroundStyle(DaziPalette.ink)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
