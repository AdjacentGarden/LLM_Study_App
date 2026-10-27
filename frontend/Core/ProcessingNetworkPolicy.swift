import Foundation
import Network
import Observation

enum LocalProcessingOperation: String, CaseIterable, Identifiable, Equatable, Sendable {
  case ocr, asr, retrieval
  var id: String { rawValue }
  var title: String {
    switch self {
    case .ocr: "教材文字识别"
    case .asr: "语音识别"
    case .retrieval: "教材检索"
    }
  }
}

enum ProcessingRoute: Equatable, Sendable {
  case existing, onDevice, cancelled
}

enum ProcessingNetworkKind: Equatable, Sendable {
  case unknown, wifi, wired, cellular, offline
}

struct ProcessingNetworkPath: Equatable, Sendable {
  var kind: ProcessingNetworkKind
  var isExpensive = false
  var isConstrained = false

  static func snapshot(_ path: NWPath) -> Self {
    let kind: ProcessingNetworkKind
    if path.status != .satisfied { kind = .offline }
    else if path.usesInterfaceType(.cellular) { kind = .cellular }
    else if path.usesInterfaceType(.wifi) { kind = .wifi }
    else if path.usesInterfaceType(.wiredEthernet) { kind = .wired }
    else { kind = .unknown }
    return Self(kind: kind, isExpensive: path.isExpensive, isConstrained: path.isConstrained)
  }
}

struct ProcessingNetworkPrompt: Identifiable, Equatable, Sendable {
  let id: UUID
  var operations: [LocalProcessingOperation]
  var network: ProcessingNetworkKind
  var isExpensive: Bool
  var isConstrained: Bool
}

/// Path-only state: no UI, timers, networking, preferences, or model requests.
/// Repeated flags on one interface never restart its stability interval.
struct ProcessingNetworkStability {
  private(set) var path = ProcessingNetworkPath(kind: .unknown)
  private(set) var settledKind: ProcessingNetworkKind = .unknown
  private(set) var deadline: TimeInterval?
  private var candidate: ProcessingNetworkKind?
  private var receivedPath = false
  let wifiStability: TimeInterval
  let cellularDebounce: TimeInterval
  let unknownGrace: TimeInterval

  init(wifiStability: TimeInterval = 5, cellularDebounce: TimeInterval = 2,
    unknownGrace: TimeInterval = 2) {
    self.wifiStability = max(0, wifiStability)
    self.cellularDebounce = max(0, cellularDebounce)
    self.unknownGrace = max(0, unknownGrace)
  }

  var isSettled: Bool { receivedPath && candidate == nil }

  mutating func update(_ next: ProcessingNetworkPath, at time: TimeInterval) {
    path = next
    let unchanged = receivedPath
      && (candidate == next.kind || (candidate == nil && settledKind == next.kind))
    receivedPath = true
    if next.kind == .offline {
      candidate = nil
      deadline = nil
      settledKind = .offline
      return
    }
    if unchanged { advance(to: time); return }
    candidate = next.kind
    let delay: TimeInterval
    switch next.kind {
    case .wifi, .wired: delay = wifiStability
    case .cellular: delay = cellularDebounce
    case .unknown: delay = unknownGrace
    case .offline: delay = 0
    }
    deadline = time + delay
    advance(to: time)
  }

  mutating func advance(to time: TimeInterval) {
    guard let deadline, time >= deadline, let candidate else { return }
    settledKind = candidate
    self.candidate = nil
    self.deadline = nil
  }
}

/// A routing decision applies to one new operation only. A network update never
/// starts, cancels, restarts, or reroutes an operation that already received it.
@MainActor @Observable
final class ProcessingNetworkPolicy {
  private(set) var prompt: ProcessingNetworkPrompt?
  private(set) var path = ProcessingNetworkPath(kind: .unknown)
  private(set) var connection: ProcessingNetworkKind = .unknown
  private(set) var isSettled = false

  @ObservationIgnored private var stability: ProcessingNetworkStability
  @ObservationIgnored private let now: () -> TimeInterval
  @ObservationIgnored private let automaticallySchedules: Bool
  @ObservationIgnored private let promptCooldown: TimeInterval
  @ObservationIgnored private var monitor: NWPathMonitor?
  @ObservationIgnored private var monitorGeneration = UUID()
  @ObservationIgnored private var transitionTask: Task<Void, Never>?
  @ObservationIgnored private var cellularChoice: ProcessingRoute?
  @ObservationIgnored private var lastExplicitChoice: (route: ProcessingRoute, time: TimeInterval)?
  @ObservationIgnored private var pending: [UUID: Pending] = [:]

  private struct Pending {
    let operation: LocalProcessingOperation
    let continuation: CheckedContinuation<ProcessingRoute, Never>
  }

  init(monitoring: Bool = true, automaticallySchedules: Bool = true,
    now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
    wifiStability: TimeInterval = 5, cellularDebounce: TimeInterval = 2,
    unknownGrace: TimeInterval = 2, promptCooldown: TimeInterval = 600) {
    self.now = now
    self.automaticallySchedules = automaticallySchedules
    self.promptCooldown = max(0, promptCooldown)
    stability = ProcessingNetworkStability(wifiStability: wifiStability,
      cellularDebounce: cellularDebounce, unknownGrace: unknownGrace)
    // Initial unknown waits briefly for the first monitor delivery. It can only
    // fall back to local processing, never infer consent to use mobile data.
    stability.update(path, at: now())
    publishAndRoute(at: now())
    if monitoring { startMonitoring() }
  }

