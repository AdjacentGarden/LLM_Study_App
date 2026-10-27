import Foundation

enum CommunityPublicationStatus: String, Codable, Sendable {
  case unshared, shared
  case sharedByOther = "shared_by_other"
}

struct CommunityBookShareCandidate: Codable, Sendable, Identifiable, Equatable {
  var book_id: String
  var title: String
  var filename: String
  var page_count: Int
  var chapter_count: Int
  var summary: String
  var cover_url: String?
  var post_id: String?
  var publication_status: CommunityPublicationStatus
  var can_withdraw: Bool
  var id: String { book_id }
}

struct CommunityBookPublication: Codable, Sendable {
  var book_id: String
  var post_id: String?
  var publication_status: CommunityPublicationStatus
  var can_withdraw: Bool
}

struct CommunityShareReceipt: Codable, Sendable {
  var post_id: String
  var status: String
}

/// All writes here require a direct user action. These paths are outside
/// CloudAPI's catalog-import POST retry rule, so a lost reply is never replayed.
struct CommunitySharingService: Sendable {
  let api: CloudAPI
  let accountID: String

  private func requireAccount() async throws {
    try Task.checkCancellation()
    guard let account = await api.verifiedAccount(), account.account != nil,
      account.user_id == accountID else {
      throw CloudError(status: 401, message: "账号已变化，请重新打开分享窗口。")
    }
  }

  private func identifier(_ value: String) throws {
    guard !value.isEmpty, value.utf8.count <= 100, value.utf8.allSatisfy({
      (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
        || $0 == 45 || $0 == 95
    }) else { throw CloudError(status: 422, message: "教材或分享标识无效。") }
  }

  func candidates() async throws -> [CommunityBookShareCandidate] {
    try await requireAccount()
    let value: [CommunityBookShareCandidate] = try await api.request("/api/community/book-share-candidates")
    try await requireAccount()
    return value
  }

  func publication(bookID: String) async throws -> CommunityBookPublication {
    try identifier(bookID)
    try await requireAccount()
    let value: CommunityBookPublication = try await api.request("/api/community/books/\(bookID)/publication")
    try await requireAccount()
    guard value.book_id == bookID else { throw CloudError(status: 422, message: "发布状态与所选教材不一致。") }
    return value
  }

  func publish(bookID: String, description: String, rightsConfirmed: Bool) async throws -> CommunityShareReceipt {
    try identifier(bookID)
    let text = description.trimmingCharacters(in: .whitespacesAndNewlines)
    guard rightsConfirmed, text.count <= 500 else {
      throw CloudError(status: 422, message: rightsConfirmed ? "简介请控制在 500 字以内。" : "请先确认有权分享这本教材。")
    }
    try await requireAccount()
    let value: CommunityShareReceipt = try await api.request("/api/community/share", method: "POST", body: [
      "kind": .string("book"), "book_id": .string(bookID), "description": .string(text),
      "rights_confirmed": .bool(true),
    ])
    try await requireAccount()
    guard ["shared", "already_shared"].contains(value.status), !value.post_id.isEmpty else {
      throw CloudError(status: 0, message: "发布结果尚未确认，请核对原教材状态。")
    }
    return value
  }

  func withdraw(postID: String) async throws {
    try identifier(postID)
    try await requireAccount()
    let value: CloudOK = try await api.request("/api/community/\(postID)/withdraw", method: "POST")
    try await requireAccount()
    guard value.ok else { throw CloudError(status: 0, message: "撤回结果尚未确认，请核对原分享状态。") }
  }
}
