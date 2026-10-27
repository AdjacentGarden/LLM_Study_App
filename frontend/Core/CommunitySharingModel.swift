import CryptoKit
import Foundation
import Observation

private struct CommunitySharingPending: Codable {
  var operation: String
  var postID: String?
}

@MainActor @Observable final class CommunitySharingModel {
  private(set) var candidates: [CommunityBookShareCandidate] = []
  var selectedBookID = "" {
    didSet {
      if selectedBookID != oldValue {
        description = ""
        rightsConfirmed = false
        error = nil
        message = nil
      }
    }
  }
  var description = ""
  var rightsConfirmed = false
  private(set) var loading = false
  private(set) var submitting = false
  private(set) var checkingStatus = false
  private(set) var error: String?
  private(set) var message: String?
  private(set) var publicationRevision = 0
  let epoch: UUID
  private let service: CommunitySharingService?
  private let preferredBookID: String?
  private let storage: UserDefaults
  private let pendingKey: String
  private var pending: [String: CommunitySharingPending]
  private var valid = true
  private var generation = UUID()

  init(api: CloudAPI?, accountID: String?, epoch: UUID, preferredBookID: String? = nil,
    storage: UserDefaults = .standard) {
    self.epoch = epoch
    self.preferredBookID = preferredBookID
    self.storage = storage
    service = api.flatMap { api in accountID.map { CommunitySharingService(api: api, accountID: $0) } }
    let scope = (api?.baseURL.absoluteString ?? "") + "|" + (accountID ?? "")
    pendingKey = "community.sharing.pending." + SHA256.hash(data: Data(scope.utf8))
      .map { String(format: "%02x", $0) }.joined()
    pending = storage.data(forKey: pendingKey).flatMap {
      try? JSONDecoder().decode([String: CommunitySharingPending].self, from: $0)
    } ?? [:]
  }

  var selectedCandidate: CommunityBookShareCandidate? { candidates.first { $0.book_id == selectedBookID } }
  var publishedPostID: String? { selectedCandidate?.post_id }
  var publicationStatus: CommunityPublicationStatus? { selectedCandidate?.publication_status }
  var submissionUncertain: Bool { pending[selectedBookID] != nil }
  var isBusy: Bool { loading || submitting || checkingStatus }
  var canPublish: Bool {
    valid && service != nil && !isBusy && !submissionUncertain && selectedCandidate != nil
      && publicationStatus == .unshared && rightsConfirmed
      && description.trimmingCharacters(in: .whitespacesAndNewlines).count <= 500
  }
  var canWithdraw: Bool {
    valid && !isBusy && !submissionUncertain && selectedCandidate?.can_withdraw == true
      && publishedPostID != nil
  }

  func load() async {
    guard valid, !isBusy, let service else { return }
    loading = true
    error = nil
    message = nil
    let token = generation
    defer { if current(token) { loading = false } }
    do {
      let values = try await service.candidates()
      guard current(token) else { return }
      candidates = values
      if !values.contains(where: { $0.book_id == selectedBookID }) {
        if let preferredBookID {
          selectedBookID = values.first { $0.book_id == preferredBookID }?.book_id ?? ""
          if selectedBookID.isEmpty {
            message = "这份资料不在可分享教材中，请另选自己上传的 PDF。"
          }
        } else {
          selectedBookID = values.first?.book_id ?? ""
        }
      }
      for value in values { reconcile(value.book_id, status: value.publication_status, postID: value.post_id) }
      if submissionUncertain { message = "原提交结果尚未确认，请核对发布状态，避免重复提交。" }
    } catch { if current(token) { self.error = error.localizedDescription } }
  }

