import AppKit
import Foundation
import Vision

struct InputRow: Decodable {
    let asset_id: String
    let source_path: String
}

struct OutputRow: Encodable {
    let asset_id: String
    let text: String
    let confidence: Float
    let error: String?
}

func recognize(_ row: InputRow) -> OutputRow {
    guard let image = NSImage(contentsOfFile: row.source_path) else {
        return OutputRow(asset_id: row.asset_id, text: "", confidence: 0, error: "image_open_failed")
    }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        return OutputRow(asset_id: row.asset_id, text: "", confidence: 0, error: "cgimage_failed")
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.01
    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
        let candidates = (request.results ?? []).compactMap { $0.topCandidates(1).first }
        let text = candidates.map(\.string).joined(separator: " ")
        let confidence = candidates.isEmpty
            ? 0
            : candidates.map(\.confidence).reduce(0, +) / Float(candidates.count)
        return OutputRow(asset_id: row.asset_id, text: text, confidence: confidence, error: nil)
    } catch {
        return OutputRow(asset_id: row.asset_id, text: "", confidence: 0, error: String(describing: error))
    }
}

guard CommandLine.arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: ocr_chinese_with_vision INPUT.jsonl OUTPUT.jsonl\n".utf8))
    exit(2)
}

let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let rows = try String(contentsOf: inputURL, encoding: .utf8)
    .split(whereSeparator: \.isNewline)
    .map { try JSONDecoder().decode(InputRow.self, from: Data($0.utf8)) }

let lock = NSLock()
var results = Array<OutputRow?>(repeating: nil, count: rows.count)
let queue = OperationQueue()
queue.maxConcurrentOperationCount = 4
for (index, row) in rows.enumerated() {
    queue.addOperation {
        let result = autoreleasepool { recognize(row) }
        lock.lock()
        results[index] = result
        lock.unlock()
    }
}
queue.waitUntilAllOperationsAreFinished()

let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
let payload = try results.compactMap { $0 }.map {
    String(data: try encoder.encode($0), encoding: .utf8)!
}.joined(separator: "\n") + "\n"
try payload.write(to: outputURL, atomically: true, encoding: .utf8)
