import Foundation

@MainActor
final class DocketStore: ObservableObject {
    @Published var docket: DocketResponse?
    @Published var isOpening = false
    @Published var errorMessage: String?

    private let client: APIClient
    private var loadedAccessToken: String?

    init(client: APIClient = APIClient()) {
        self.client = client
    }

    func openCourt(accessToken: String, force: Bool = false) async {
        if loadedAccessToken != accessToken {
            clear()
            loadedAccessToken = accessToken
        }
        guard force || docket == nil else { return }
        isOpening = true
        errorMessage = nil
        defer { isOpening = false }
        do {
            let response: DocketResponse = try await client.post(
                "/api/v1/dockets/open-court",
                body: EmptyRequestBody(),
                accessToken: accessToken
            )
            docket = response
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clear() {
        docket = nil
        errorMessage = nil
        isOpening = false
    }
}

struct EmptyRequestBody: Encodable {}
