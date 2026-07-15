import SwiftUI
import UIKit

struct QuestionRichText: View {
    let plainText: String
    let htmlText: String?
    var font: Font = .body
    var uiFont: UIFont = .preferredFont(forTextStyle: .body)
    var lineSpacing: CGFloat = 4
    var foregroundColor: Color = GuzhuPalette.ink
    var textSelectionEnabled = false

    var body: some View {
        if textSelectionEnabled {
            renderedText
                .textSelection(.enabled)
        } else {
            renderedText
        }
    }

    private var renderedText: some View {
        Group {
            if let attributedText {
                Text(attributedText)
            } else {
                Text(plainText)
            }
        }
        .font(font)
        .lineSpacing(lineSpacing)
        .foregroundStyle(foregroundColor)
        .fixedSize(horizontal: false, vertical: true)
    }

    private var attributedText: AttributedString? {
        guard let htmlText,
              !htmlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            return nil
        }
        return QuestionHTMLRenderer.attributedString(
            from: htmlText,
            baseFont: uiFont
        )
    }
}

private enum QuestionHTMLRenderer {
    static func attributedString(from html: String, baseFont: UIFont) -> AttributedString? {
        let normalizedHTML = normalizeAnswerBlanks(in: html)
        let document = """
        <!doctype html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
        body { margin: 0; padding: 0; font-family: -apple-system; font-size: \(baseFont.pointSize)px; }
        p { margin: 0 0 6px 0; }
        u { text-decoration: underline; text-underline-offset: 3px; }
        </style>
        </head>
        <body>\(normalizedHTML)</body>
        </html>
        """
        guard let data = document.data(using: .utf8) else { return nil }
        guard let imported = try? NSAttributedString(
            data: data,
            options: [
                .documentType: NSAttributedString.DocumentType.html,
                .characterEncoding: String.Encoding.utf8.rawValue,
            ],
            documentAttributes: nil
        ) else {
            return nil
        }

        let mutable = NSMutableAttributedString(attributedString: imported)
        trimWhitespace(in: mutable)
        normalizeAttributes(in: mutable, baseFont: baseFont)
        return AttributedString(mutable)
    }

    private static func normalizeAnswerBlanks(in html: String) -> String {
        let pattern = #"<u\b([^>]*)>(.*?)</u>"#
        guard let regex = try? NSRegularExpression(
            pattern: pattern,
            options: [.caseInsensitive, .dotMatchesLineSeparators]
        ) else {
            return html
        }

        let sourceRange = NSRange(html.startIndex..<html.endIndex, in: html)
        let matches = regex.matches(in: html, range: sourceRange)
        guard !matches.isEmpty else { return html }

        let mutable = NSMutableString(string: html)
        for match in matches.reversed() {
            guard match.numberOfRanges == 3,
                  let attributesRange = Range(match.range(at: 1), in: html),
                  let contentRange = Range(match.range(at: 2), in: html)
            else {
                continue
            }

            let content = String(html[contentRange])
            guard isBlankUnderlineContent(content) else { continue }

            let attributes = String(html[attributesRange])
            let placeholder = String(
                repeating: "&#95;",
                count: answerBlankGlyphCount(for: content)
            )
            mutable.replaceCharacters(
                in: match.range,
                with: "<u\(attributes)>\(placeholder)</u>"
            )
        }
        return mutable as String
    }

    private static func isBlankUnderlineContent(_ htmlFragment: String) -> Bool {
        visibleText(from: htmlFragment).unicodeScalars.allSatisfy(isHTMLWhitespace)
    }

    private static func answerBlankGlyphCount(for htmlFragment: String) -> Int {
        let whitespaceUnits = visibleText(from: htmlFragment).unicodeScalars.reduce(0) { count, scalar in
            guard isHTMLWhitespace(scalar) else { return count }
            return count + (scalar.value == 0x3000 ? 2 : 1)
        }
        return min(10, max(4, Int(ceil(Double(max(whitespaceUnits, 1)) / 2.0))))
    }

    private static func visibleText(from htmlFragment: String) -> String {
        let withoutTags = htmlFragment.replacingOccurrences(
            of: #"<[^>]+>"#,
            with: "",
            options: .regularExpression
        )
        return decodeWhitespaceEntities(in: withoutTags)
    }

    private static func decodeWhitespaceEntities(in text: String) -> String {
        var decoded = text
        decoded = decoded.replacingOccurrences(of: "&nbsp;", with: " ", options: .caseInsensitive)
        decoded = decoded.replacingOccurrences(of: "&ensp;", with: " ", options: .caseInsensitive)
        decoded = decoded.replacingOccurrences(of: "&emsp;", with: "  ", options: .caseInsensitive)
        decoded = decoded.replacingOccurrences(of: "&thinsp;", with: " ", options: .caseInsensitive)
        decoded = decoded.replacingOccurrences(of: #"&#0*160;"#, with: " ", options: .regularExpression)
        decoded = decoded.replacingOccurrences(
            of: #"&#x0*a0;"#,
            with: " ",
            options: [.regularExpression, .caseInsensitive]
        )
        return decoded
    }

    private static func isHTMLWhitespace(_ scalar: UnicodeScalar) -> Bool {
        CharacterSet.whitespacesAndNewlines.contains(scalar)
            || scalar.value == 0x00A0
            || scalar.value == 0x3000
    }

    private static func normalizeAttributes(
        in text: NSMutableAttributedString,
        baseFont: UIFont
    ) {
        let range = NSRange(location: 0, length: text.length)
        text.removeAttribute(.foregroundColor, range: range)
        text.removeAttribute(.backgroundColor, range: range)
        text.enumerateAttribute(.font, in: range) { value, range, _ in
            let font = normalizedFont(from: value as? UIFont, baseFont: baseFont)
            text.addAttribute(.font, value: font, range: range)
        }
    }

    private static func normalizedFont(from imported: UIFont?, baseFont: UIFont) -> UIFont {
        guard let imported else { return baseFont }
        let retainedTraits = imported.fontDescriptor.symbolicTraits.intersection([
            .traitBold,
            .traitItalic,
        ])
        guard let descriptor = baseFont.fontDescriptor.withSymbolicTraits(retainedTraits) else {
            return baseFont
        }
        return UIFont(descriptor: descriptor, size: baseFont.pointSize)
    }

    private static func trimWhitespace(in text: NSMutableAttributedString) {
        while text.length > 0, text.string.last?.isWhitespace == true {
            text.deleteCharacters(in: NSRange(location: text.length - 1, length: 1))
        }
        while text.length > 0, text.string.first?.isWhitespace == true {
            text.deleteCharacters(in: NSRange(location: 0, length: 1))
        }
    }
}
