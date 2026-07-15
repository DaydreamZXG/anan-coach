import SwiftUI

enum GuzhuPalette {
    // MARK: 竹简纸底色（页面背景）
    static let paper = Color(
        light: Color(red: 0.93, green: 0.89, blue: 0.80),
        dark: Color(red: 0.15, green: 0.13, blue: 0.11)
    )

    // MARK: 羊皮纸卡片面
    static let card = Color(
        light: Color(red: 0.96, green: 0.93, blue: 0.86),
        dark: Color(red: 0.20, green: 0.18, blue: 0.15)
    )

    // MARK: 墨色（正文）
    static let ink = Color(
        light: Color(red: 0.17, green: 0.15, blue: 0.13),
        dark: Color(red: 0.92, green: 0.90, blue: 0.85)
    )

    // MARK: 朱砂红（品牌主色 / 印章 / 警示）
    static let cinnabar = Color(
        light: Color(red: 0.78, green: 0.24, blue: 0.18),
        dark: Color(red: 0.90, green: 0.40, blue: 0.30)
    )

    // MARK: 绛红（CTA 行动色 · 比朱砂更沉稳，压住古风底蕴，避免现代互联网刺眼红）
    static let vermilion = Color(
        light: Color(red: 0.66, green: 0.17, blue: 0.13),
        dark: Color(red: 0.82, green: 0.32, blue: 0.24)
    )
    static let vermilionDeep = Color(
        light: Color(red: 0.52, green: 0.12, blue: 0.10),
        dark: Color(red: 0.70, green: 0.24, blue: 0.18)
    )

    // MARK: 青瓷绿（正确 / 正面反馈）
    static let celadon = Color(
        light: Color(red: 0.42, green: 0.62, blue: 0.55),
        dark: Color(red: 0.55, green: 0.78, blue: 0.68)
    )

    // MARK: 赭石橙（提醒 / 中等难度）
    static let ochre = Color(
        light: Color(red: 0.80, green: 0.55, blue: 0.25),
        dark: Color(red: 0.90, green: 0.68, blue: 0.40)
    )

    // MARK: 石青蓝（链接 / 次要强调）
    static let azurite = Color(
        light: Color(red: 0.30, green: 0.45, blue: 0.65),
        dark: Color(red: 0.50, green: 0.65, blue: 0.85)
    )

    // MARK: 淡墨（次级文字 / 占位）
    static let faintInk = Color(
        light: Color(red: 0.17, green: 0.15, blue: 0.13).opacity(0.55),
        dark: Color.white.opacity(0.55)
    )

    // MARK: 描边
    static let cardStroke = Color(
        light: Color(red: 0.55, green: 0.45, blue: 0.30).opacity(0.25),
        dark: Color.white.opacity(0.12)
    )

    // MARK: 分隔线
    static let divider = Color(
        light: Color(red: 0.55, green: 0.45, blue: 0.30).opacity(0.15),
        dark: Color.white.opacity(0.08)
    )

    // MARK: 6大板块主题色
    static func moduleColor(_ module: String) -> Color {
        switch module {
        case "常识判断", "体察民情", "民情":
            return azurite
        case "言语理解与表达", "释义圣旨", "圣旨":
            return cinnabar
        case "数量关系", "统筹工程", "工程":
            return ochre
        case "判断推理", "堪破冤案", "冤案":
            return celadon
        case "资料分析", "核算钱粮", "钱粮":
            return Color(red: 0.55, green: 0.42, blue: 0.28)
        case "政治理论", "研读朝政", "朝政":
            return cinnabar
        default:
            return ink
        }
    }
}

// MARK: - 羊皮纸卡片（古风主卡片样式）

struct ParchmentCard: ViewModifier {
    var padding: CGFloat = 16
    var cornerStyle: UIRectCorner = .allCorners
    var cornerRadius: CGFloat = 8

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(GuzhuPalette.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(GuzhuPalette.cardStroke, lineWidth: 1)
            )
            .shadow(color: GuzhuPalette.ink.opacity(0.08), radius: 4, x: 0, y: 2)
    }
}

