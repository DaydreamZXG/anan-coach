import CryptoKit
import Foundation

enum APIClientError: LocalizedError {
    case authenticationRequired
    case appleCredentialInvalid
    case deviceIdentifierUnavailable
    case invalidResponse
    case httpStatus(Int, String)
    case invalidPath(String)

    var errorDescription: String? {
        switch self {
        case .authenticationRequired:
            "需要先用 Apple 登录。"
        case .appleCredentialInvalid:
            "Apple 登录凭证不可用。"
        case .deviceIdentifierUnavailable:
            "无法读取当前设备标识。"
        case .invalidResponse:
            "服务器响应不可用。"
        case .httpStatus(let status, let body):
            "服务器返回 \(status)：\(body)"
        case .invalidPath(let path):
            "无效的接口路径：\(path)"
        }
    }
}

struct APIClient {
    /// 默认连生产；本地开发时可在实例化时覆盖 baseURL
    var baseURL = URL(string: "https://kg.xingguoxian.cn")!
    var session: URLSession = .shared

    /// 复用 decoder，统一 ISO8601 日期解码策略
    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    func get<T: Decodable>(_ path: String, accessToken: String? = nil) async throws -> T {
        var request = URLRequest(url: try url(path))
        authorize(&request, accessToken: accessToken)
        return try await send(request)
    }

    func post<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        accessToken: String? = nil,
        idempotencyKey: String? = nil
    ) async throws -> Response {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let idempotencyKey {
            request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
        }
        authorize(&request, accessToken: accessToken)
        request.httpBody = try JSONEncoder().encode(body)
        return try await send(request)
    }

    func put<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        accessToken: String? = nil
    ) async throws -> Response {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request, accessToken: accessToken)
        request.httpBody = try JSONEncoder().encode(body)
        return try await send(request)
    }

    func postEmpty(_ path: String, accessToken: String? = nil) async throws {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "POST"
        authorize(&request, accessToken: accessToken)
        try await sendVoid(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIClientError.httpStatus(http.statusCode, body)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    private func sendVoid(_ request: URLRequest) async throws {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIClientError.httpStatus(http.statusCode, body)
        }
    }

    private func authorize(_ request: inout URLRequest, accessToken: String?) {
        if let accessToken {
            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }
    }

    private func url(_ path: String) throws -> URL {
        if path.hasPrefix("http"), let absolute = URL(string: path) {
            return absolute
        }
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIClientError.invalidPath(path)
        }
        return url
    }
}

/// Persists one logical paid command across transport failures and app restarts.
/// Only a body hash and generated command key are stored; request content is not.
struct DurableAPICommandKey {
    private struct Checkpoint: Codable {
        let bodyFingerprint: String
        let idempotencyKey: String
    }

    private static let lock = NSLock()
    private let namespace: String
    private let storageKey: String
    private let defaults: UserDefaults

    init(
        namespace: String,
        subject: String,
        defaults: UserDefaults = .standard
    ) {
        self.namespace = namespace
        self.defaults = defaults
        let subjectFingerprint = Self.fingerprint(Data(subject.utf8))
        storageKey = "gongkao.pending-api-command.v1.\(namespace).\(subjectFingerprint)"
    }

    func key<Body: Encodable>(for body: Body) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let fingerprint = Self.fingerprint(try encoder.encode(body))

        Self.lock.lock()
        defer { Self.lock.unlock() }
        if
            let data = defaults.data(forKey: storageKey),
            let checkpoint = try? JSONDecoder().decode(Checkpoint.self, from: data),
            checkpoint.bodyFingerprint == fingerprint
        {
            return checkpoint.idempotencyKey
        }

        let checkpoint = Checkpoint(
            bodyFingerprint: fingerprint,
            idempotencyKey: "\(namespace)-\(UUID().uuidString.lowercased())"
        )
        defaults.set(try JSONEncoder().encode(checkpoint), forKey: storageKey)
        return checkpoint.idempotencyKey
    }

    func complete() {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        defaults.removeObject(forKey: storageKey)
    }

    private static func fingerprint(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
