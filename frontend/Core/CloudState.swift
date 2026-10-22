import Foundation
import Observation

struct CloudPendingProgress: Codable, Sendable {
  var workspaceID: String
  var resourceID: String
  var mutationID = UUID().uuidString
  var value: CloudProgress
  var path: String { "/api/workspaces/\(workspaceID)/resources/\(resourceID)" }
}

struct CloudLocalSnapshot: Codable, Sendable {
  var snapshot: AppSnapshot
  var sources: [SourceContent]
  var workspaces: [CloudWorkspace]
  var filePaths: [String: String]
  var pending: [String: CloudPendingProgress]
  var creationWorkspaceID: String?
  var notes: [StudyNote]?
  var pendingSelection: CloudBootstrap.Selection?
  var onboardingComplete: Bool? = nil
}

@MainActor @Observable final class CloudState {
  var account: CloudAccountState?
  var authenticated = false
  var localLogin = false
  var onboardingComplete = false
  var isWorking = false
  var isHydrating = false
  var syncing = false
  var error: String?
  var syncError: String?
  var workspaces: [CloudWorkspace] = []
  var interview: CloudInterview?
  var showDiagnosis = false
  var requestedChapterID: String?
  var showCompletionOnCompile = false
  var diagnosisWorkspaceID: String?
  var diagnosisResourceID: String?
  var pending: [String: CloudPendingProgress] = [:]
  var cacheKey: String?
  var creationWorkspaceID: String?
  var pendingSelection: CloudBootstrap.Selection?
  var confirmedSelection: CloudBootstrap.Selection?
  var epoch = UUID()
  var syncTask: Task<Void, Never>?
  var recordsTask: Task<Void, Never>?
}