extension View {
    func parchmentCard(padding: CGFloat = 16, cornerRadius: CGFloat = 8) -> some View {
        modifier(ParchmentCard(padding: padding, cornerRadius: cornerRadius))
    }
}

// MARK: - 朱砂印章（答对/答错反馈）

struct SealStamp: View {
    let text: String
    let color: Color
    var size: CGFloat = 80

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .strokeBorder(color, lineWidth: 2)
                .frame(width: size, height: size)
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .strokeBorder(color.opacity(0.6), lineWidth: 1)
                .padding(4)
                .frame(width: size, height: size)
            Text(text)
                .font(.system(size: size * 0.35, weight: .bold, design: .serif))
                .foregroundStyle(color)
        }
        .opacity(0.85)
    }
}

// MARK: - 古风字体

enum GuzhuFont {
    static func title(_ size: CGFloat = 22) -> Font {
        .system(size: size, weight: .bold, design: .serif)
    }

    static func headline(_ size: CGFloat = 17) -> Font {
        .system(size: size, weight: .semibold, design: .serif)
    }

    static func body(_ size: CGFloat = 16) -> Font {
        .system(size: size, weight: .regular, design: .default)
    }

    static func caption(_ size: CGFloat = 13) -> Font {
        .system(size: size, weight: .regular, design: .default)
    }
}

// MARK: - 术语映射表

enum GuzhuTerms {
    static let moduleMap: [String: String] = [
        "常识判断": "体察民情",
        "言语理解与表达": "释义圣旨",
        "言语理解": "释义圣旨",
        "数量关系": "统筹工程",
        "判断推理": "堪破冤案",
        "资料分析": "核算钱粮",
        "政治理论": "研读朝政",
        "行测": "批阅奏折",
        "申论": "平灾奏折",
    ]

    static let globalMap: [String: String] = [
        "今日任务": "今日政务",
        "刷题": "批阅奏折",
        "错题": "误判案卷",
        "每日目标": "治绩目标",
        "学习时长": "理政时长",
        "目标分数": "治绩目标",
        "摸底测试": "初任考评",
        "岗位": "官职",
        "选岗": "择官选任",
        "模考": "会试",
    ]

    static func module(_ name: String) -> String {
        moduleMap[name] ?? name
    }

    static func global(_ text: String) -> String {
        globalMap[text] ?? text
    }
}

// MARK: - 6大板块定义

enum GuzhuModule: String, CaseIterable, Identifiable {
    case minqing = "体察民情"
    case shengzhi = "释义圣旨"
    case gongcheng = "统筹工程"
    case yuanan = "堪破冤案"
    case qianliang = "核算钱粮"
    case chaozheng = "研读朝政"

    var id: String { rawValue }

    var shortName: String {
        switch self {
        case .minqing: return "民情"
        case .shengzhi: return "圣旨"
        case .gongcheng: return "工程"
        case .yuanan: return "冤案"
        case .qianliang: return "钱粮"
        case .chaozheng: return "朝政"
        }
    }

    var originalName: String {
        switch self {
        case .minqing: return "常识判断"
        case .shengzhi: return "言语理解与表达"
        case .gongcheng: return "数量关系"
        case .yuanan: return "判断推理"
        case .qianliang: return "资料分析"
        case .chaozheng: return "政治理论"
        }
    }

    var symbol: String {
        switch self {
        case .minqing: return "person.2"
        case .shengzhi: return "scroll"
        case .gongcheng: return "building.2"
        case .yuanan: return "exclamationmark.shield"
        case .qianliang: return "chart.bar.doc.horizontal"
        case .chaozheng: return "flag"
        }
    }

    var color: Color {
        GuzhuPalette.moduleColor(rawValue)
    }

    /// 判断该板块是否与某个任务的模块名匹配（用于顶部分类标签过滤）
    func matches(moduleName: String?) -> Bool {
        guard let name = moduleName, !name.isEmpty else { return false }
        let aliases = [rawValue, originalName, shortName]
        return aliases.contains { alias in
            name == alias || name.contains(alias) || alias.contains(name)
        }
    }
}
