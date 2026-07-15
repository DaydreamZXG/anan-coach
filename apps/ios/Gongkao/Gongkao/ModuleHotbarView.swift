import SwiftUI

struct ModuleHotbarView: View {
    @Binding var selectedModule: GuzhuModule?
    var onSelect: (GuzhuModule?) -> Void = { _ in }

    var body: some View {
        HStack(spacing: 0) {
            // 全部：不按板块过滤
            Button {
                selectedModule = nil
                onSelect(nil)
            } label: {
                Text("全部")
                    .font(GuzhuFont.caption(11))
                    .foregroundStyle(selectedModule == nil ? GuzhuPalette.azurite : GuzhuPalette.faintInk)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(
                        selectedModule == nil
                            ? GuzhuPalette.azurite.opacity(0.15)
                            : Color.clear,
                        in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                    )
            }
            .buttonStyle(.plain)

            ForEach(GuzhuModule.allCases) { module in
                Button {
                    selectedModule = module
                    onSelect(module)
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: module.symbol)
                            .font(.system(size: 18, weight: .semibold))
                            .frame(width: 32, height: 32)
                            .background(
                                selectedModule == module
                                    ? module.color.opacity(0.15)
                                    : Color.clear,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                            )
                        Text(module.shortName)
                            .font(GuzhuFont.caption(11))
                    }
                    .foregroundStyle(
                        selectedModule == module
                            ? module.color
                            : GuzhuPalette.faintInk
                    )
                    .frame(maxWidth: .infinity)
                    .padding(.top, 8)
                    .padding(.bottom, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .background(GuzhuPalette.card)
        .overlay(
            Rectangle()
                .fill(GuzhuPalette.divider)
                .frame(height: 1),
            alignment: .top
        )
    }
}

#Preview {
    VStack {
        Spacer()
        ModuleHotbarView(selectedModule: .constant(GuzhuModule.yuanan as GuzhuModule?))
    }
    .background(GuzhuPalette.paper)
}