  func publish() async {
    guard canPublish, let service, let candidate = selectedCandidate else { return }
    let bookID = candidate.book_id, text = description
    submitting = true
    error = nil
    message = nil
    let token = generation
    setPending(bookID, .init(operation: "publish"))
    defer { if current(token) { submitting = false } }
    do {
      let receipt = try await service.publish(bookID: bookID, description: text, rightsConfirmed: rightsConfirmed)
      guard current(token) else { return }
      setPending(bookID, nil)
      apply(.init(book_id: bookID, post_id: receipt.post_id,
        publication_status: receipt.status == "shared" ? .shared : .sharedByOther,
        can_withdraw: receipt.status == "shared"))
      rightsConfirmed = false
      message = receipt.status == "shared" ? "已分享到发现。" : "这本教材已在发现中，没有重复发布。"
      publicationRevision += 1
      // A duplicate may belong to this account or another account. Resolve it by GET.
      if let status = try? await service.publication(bookID: bookID), current(token) { apply(status) }
    } catch {
      guard current(token) else { return }
      failedWrite(error, bookID: bookID)
    }
  }

  func refreshStatus() async {
    guard valid, !isBusy, let service, selectedCandidate != nil else { return }
    let bookID = selectedBookID, previous = selectedCandidate
    checkingStatus = true
    error = nil
    let token = generation
    defer { if current(token) { checkingStatus = false } }
    do {
      let status = try await service.publication(bookID: bookID)
      guard current(token) else { return }
      let intent = pending[bookID]
      apply(status)
      // This explicit read unlocks a new user action even when the prior write
      // never reached the server. Canonical book deduplication still prevents
      // duplicate publication; checking itself must never perform a POST.
      setPending(bookID, nil)
      if status.publication_status == .unshared {
        message = intent?.operation == "publish"
          ? "已核对，目前尚未发布。你可以再次确认发布。"
          : "已核对，这本教材目前未在发现中发布。"
      } else if intent?.operation == "withdraw" && status.post_id == intent?.postID {
        message = "已核对，原分享目前仍在发现中。你可以再次确认撤回。"
      } else {
        message = "已核对，这本教材已在发现中。"
      }
      if previous?.publication_status != status.publication_status || previous?.post_id != status.post_id {
        publicationRevision += 1
      }
    } catch { if current(token) { self.error = error.localizedDescription } }
  }

  /// The caller presents the withdrawal confirmation before invoking this method.
  func withdraw() async {
    guard canWithdraw, let service, let postID = publishedPostID else { return }
    let bookID = selectedBookID, token = generation
    submitting = true
    error = nil
    message = nil
    setPending(bookID, .init(operation: "withdraw", postID: postID))
    defer { if current(token) { submitting = false } }
    do {
      try await service.withdraw(postID: postID)
      guard current(token) else { return }
      setPending(bookID, nil)
      apply(.init(book_id: bookID, publication_status: .unshared, can_withdraw: false))
      message = "已从发现撤回，已导入者的副本会保留。"
      publicationRevision += 1
    } catch {
      guard current(token) else { return }
      failedWrite(error, bookID: bookID)
    }
  }

  func invalidateIdentity() {
    valid = false
    generation = UUID()
    candidates = []
    selectedBookID = ""
    loading = false
    submitting = false
    checkingStatus = false
    description = ""
    rightsConfirmed = false
    error = "账号或服务已变化，请重新打开分享窗口。"
    message = nil
  }

  private func current(_ token: UUID) -> Bool { valid && generation == token }

  private func setPending(_ bookID: String, _ value: CommunitySharingPending?) {
    pending[bookID] = value
    storage.set(try? JSONEncoder().encode(pending), forKey: pendingKey)
  }

  private func apply(_ publication: CommunityBookPublication) {
    guard let index = candidates.firstIndex(where: { $0.book_id == publication.book_id }) else { return }
    candidates[index].post_id = publication.post_id
    candidates[index].publication_status = publication.publication_status
    candidates[index].can_withdraw = publication.can_withdraw
  }

  private func reconcile(_ bookID: String, status: CommunityPublicationStatus, postID: String?) {
    guard let intent = pending[bookID] else { return }
    let confirmed = intent.operation == "publish" ? status != .unshared
      : status == .unshared || postID != intent.postID
    if confirmed {
      setPending(bookID, nil)
      publicationRevision += 1
    }
  }

  private func failedWrite(_ failure: Error, bookID: String) {
    if let response = failure as? CloudError, [400, 401, 403, 404, 409, 413, 422, 429].contains(response.status) {
      setPending(bookID, nil)
      error = response.localizedDescription
    } else {
      error = "提交结果尚未确认，请核对原教材的发布状态，避免重复提交。"
    }
  }
}