  var isStableWiFi: Bool { isSettled && connection == .wifi }

  func startMonitoring() {
    guard monitor == nil else { return }
    let generation = UUID()
    monitorGeneration = generation
    let next = NWPathMonitor()
    next.pathUpdateHandler = { [weak self] path in
      let snapshot = ProcessingNetworkPath.snapshot(path)
      Task { @MainActor [weak self] in
        guard let self, self.monitorGeneration == generation, self.monitor != nil else { return }
        self.updatePath(snapshot)
      }
    }
    monitor = next
    next.start(queue: DispatchQueue(label: "com.bookcourse.processing-network"))
  }

  func stopMonitoring() {
    monitorGeneration = UUID()
    monitor?.cancel()
    monitor = nil
    transitionTask?.cancel()
    transitionTask = nil
    cancelPending()
  }

  /// Injecting paths and monotonic time makes the same rules testable offline.
  func updatePath(_ next: ProcessingNetworkPath, at time: TimeInterval? = nil) {
    let instant = time ?? now()
    stability.update(next, at: instant)
    publishAndRoute(at: instant)
  }

  func advance(to time: TimeInterval? = nil) {
    let instant = time ?? now()
    stability.advance(to: instant)
    publishAndRoute(at: instant)
  }

  func route(for operation: LocalProcessingOperation) async -> ProcessingRoute {
    guard !Task.isCancelled else { return .cancelled }
    let id = UUID()
    let route: ProcessingRoute = await withTaskCancellationHandler {
      await withCheckedContinuation { continuation in
        guard !Task.isCancelled else { continuation.resume(returning: .cancelled); return }
        pending[id] = Pending(operation: operation, continuation: continuation)
        advance()
      }
    } onCancel: {
      Task { @MainActor [weak self] in self?.cancelRequest(id) }
    }
    return Task.isCancelled ? .cancelled : route
  }

  /// Pass the displayed prompt ID to ignore a late dismissal from an old UI.
  /// Dismissing/cancelling is never remembered as permission to use mobile data.
  func resolvePrompt(_ route: ProcessingRoute, promptID: UUID? = nil) {
    guard let current = prompt, promptID == nil || current.id == promptID else { return }
    if route != .cancelled {
      cellularChoice = route
      lastExplicitChoice = (route, now())
    }
    finishAll(with: route)
  }

  func cancelPending() {
    finishAll(with: .cancelled)
  }

  /// An explicit retry can ask again when the selected device capability is
  /// unavailable. It grants no cloud consent and leaves any open prompt and
  /// its concurrent requests intact.
  func reconsiderCellularChoice() {
    cellularChoice = nil
    lastExplicitChoice = nil
  }

  /// Call on account/identity changes. Choices live only in this instance and
  /// are never written to UserDefaults or shared with another account.
  func reset() {
    cancelPending()
    cellularChoice = nil
    lastExplicitChoice = nil
  }

  private func publishAndRoute(at time: TimeInterval) {
    path = stability.path
    connection = stability.settledKind
    isSettled = stability.isSettled
    scheduleTransition(at: time)
    guard isSettled else {
      if prompt != nil { refreshPrompt() }
      return
    }
    switch connection {
    case .wifi, .wired:
      cellularChoice = nil
      finishAll(with: .existing)
    case .offline, .unknown:
      finishAll(with: .onDevice)
    case .cellular:
      if cellularChoice == nil, let previous = lastExplicitChoice,
        time >= previous.time, time - previous.time < promptCooldown {
        cellularChoice = previous.route
      }
      if let cellularChoice { finishAll(with: cellularChoice) }
      else { refreshPrompt() }
    }
  }

  private func refreshPrompt() {
    guard !pending.isEmpty else { prompt = nil; return }
    let operations = LocalProcessingOperation.allCases.filter { operation in
      pending.values.contains { $0.operation == operation }
    }
    prompt = ProcessingNetworkPrompt(id: prompt?.id ?? UUID(), operations: operations,
      network: .cellular, isExpensive: path.isExpensive, isConstrained: path.isConstrained)
  }

  private func finishAll(with route: ProcessingRoute) {
    let waiting = pending.values.map(\.continuation)
    pending.removeAll()
    prompt = nil
    for continuation in waiting { continuation.resume(returning: route) }
  }

  private func cancelRequest(_ id: UUID) {
    guard let request = pending.removeValue(forKey: id) else { return }
    request.continuation.resume(returning: .cancelled)
    if pending.isEmpty { prompt = nil }
    else if prompt != nil { refreshPrompt() }
  }

  private func scheduleTransition(at time: TimeInterval) {
    transitionTask?.cancel()
    transitionTask = nil
    guard automaticallySchedules, let deadline = stability.deadline else { return }
    let delay = max(0, deadline - time)
    transitionTask = Task { @MainActor [weak self] in
      do { try await Task.sleep(for: .seconds(delay)) } catch { return }
      self?.advance()
    }
  }
}
