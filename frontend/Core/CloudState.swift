import Foundation
import Observation

struct CloudPendingProgress: Codable, Sendable {
  var workspaceID: String
  var resourceID: String
  var mutationID = UUID().uuidString
  var value: CloudProgress
  // Optional for decoding snapshots created before operation-aware conflict merging.
  var baseMasteredIDs: [String]? = nil

  var masteryChanges: [String: Bool]? = nil

  var effectiveMasteryChanges: [String: Bool] {
    if let masteryChanges { return masteryChanges }
    let base = Set(baseMasteredIDs ?? value.mastered_ids)
    let local = Set(value.mastered_ids)
    var changes = Dictionary(uniqueKeysWithValues: local.subtracting(base).map { ($0, true) })
    for removed in base.subtracting(local) { changes[removed] = false }
    return changes
  }

  mutating func recordMastery(_ id: String, mastered: Bool) {
    var changes = effectiveMasteryChanges
    changes[id] = mastered
    masteryChanges = changes
    mutationID = UUID().uuidString
    var ids = Set(value.mastered_ids)
    if mastered { ids.insert(id) } else { ids.remove(id) }
    value.mastered_ids = ids.sorted()
  }

  mutating func rebaseMastery(onto server: CloudProgress) {
    let changes = effectiveMasteryChanges
    var ids = Set(server.mastered_ids)
    for (id, mastered) in changes {
      if mastered { ids.insert(id) } else { ids.remove(id) }
    }
    value.mastered_ids = ids.sorted()
    baseMasteredIDs = server.mastered_ids
    masteryChanges = changes
    value.revision = server.revision
  }

  mutating func acknowledgeMastery(_ sent: CloudPendingProgress, saved: CloudProgress) {
    // Keep operations made while the acknowledged request was in flight.
    masteryChanges = effectiveMasteryChanges.filter {
      sent.effectiveMasteryChanges[$0.key] != $0.value
    }
    rebaseMastery(onto: saved)
  }
  var path: String { "/api/workspaces/\(workspaceID)/resources/\(resourceID)" }
}

struct CloudPendingReview: Codable, Sendable {
  var eventID = UUID().uuidString
  var workspaceID: String
  var resourceID: String
  var cardID: String
  var path: String
  var rating: String
  var seconds: Double
  var planCompletion: CloudPlanCompletion? = nil
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
  var pendingReviews: [CloudPendingReview]? = nil
  var requiresPersonalization: Bool? = nil
  var dailyPlans: [String: CloudStudyPlan]? = nil
  var pendingPlanCompletions: [CloudPlanCompletion]? = nil
}

struct LocalCourseLearningOwner: Equatable, Sendable {
  let token: UUID
  let scope: String
  let epoch: UUID
  let courseID: String
}

enum LocalCourseLearningContext {
  @TaskLocal static var token: UUID?
}

@MainActor @Observable final class CloudState {
  var legacyPrivateSyncEnabled = false
  var localDeletedCourseIDs: Set<String> = []
  var archivedDuplicateCourseAliases: [String: String] = [:]
  @ObservationIgnored var duplicateMaintenanceTask: Task<Void, Never>?
  @ObservationIgnored var duplicateMaintenanceScope: String?
  var localCourseSources: [String: [SourceContent]] = [:]
  var localDiagnostics: [String: LocalDiagnosticSession] = [:]
  var privateLocalMode = false
  var localFirstChecked = false
  var publicLibraryDownloading = false
  var localFirstError: String?
  var migrationStatus: String?
  var account: CloudAccountState?
  var authenticated = false
  var localLogin = false
  var onboardingComplete = false
  var requiresPersonalization = false
  var isWorking = false
  var localGenerationStatuses: [String: LocalGenerationStatus] = [:]
  @ObservationIgnored var localLearningOwner: LocalCourseLearningOwner?
  @ObservationIgnored var cancelLocalLearningTask: (@Sendable () -> Void)?
  var localStageFingerprints: [String: String] = [:]
  @ObservationIgnored var stageSaveTasks: [String: Task<Void, Never>] = [:]
  var materialSyncCount = 0
  var isHydrating = false
  var syncing = false
  var error: String?
  var syncError: String?
  var cacheWriteError: String?
  var workspaces: [CloudWorkspace] = []
  var interview: CloudInterview?
  var showDiagnosis = false
  var choosingLearningMode = false
  var requestedChapterID: String?
  var showCompletionOnCompile = false
  var diagnosisWorkspaceID: String?
  var diagnosisResourceID: String?
  var diagnosisRetryAvailable = false
  var diagnosisRetryEpoch: UUID?
  var pending: [String: CloudPendingProgress] = [:]
  var pendingReviews: [CloudPendingReview] = []
  var cacheKey: String?
  var creationWorkspaceID: String?
  var pendingSelection: CloudBootstrap.Selection?
  var confirmedSelection: CloudBootstrap.Selection?
  var epoch = UUID()
  var syncTask: Task<Void, Never>?
  var recordsTask: Task<Void, Never>?
  var retryDelay: Duration = .seconds(15)
  var retryTask: Task<Void, Never>?
  var contentTask: Task<Void, Never>?
  var courseJob: CloudCourseJob?
  var courseTask: Task<CloudCourse, Error>?
}
