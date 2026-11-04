import Foundation
import Observation

struct CloudBookCoverRequest: Hashable {
  let path: String
  let origin: String
  let accountID: String
  let epoch: UUID
}

/// A cover belongs to the currently displayed book and verified identity.
@MainActor @Observable final class CloudBookCoverModel {
  private(set) var request: CloudBookCoverRequest?
  private(set) var data: Data?
  private var generation = UUID()

  func load(_ request: CloudBookCoverRequest?, api: CloudAPI?) async {
    let generation = UUID()
    self.generation = generation
    self.request = request
    data = nil
    guard let request, let api, api.baseURL.absoluteString == request.origin else { return }
    do {
      let bytes = try await api.downloadBookCover(request.path, accountID: request.accountID, epoch: request.epoch)
      try Task.checkCancellation()
      guard generation == self.generation else { return }
      data = bytes
    } catch {
      // Cover loading must not interrupt course navigation. The source placeholder remains visible.
    }
  }
}

/// Deterministic across launches and devices; never use Swift's randomized hashValue.
enum CourseCoverFallback {
  static let styles = ["violet", "mint", "blue", "peach", "rose", "gold"]
  static func path(seed: String) -> String {
    let hash = seed.utf8.reduce(UInt64(14695981039346656037)) { ($0 ^ UInt64($1)) &* 1099511628211 }
    return "covers/library-" + styles[Int(hash % UInt64(styles.count))] + ".svg"
  }
}
