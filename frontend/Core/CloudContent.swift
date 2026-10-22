import Foundation

/// Versioned immutable bodies are shared independently of personal progress.
struct CloudContent: Codable, Sendable {
  var structure: CloudStructure?
  var courses: [CloudCourse]
  var source_content: SourceContent?

  init(resource: CloudResource) {
    structure = resource.structure
    courses = resource.courses
    source_content = resource.source_content
    if var source = source_content {
      for index in source.plan.tasks.indices {
        let prefix = "catalog:\(resource.id):"
        if source.plan.tasks[index].task_id.hasPrefix(prefix) {
          source.plan.tasks[index].task_id = "catalog:__shared__:" + source.plan.tasks[index].task_id.dropFirst(prefix.count)
        }
        source.plan.tasks[index].status = "pending"
      }
      source_content = source
    }
  }

  func applying(to resource: CloudResource) -> CloudResource {
    var result = resource
    result.structure = structure
    result.courses = courses
    result.source_content = source_content
    if var source = result.source_content {
      for index in source.plan.tasks.indices {
        source.plan.tasks[index].task_id = source.plan.tasks[index].task_id.replacingOccurrences(of: "catalog:__shared__:", with: "catalog:\(resource.id):")
      }
      result.source_content = source
    }
    return result
  }
}

extension AppStore {
  func hydrateCloudContent(workspaceID: String) async throws {
    guard let cloud, let account = cloudState.cacheKey else { return }
    let epoch = cloudState.epoch
    guard let wi = cloudState.workspaces.firstIndex(where: { $0.id == workspaceID }) else { return }
    for resource in cloudState.workspaces[wi].resources {
      guard let version = resource.content_key, let path = resource.content_path,
        resource.structure == nil && resource.source_content == nil else { continue }
      var content = try await repository.loadCloudContent(version, account: account)
      if content == nil {
        content = try await cloud.request(path, as: CloudContent.self)
        try await repository.saveCloudContent(content!, contentKey: version, account: account)
      }
      guard epoch == cloudState.epoch,
        let currentWI = cloudState.workspaces.firstIndex(where: { $0.id == workspaceID }),
        let ri = cloudState.workspaces[currentWI].resources.firstIndex(where: { $0.id == resource.id }),
        cloudState.workspaces[currentWI].resources[ri].content_key == version else { return }
      cloudState.workspaces[currentWI].resources[ri] = content!.applying(to: cloudState.workspaces[currentWI].resources[ri])
    }
    installCloudSources(workspaceID: workspaceID)
  }

  func loadSelectedCloudContent() {
    guard let wid = selectedCourseID else { return }
    cloudState.contentTask?.cancel()
    let epoch = cloudState.epoch
    cloudState.contentTask = Task { @MainActor in
      do {
        try await hydrateCloudContent(workspaceID: wid)
        guard epoch == cloudState.epoch, selectedCourseID == wid else { return }
        saveCloudCacheOnly()
      } catch {
        if let failure = error as? CloudError, failure.status == 401 { handleCloudError(error) }
        else if epoch == cloudState.epoch {
          cloudState.syncError = "这份资料尚未缓存，请联网后重试：" + error.localizedDescription
          if (error as? CloudError)?.transportFailure == true { scheduleCloudRetry() }
        }
      }
    }
  }

}
