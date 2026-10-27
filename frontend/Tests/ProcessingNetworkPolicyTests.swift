import Foundation
import XCTest

@testable import BookCourse

@MainActor private final class ProcessingPolicyClock {
  var time: TimeInterval = 0
}

@MainActor final class ProcessingNetworkPolicyTests: XCTestCase {
  private func policy(_ clock: ProcessingPolicyClock) -> ProcessingNetworkPolicy {
    ProcessingNetworkPolicy(monitoring: false, automaticallySchedules: false, now: { clock.time })
  }

  private func settle(_ kind: ProcessingNetworkKind, policy: ProcessingNetworkPolicy,
    clock: ProcessingPolicyClock) {
    policy.updatePath(ProcessingNetworkPath(kind: kind))
    switch kind {
    case .wifi, .wired: clock.time += 5
    case .cellular, .unknown: clock.time += 2
    case .offline: break
    }
    policy.advance()
  }

  private func allowTasks() async {
    for _ in 0..<20 { await Task.yield() }
  }

  private func displayedPrompt(_ policy: ProcessingNetworkPolicy,
    file: StaticString = #filePath, line: UInt = #line) async throws -> ProcessingNetworkPrompt {
    await allowTasks()
    return try XCTUnwrap(policy.prompt, file: file, line: line)
  }

  func testPureStabilityNeedsFiveContinuousWiFiSecondsAndFlagsDoNotRestartIt() {
    var state = ProcessingNetworkStability()
    state.update(ProcessingNetworkPath(kind: .wifi), at: 10)
    state.advance(to: 14.9)
    XCTAssertFalse(state.isSettled)
    state.update(ProcessingNetworkPath(kind: .wifi, isExpensive: true, isConstrained: true), at: 14.9)
    XCTAssertEqual(state.deadline, 15)
    state.advance(to: 15)
    XCTAssertTrue(state.isSettled)
    XCTAssertEqual(state.settledKind, .wifi)
    XCTAssertTrue(state.path.isExpensive)
    XCTAssertTrue(state.path.isConstrained)
  }

  func testPureStabilityDebouncesCellularAndRestartsWiFiAfterAnInterruption() {
    var state = ProcessingNetworkStability()
    state.update(ProcessingNetworkPath(kind: .wifi), at: 0)
    state.advance(to: 5)
    state.update(ProcessingNetworkPath(kind: .cellular), at: 6)
    state.advance(to: 7.9)
    XCTAssertFalse(state.isSettled)
    state.update(ProcessingNetworkPath(kind: .wifi), at: 7.9)
    XCTAssertEqual(state.deadline, 12.9)
    state.advance(to: 12.8)
    XCTAssertFalse(state.isSettled)
    state.advance(to: 12.9)
    XCTAssertEqual(state.settledKind, .wifi)
    XCTAssertTrue(state.isSettled)
    state.update(ProcessingNetworkPath(kind: .cellular), at: 20)
    state.advance(to: 22)
    XCTAssertEqual(state.settledKind, .cellular)
    XCTAssertTrue(state.isSettled)
  }

  func testPathUpdatesAloneNeverDisplayAPrompt() {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    XCTAssertNil(policy.prompt)
    clock.time = 900
    policy.advance()
    XCTAssertNil(policy.prompt)
  }

  func testWiFiOperationWaitsForStabilityThenUsesExistingEvenWhenExpensive() async {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    policy.updatePath(ProcessingNetworkPath(kind: .wifi, isExpensive: true, isConstrained: true))
    var completed = false
    let operation = Task {
      let route = await policy.route(for: .ocr)
      completed = true
      return route
    }
    await allowTasks()
    XCTAssertFalse(completed)
    XCTAssertNil(policy.prompt)
    clock.time = 4.9
    policy.advance()
    await allowTasks()
    XCTAssertFalse(completed)
    clock.time = 5
    policy.advance()
    let result = await operation.value
    XCTAssertEqual(result, .existing)
    XCTAssertTrue(policy.isStableWiFi)
    XCTAssertNil(policy.prompt)
  }

