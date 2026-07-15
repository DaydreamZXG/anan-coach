import SwiftUI

struct OnboardingConditionsStep: View {
    @EnvironmentObject private var store: OnboardingStore
    @State private var showMajorPicker = false

    private let educations = ["大专", "本科", "硕士研究生", "博士研究生"]
    private let politicalOptions = ["群众", "共青团员", "中共党员"]

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("这些条件决定你能报哪些岗位，来自国考公告的硬性要求。")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            OnboardingCard(title: "学历") {
                Picker("学历", selection: $store.education) {
                    ForEach(educations, id: \.self) { Text($0) }
                }
                .pickerStyle(.segmented)
            }

            OnboardingCard(title: "专业") {
                Button {
                    showMajorPicker = true
                } label: {
                    HStack {
                        if let major = store.selectedMajor {
                            Text("\(major.name)（\(major.code)）")
                                .foregroundStyle(.primary)
                        } else {
                            Text("请选择专业")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Text("按毕业证专业选择，三级学科匹配，确保岗位专业要求精准对应")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            OnboardingCard(title: "政治面貌") {
                Picker("政治面貌", selection: $store.politicalStatus) {
                    ForEach(politicalOptions, id: \.self) { Text($0) }
                }
                .pickerStyle(.segmented)
            }

            OnboardingCard(title: "身份") {
                Toggle("应届毕业生", isOn: $store.freshGraduate)
                Stepper("基层工作年限：\(store.grassrootsYears) 年", value: $store.grassrootsYears, in: 0...30)
            }
        }
        .onAppear {
            Task { await store.loadMajors() }
        }
        .sheet(isPresented: $showMajorPicker) {
            MajorPickerSheet()
        }
    }
}

/// 专业选择器 Sheet — 用三个 section 展示三级，支持搜索
struct MajorPickerSheet: View {
    @EnvironmentObject private var store: OnboardingStore
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @State private var expandedCategory: MajorCategory?
    @State private var expandedSub: MajorSubCategory?

    var body: some View {
        NavigationStack {
            List {
                if search.isEmpty {
                    ForEach(store.majorTree) { category in
                        DisclosureGroup(
                            isExpanded: Binding(
                                get: { expandedCategory?.code == category.code },
                                set: { open in
                                    expandedCategory = open ? category : nil
                                    expandedSub = nil
                                }
                            )
                        ) {
                            ForEach(category.subCategories) { sub in
                                DisclosureGroup(
                                    isExpanded: Binding(
                                        get: { expandedSub?.code == sub.code },
                                        set: { open in
                                            expandedSub = open ? sub : nil
                                        }
                                    )
                                ) {
                                    ForEach(sub.majors) { major in
                                        HStack {
                                            Text(major.name)
                                                .lineLimit(1)
                                            Spacer()
                                            Text(major.code)
                                                .font(.caption.monospaced())
                                                .foregroundStyle(.secondary)
                                        }
                                        .contentShape(Rectangle())
                                        .onTapGesture {
                                            store.selectedMajor = major
                                            dismiss()
                                        }
                                    }
                                } label: {
                                    HStack {
                                        Text(sub.name)
                                        Spacer()
                                        Text(sub.code)
                                            .font(.caption.monospaced())
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        } label: {
                            HStack {
                                Text(category.name)
                                Spacer()
                                Text(category.code)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                } else {
                    if searchResults.isEmpty {
                        Text("没有匹配的专业")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(searchResults) { item in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.major.name)
                                        .lineLimit(1)
                                    Text(item.path)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                                Spacer()
                                Text(item.major.code)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                store.selectedMajor = item.major
                                dismiss()
                            }
                        }
                    }
                }
            }
            .searchable(text: $search, prompt: "搜索专业名称或代码")
            .navigationTitle("选择专业")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }

    private var searchResults: [MajorSearchEntry] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return [] }
        var seen = Set<String>()
        var results: [MajorSearchEntry] = []
        for category in store.majorTree {
            let categoryHit = category.name.lowercased().contains(q) || category.code.lowercased().contains(q)
            for sub in category.subCategories {
                let subHit = sub.name.lowercased().contains(q) || sub.code.lowercased().contains(q)
                for major in sub.majors {
                    let majorHit = major.name.lowercased().contains(q) || major.code.lowercased().contains(q)
                    if (categoryHit || subHit || majorHit) && !seen.contains(major.code) {
                        seen.insert(major.code)
                        results.append(MajorSearchEntry(major: major, path: "\(category.name) · \(sub.name)"))
                    }
                }
            }
        }
        return Array(results.prefix(80))
    }
}

struct MajorSearchEntry: Identifiable {
    var id: String { major.code }
    let major: MajorItem
    let path: String
}

struct OnboardingTimeBudgetStep: View {
    @EnvironmentObject private var store: OnboardingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("搭子按你的真实时间排任务，不给你排做不完的计划。")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            OnboardingCard(title: "备考状态") {
                Picker("备考状态", selection: $store.studyMode) {
                    Text("在职备考").tag("parttime")
                    Text("全职备考").tag("fulltime")
                }
                .pickerStyle(.segmented)
                .onChange(of: store.studyMode) { _, mode in
                    store.dailyMinutes = mode == "fulltime" ? 480 : 120
                }
            }

            OnboardingCard(title: "每天可学习时间") {
                Stepper(
                    dailyLabel,
                    value: $store.dailyMinutes,
                    in: 30...720,
                    step: 30
                )
                Text(store.studyMode == "parttime"
                    ? "在职模式：任务拆成 25 分钟内的段落，通勤、午休、晚上各消化几个"
                    : "全职模式：按上午/下午/晚上三个时段安排成块训练、套题与每周模考")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var dailyLabel: String {
        let hours = Double(store.dailyMinutes) / 60
        return String(format: "%.1f 小时 / 天（%d 分钟）", hours, store.dailyMinutes)
    }
}

struct OnboardingCard<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(DaziPalette.card, in: RoundedRectangle(cornerRadius: 14))
    }
}

struct OnboardingPositionStep: View {
    @EnvironmentObject private var store: OnboardingStore
    @State private var inspectedPosition: PositionSummary?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("根据你的条件筛出可报岗位。选一个作为目标，也可以先跳过。")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            OnboardingCard(title: "地区筛选") {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        Picker("省份", selection: $store.selectedProvince) {
                            Text("全部省份").tag(ProvinceInfo?.none)
                            ForEach(store.provinces) { province in
                                Text(province.name).tag(ProvinceInfo?.some(province))
                            }
                        }
                        .pickerStyle(.menu)

                        Picker("城市", selection: $store.selectedCity) {
                            Text("全部城市").tag(CityInfo?.none)
                            ForEach(store.selectedProvince?.cities ?? []) { city in
                                Text(city.name).tag(CityInfo?.some(city))
                            }
                        }
                        .pickerStyle(.menu)
                        .disabled(store.selectedProvince == nil)

                        if let city = store.selectedCity, !city.districts.isEmpty {
                            Picker("区县", selection: $store.selectedDistrict) {
                                Text("全部区县").tag(DistrictInfo?.none)
                                ForEach(city.districts) { district in
                                    Text(district.name).tag(DistrictInfo?.some(district))
                                }
                            }
                            .pickerStyle(.menu)
                        }
                    }
                    Button("筛选岗位") {
                        Task { await store.searchPositions() }
                    }
                    .buttonStyle(.bordered)
                    .disabled(store.searching)
                }
            }

            if store.searching {
                ProgressView("正在筛选岗位…")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 30)
            } else if store.positions.isEmpty {
                Text("暂无符合条件的岗位。可调整地区，或点右下角下一步先跳过。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 30)
            } else {
                ForEach(store.positions) { position in
                    PositionRow(
                        position: position,
                        selected: store.selectedPosition?.positionId == position.positionId
                    ) {
                        inspectedPosition = position
                        Task { await store.loadPositionDetail(positionId: position.positionId) }
                    }
                }
            }
        }
        .onAppear {
            Task {
                await store.loadRegions()
                await store.loadMajors()
            }
        }
        .onChange(of: store.selectedProvince) { _, _ in
            store.selectedCity = nil
            store.selectedDistrict = nil
        }
        .onChange(of: store.selectedCity) { _, _ in
            store.selectedDistrict = nil
        }
        .sheet(item: $store.selectedPositionDetail) { detail in
            PositionDetailSheet(
                detail: detail,
                selected: store.selectedPosition?.positionId == detail.positionId
            ) {
                if store.selectedPosition?.positionId == detail.positionId {
                    withAnimation(.snappy) {
                        store.selectedPosition = nil
                        store.selectedDecisionItem = nil
                    }
                } else if let inspected = inspectedPosition {
                    withAnimation(.snappy) {
                        store.selectedPosition = inspected
                        store.selectedDecisionItem = nil
                    }
                }
                store.selectedPositionDetail = nil
            }
        }
    }
}
