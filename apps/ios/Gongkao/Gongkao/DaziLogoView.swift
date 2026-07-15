import SwiftUI

// MARK: - 搭子 Logo（D 字母渐变）
//
// 品牌符号：圆角粗体 D，对角线折角，蓝→青→紫三色渐变。
// 纯 SwiftUI 矢量实现，无级缩放，不依赖图片资源。

struct DaziLogoView: View {
    var size: CGFloat = 120

    var body: some View {
        DLetterShape()
            .fill(DaziGradient.brandLinear)
            .frame(width: size, height: size)
            .shadow(color: Color(red: 0.40, green: 0.30, blue: 0.95).opacity(0.35), radius: size * 0.10, x: 0, y: size * 0.04)
    }
}

// MARK: - D 字母形状（含左下角折角）

private struct DLetterShape: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width
        let h = rect.height
        let cornerRadius = w * 0.18
        let strokeWidth = w * 0.22
        let foldSize = w * 0.32

        var path = Path()

        // 左竖（顶部圆角）
        path.move(to: CGPoint(x: cornerRadius, y: 0))
        path.addLine(to: CGPoint(x: w * 0.62, y: 0))

        // 右上弧
        let arcCenterTop = CGPoint(x: w * 0.62, y: h * 0.30)
        path.addArc(
            center: arcCenterTop,
            radius: h * 0.30,
            startAngle: .degrees(-90),
            endAngle: .degrees(0),
            clockwise: false
        )

        // 右弧
        let arcCenterRight = CGPoint(x: w * 0.62, y: h * 0.50)
        path.addArc(
            center: arcCenterRight,
            radius: h * 0.50 - strokeWidth / 2,
            startAngle: .degrees(0),
            endAngle: .degrees(90),
            clockwise: false
        )

        // 右下弧
        let arcCenterBottom = CGPoint(x: w * 0.45, y: h * 0.72)
        path.addArc(
            center: arcCenterBottom,
            radius: h * 0.28,
            startAngle: .degrees(0),
            endAngle: .degrees(90),
            clockwise: false
        )

        // 底部（左竖底部圆角）——走到折角处
        path.addLine(to: CGPoint(x: foldSize, y: h))
        path.addLine(to: CGPoint(x: 0, y: h - foldSize))
        path.addLine(to: CGPoint(x: 0, y: cornerRadius))
        path.addArc(
            center: CGPoint(x: cornerRadius, y: cornerRadius),
            radius: cornerRadius,
            startAngle: .degrees(180),
            endAngle: .degrees(270),
            clockwise: false
        )
        path.closeSubpath()

        // 内孔（D 的白色部分）
        let innerW = w * 0.42
        let innerH = h * 0.42
        let innerX = strokeWidth
        let innerY = (h - innerH) / 2
        let innerRect = CGRect(x: innerX, y: innerY, width: innerW, height: innerH)

        var innerPath = Path()
        innerPath.move(to: CGPoint(x: innerRect.minX, y: innerRect.midY))
        innerPath.addArc(
            center: CGPoint(x: innerRect.midX, y: innerRect.midY),
            radius: innerH / 2,
            startAngle: .degrees(180),
            endAngle: .degrees(270),
            clockwise: false
        )
        innerPath.addLine(to: CGPoint(x: innerRect.maxX, y: innerRect.minY))
        innerPath.addLine(to: CGPoint(x: innerRect.maxX, y: innerRect.maxY))
        innerPath.addArc(
            center: CGPoint(x: innerRect.midX, y: innerRect.midY),
            radius: innerH / 2,
            startAngle: .degrees(90),
            endAngle: .degrees(180),
            clockwise: false
        )
        innerPath.closeSubpath()

        path.addPath(innerPath)

        return path
    }
}

#Preview {
    HStack(spacing: 30) {
        DaziLogoView(size: 80)
        DaziLogoView(size: 120)
        DaziLogoView(size: 200)
    }
    .padding(40)
    .background(DaziPalette.paper)
}