  func testNewOperationDuringWiFiToCellularDoesNotSlipThroughOldWiFiState() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.wifi, policy: policy, clock: clock)
    policy.updatePath(ProcessingNetworkPath(kind: .cellular))
    var completed = false
    let operation = Task {
      let route = await policy.route(for: .asr)
      completed = true
      return route
    }
    await allowTasks()
    XCTAssertFalse(completed)
    XCTAssertNil(policy.prompt)
    clock.time += 1.99
    policy.advance()
    await allowTasks()
    XCTAssertFalse(completed)
    clock.time += 0.01
    policy.advance()
    let prompt = try await displayedPrompt(policy)
    XCTAssertEqual(prompt.operations, [.asr])
    policy.resolvePrompt(.onDevice, promptID: prompt.id)
    let result = await operation.value
    XCTAssertEqual(result, .onDevice)
  }

  func testConcurrentOperationsShareOnePromptAndTheSameExplicitDecision() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .ocr) }
    let initial = try await displayedPrompt(policy)
    let second = Task { await policy.route(for: .asr) }
    let third = Task { await policy.route(for: .retrieval) }
    let combined = try await displayedPrompt(policy)
    XCTAssertEqual(combined.id, initial.id)
    XCTAssertEqual(combined.operations, [.ocr, .asr, .retrieval])
    policy.resolvePrompt(.onDevice, promptID: combined.id)
    let results = await [first.value, second.value, third.value]
    XCTAssertEqual(results, [.onDevice, .onDevice, .onDevice])
    XCTAssertNil(policy.prompt)
  }

  func testExplicitCloudChoiceLastsForCurrentCellularSessionWithoutRepeatedPrompts() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .ocr) }
    let prompt = try await displayedPrompt(policy)
    policy.resolvePrompt(.existing, promptID: prompt.id)
    let firstResult = await first.value
    XCTAssertEqual(firstResult, .existing)
    clock.time += 900
    let secondResult = await policy.route(for: .asr)
    XCTAssertEqual(secondResult, .existing)
    XCTAssertNil(policy.prompt)
  }

  func testWiFiReturnAutomaticallyRestoresNewOperationsAndCooldownReusesLocalChoice() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .asr) }
    let prompt = try await displayedPrompt(policy)
    policy.resolvePrompt(.onDevice, promptID: prompt.id)
    let originalRoute = await first.value
    XCTAssertEqual(originalRoute, .onDevice)
    settle(.wifi, policy: policy, clock: clock)
    let wifiRoute = await policy.route(for: .ocr)
    XCTAssertEqual(wifiRoute, .existing)
    // The already-returned recording route remains unchanged; no operation is restarted.
    XCTAssertEqual(originalRoute, .onDevice)
    settle(.cellular, policy: policy, clock: clock)
    let newRoute = await policy.route(for: .retrieval)
    XCTAssertEqual(newRoute, .onDevice)
    XCTAssertNil(policy.prompt)
  }

  func testNewCellularSessionAfterCooldownRequiresAnotherExplicitDecision() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .ocr) }
    let initial = try await displayedPrompt(policy)
    policy.resolvePrompt(.existing, promptID: initial.id)
    let firstResult = await first.value
    XCTAssertEqual(firstResult, .existing)
    settle(.wifi, policy: policy, clock: clock)
    clock.time += 600
    settle(.cellular, policy: policy, clock: clock)
    var completed = false
    let next = Task {
      let result = await policy.route(for: .asr)
      completed = true
      return result
    }
    let newPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(newPrompt.id, initial.id)
    XCTAssertFalse(completed)
    clock.time += 1_000
    policy.advance()
    await allowTasks()
    XCTAssertFalse(completed, "Silence is never consent to use the existing mobile-data path.")
    policy.resolvePrompt(.onDevice, promptID: newPrompt.id)
    let result = await next.value
    XCTAssertEqual(result, .onDevice)
  }

  func testBriefWiFiAndOfflineJitterPreserveTheCurrentCellularChoice() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let initial = Task { await policy.route(for: .retrieval) }
    let prompt = try await displayedPrompt(policy)
    policy.resolvePrompt(.onDevice, promptID: prompt.id)
    let result = await initial.value
    XCTAssertEqual(result, .onDevice)
    policy.updatePath(ProcessingNetworkPath(kind: .wifi))
    clock.time += 1
    policy.advance()
    settle(.cellular, policy: policy, clock: clock)
    let afterWiFi = await policy.route(for: .ocr)
    XCTAssertEqual(afterWiFi, .onDevice)
    policy.updatePath(ProcessingNetworkPath(kind: .offline))
    let offline = await policy.route(for: .asr)
    XCTAssertEqual(offline, .onDevice)
    settle(.cellular, policy: policy, clock: clock)
    let afterOffline = await policy.route(for: .asr)
    XCTAssertEqual(afterOffline, .onDevice)
    XCTAssertNil(policy.prompt)
  }

  func testOfflineAndUnresolvedInitialNetworkNeverAutomaticallyUseCloud() async {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    var completed = false
    let initial = Task {
      let result = await policy.route(for: .retrieval)
      completed = true
      return result
    }
    await allowTasks()
    XCTAssertFalse(completed)
    clock.time = 2
    policy.advance()
    let unknown = await initial.value
    XCTAssertEqual(unknown, .onDevice)
    policy.updatePath(ProcessingNetworkPath(kind: .offline))
    let offline = await policy.route(for: .ocr)
    XCTAssertEqual(offline, .onDevice)
    XCTAssertNil(policy.prompt)
  }

  func testStableWiFiResolvesAnOutstandingCellularPromptWithoutRerunningOperations() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    var completions = 0
    let operation = Task {
      let result = await policy.route(for: .ocr)
      completions += 1
      return result
    }
    let oldPrompt = try await displayedPrompt(policy)
    policy.updatePath(ProcessingNetworkPath(kind: .wifi))
    clock.time += 4.9
    policy.advance()
    await allowTasks()
    XCTAssertEqual(completions, 0)
    XCTAssertEqual(policy.prompt?.id, oldPrompt.id)
    clock.time += 0.1
    policy.advance()
    let result = await operation.value
    XCTAssertEqual(result, .existing)
    XCTAssertEqual(completions, 1)
    XCTAssertNil(policy.prompt)
    policy.resolvePrompt(.onDevice, promptID: oldPrompt.id)
    XCTAssertEqual(completions, 1)
  }

  func testCancellingOneConcurrentRequestKeepsTheOthersPromptAndCancelsOnlyThatTask() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .ocr) }
    let second = Task { await policy.route(for: .asr) }
    let initial = try await displayedPrompt(policy)
    first.cancel()
    let firstResult = await first.value
    XCTAssertEqual(firstResult, .cancelled)
    let remaining = try await displayedPrompt(policy)
    XCTAssertEqual(remaining.id, initial.id)
    XCTAssertEqual(remaining.operations, [.asr])
    policy.resolvePrompt(.onDevice, promptID: remaining.id)
    let secondResult = await second.value
    XCTAssertEqual(secondResult, .onDevice)
  }

  func testCancelDoesNotBecomeConsentAndResetClearsAccountChoicesAndPendingRequests() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let cancelled = Task { await policy.route(for: .ocr) }
    let oldPrompt = try await displayedPrompt(policy)
    policy.cancelPending()
    let cancelledResult = await cancelled.value
    XCTAssertEqual(cancelledResult, .cancelled)
    let fresh = Task { await policy.route(for: .asr) }
    let freshPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(oldPrompt.id, freshPrompt.id)
    policy.resolvePrompt(.existing, promptID: oldPrompt.id)
    XCTAssertEqual(policy.prompt?.id, freshPrompt.id)
    policy.resolvePrompt(.existing, promptID: freshPrompt.id)
    let freshResult = await fresh.value
    XCTAssertEqual(freshResult, .existing)
    policy.reset()
    let anotherAccount = Task { await policy.route(for: .retrieval) }
    let nextPrompt = try await displayedPrompt(policy)
    XCTAssertEqual(nextPrompt.operations, [.retrieval])
    policy.reset()
    let nextResult = await anotherAccount.value
    XCTAssertEqual(nextResult, .cancelled)
    XCTAssertNil(policy.prompt)
  }

  func testImportCancelledInTheSameTurnAsCloudConsentCannotStartWork() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    var starts = 0
    let importing = Task {
      let route = await policy.route(for: .ocr)
      if route != .cancelled && !Task.isCancelled { starts += 1 }
      return route
    }
    let prompt = try await displayedPrompt(policy)
    // Both occur synchronously on MainActor before the awaiting task resumes.
    policy.resolvePrompt(.existing, promptID: prompt.id)
    importing.cancel()
    let result = await importing.value
    XCTAssertEqual(result, .cancelled)
    XCTAssertEqual(starts, 0)
    XCTAssertNil(policy.prompt)
    // The user's explicit consent remains usable by a different, uncancelled operation.
    let separate = await policy.route(for: .asr)
    XCTAssertEqual(separate, .existing)
  }

  func testTaskCancelledBeforeRouteDoesNotCreateAPrompt() async {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let operation = Task { await policy.route(for: .ocr) }
    operation.cancel()
    let result = await operation.value
    XCTAssertEqual(result, .cancelled)
    XCTAssertNil(policy.prompt)
  }

  func testExplicitPromptCancellationIsNotRememberedAsMobileDataConsent() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .ocr) }
    let prompt = try await displayedPrompt(policy)
    policy.resolvePrompt(.cancelled, promptID: prompt.id)
    let result = await first.value
    XCTAssertEqual(result, .cancelled)
    let next = Task { await policy.route(for: .ocr) }
    let nextPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(nextPrompt.id, prompt.id)
    policy.resolvePrompt(.onDevice, promptID: nextPrompt.id)
    let nextResult = await next.value
    XCTAssertEqual(nextResult, .onDevice)
  }

  func testLosingNetworkWhilePromptIsOpenImmediatelyUsesLocalAndIgnoresLateConsent() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let first = Task { await policy.route(for: .asr) }
    let prompt = try await displayedPrompt(policy)
    policy.updatePath(ProcessingNetworkPath(kind: .offline))
    let result = await first.value
    XCTAssertEqual(result, .onDevice)
    XCTAssertNil(policy.prompt)
    policy.resolvePrompt(.existing, promptID: prompt.id)
    settle(.cellular, policy: policy, clock: clock)
    let next = Task { await policy.route(for: .ocr) }
    let nextPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(nextPrompt.id, prompt.id)
    policy.resolvePrompt(.onDevice, promptID: nextPrompt.id)
    let nextResult = await next.value
    XCTAssertEqual(nextResult, .onDevice)
  }

  func testReconsideringLocalChoiceAsksAgainPreservesOpenWaitersAndNeverTreatsCancelAsCloudConsent() async throws {
    let clock = ProcessingPolicyClock(), policy = policy(clock)
    settle(.cellular, policy: policy, clock: clock)
    let initial = Task { await policy.route(for: .asr) }
    let initialPrompt = try await displayedPrompt(policy)
    policy.resolvePrompt(.onDevice, promptID: initialPrompt.id)
    let initialRoute = await initial.value
    XCTAssertEqual(initialRoute, .onDevice)

    policy.reconsiderCellularChoice()
    XCTAssertNil(policy.prompt, "Reconsidering alone cannot submit work or display a prompt.")
    var completions = 0
    let retry = Task {
      let route = await policy.route(for: .asr)
      completions += 1
      return route
    }
    let concurrent = Task {
      let route = await policy.route(for: .retrieval)
      completions += 1
      return route
    }
    let retryPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(retryPrompt.id, initialPrompt.id)
    XCTAssertEqual(retryPrompt.operations, [.asr, .retrieval])
    policy.reconsiderCellularChoice()
    XCTAssertEqual(policy.prompt, retryPrompt)
    await allowTasks()
    XCTAssertEqual(completions, 0, "An open prompt and its waiters must not be cancelled or resolved.")
    policy.resolvePrompt(.cancelled, promptID: retryPrompt.id)
    let results = await [retry.value, concurrent.value]
    XCTAssertEqual(results, [.cancelled, .cancelled])

    let fresh = Task { await policy.route(for: .ocr) }
    let freshPrompt = try await displayedPrompt(policy)
    XCTAssertNotEqual(freshPrompt.id, retryPrompt.id)
    policy.resolvePrompt(.existing, promptID: freshPrompt.id)
    let freshRoute = await fresh.value
    XCTAssertEqual(freshRoute, .existing, "Cloud processing requires this new explicit decision.")
  }
}
