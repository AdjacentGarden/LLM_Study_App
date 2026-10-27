import Foundation
import Speech

enum AppleSpeechError: LocalizedError, Equatable, Sendable {
  case permissionDescriptionMissing, denied, restricted, onDeviceUnavailable, unavailable
  case invalidAudio, timedOut, emptyTranscription, busy, recognitionFailed(String)
  var errorDescription: String? {
    switch self {
    case .permissionDescriptionMissing: "应用尚未配置系统语音识别权限说明，暂不能在本机识别。"
    case .denied: "系统语音识别权限未获允许，请在系统设置中开启后重试。"
    case .restricted: "这台设备限制了语音识别，原始录音仍保留。"
    case .onDeviceUnavailable: "本机中文语音识别模型不可用或尚未安装。请先在 Wi-Fi 下准备系统语音能力；录音不会上传。"
    case .unavailable: "系统本机语音识别暂不可用，请稍后重试；录音不会上传。"
    case .invalidAudio: "请先保存有效的本机录音，再生成笔记。"
    case .timedOut: "本机语音识别超时，原始录音仍保留，可以重试。"
    case .emptyTranscription: "没有识别到可用语音，请回放录音后重试。"
    case .busy: "本机正在识别另一段录音，请稍后重试。"
    case .recognitionFailed(let message): "本机语音识别未完成：\(message)。录音不会自动上传。"
    }
  }
}

struct AppleSpeechCapability: Sendable {
  var available: Bool
  var supportsOnDeviceRecognition: Bool
  var needsAuthorization: Bool
  var message: String
}

@MainActor protocol AppleSpeechRecognitionHandle: AnyObject { func cancel() }
@MainActor protocol AppleSpeechRecognitionBackend: AnyObject {
  var authorizationStatus: SFSpeechRecognizerAuthorizationStatus { get }
  var supportsOnDeviceRecognition: Bool { get }
  var isAvailable: Bool { get }
  func authorize(_ completion: @escaping @Sendable (SFSpeechRecognizerAuthorizationStatus) -> Void)
  func recognize(fileURL: URL, requiresOnDeviceRecognition: Bool,
    completion: @escaping @Sendable (Result<String, AppleSpeechError>) -> Void) -> (any AppleSpeechRecognitionHandle)?
}

@MainActor private final class SystemSpeechHandle: AppleSpeechRecognitionHandle {
  let task: SFSpeechRecognitionTask
  init(_ task: SFSpeechRecognitionTask) { self.task = task }
  func cancel() { task.cancel() }
}

@MainActor private final class SystemAppleSpeechBackend: AppleSpeechRecognitionBackend {
  let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
  var authorizationStatus: SFSpeechRecognizerAuthorizationStatus { SFSpeechRecognizer.authorizationStatus() }
  var supportsOnDeviceRecognition: Bool { recognizer?.supportsOnDeviceRecognition == true }
  var isAvailable: Bool { recognizer?.isAvailable == true }
  func authorize(_ completion: @escaping @Sendable (SFSpeechRecognizerAuthorizationStatus) -> Void) {
    SFSpeechRecognizer.requestAuthorization(completion)
  }
  func recognize(fileURL: URL, requiresOnDeviceRecognition: Bool,
    completion: @escaping @Sendable (Result<String, AppleSpeechError>) -> Void) -> (any AppleSpeechRecognitionHandle)? {
    guard let recognizer, recognizer.supportsOnDeviceRecognition, requiresOnDeviceRecognition else {
      completion(.failure(.onDeviceUnavailable)); return nil
    }
    let request = SFSpeechURLRecognitionRequest(url: fileURL)
    request.requiresOnDeviceRecognition = true
    request.shouldReportPartialResults = false
    request.addsPunctuation = true
    request.taskHint = .dictation
    let task = recognizer.recognitionTask(with: request) { result, error in
      if let result, result.isFinal {
        let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
        completion(text.isEmpty ? .failure(.emptyTranscription) : .success(text))
      } else if let error {
        completion(.failure(.recognitionFailed(error.localizedDescription)))
      }
    }
    return SystemSpeechHandle(task)
  }
}

@MainActor private final class AppleSpeechOperation {
  var continuation: CheckedContinuation<String, Error>?
  var handle: (any AppleSpeechRecognitionHandle)?
  var timeout: Task<Void, Never>?
  var finished = false
  func finish(_ result: Result<String, Error>) {
    guard !finished else { return }
    finished = true
    timeout?.cancel(); timeout = nil
    handle?.cancel(); handle = nil
    continuation?.resume(with: result); continuation = nil
  }
}

/// Uses only the installed Speech framework recognizer. It has no network
/// client, asset-download request, or remote fallback.
@MainActor final class AppleSpeechTranscriber {
  private let backend: any AppleSpeechRecognitionBackend
  private let permissionDescriptionPresent: Bool
  private var current: AppleSpeechOperation?

  convenience init() {
    self.init(backend: SystemAppleSpeechBackend(), permissionDescriptionPresent:
      !(Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") as? String ?? "").isEmpty)
  }
  init(backend: any AppleSpeechRecognitionBackend, permissionDescriptionPresent: Bool) {
    self.backend = backend
    self.permissionDescriptionPresent = permissionDescriptionPresent
  }

  static func capability() -> AppleSpeechCapability { AppleSpeechTranscriber().capability }
  var capability: AppleSpeechCapability {
    let status = backend.authorizationStatus
    let error: AppleSpeechError? = !permissionDescriptionPresent ? .permissionDescriptionMissing
      : status == .denied ? .denied : status == .restricted ? .restricted
      : !backend.supportsOnDeviceRecognition ? .onDeviceUnavailable : !backend.isAvailable ? .unavailable : nil
    return AppleSpeechCapability(available: error == nil,
      supportsOnDeviceRecognition: backend.supportsOnDeviceRecognition,
      needsAuthorization: status == .notDetermined,
      message: error?.localizedDescription ?? "使用 Apple 系统中文语音识别，录音保留在本机。")
  }

  func transcribe(fileURL: URL, timeoutSeconds: Double = 120) async throws -> String {
    try Task.checkCancellation()
    guard current == nil else { throw AppleSpeechError.busy }
    guard fileURL.isFileURL, let values = try? fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
      values.isRegularFile == true, let size = values.fileSize, size > 0, size <= 20 * 1024 * 1024 else {
      throw AppleSpeechError.invalidAudio
    }
    if !permissionDescriptionPresent { throw AppleSpeechError.permissionDescriptionMissing }
    if !backend.supportsOnDeviceRecognition { throw AppleSpeechError.onDeviceUnavailable }
    let operation = AppleSpeechOperation()
    current = operation
    defer { if current === operation { current = nil } }
    return try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { continuation in
        operation.continuation = continuation
        operation.timeout = Task { @MainActor [weak operation] in
          let duration = timeoutSeconds.isFinite ? max(0.01, min(600, timeoutSeconds)) : 120
          do { try await Task.sleep(for: .seconds(duration)) }
          catch { return }
          operation?.finish(.failure(AppleSpeechError.timedOut))
        }
        if Task.isCancelled { operation.finish(.failure(CancellationError())); return }
        if backend.authorizationStatus == .notDetermined {
          backend.authorize { [weak self, weak operation] status in
            Task { @MainActor in
              guard let self, let operation, !operation.finished else { return }
              self.begin(operation, fileURL: fileURL, authorization: status)
            }
          }
        } else { begin(operation, fileURL: fileURL, authorization: backend.authorizationStatus) }
      }
    } onCancel: {
      Task { @MainActor in operation.finish(.failure(CancellationError())) }
    }
  }

  private func begin(_ operation: AppleSpeechOperation, fileURL: URL,
    authorization: SFSpeechRecognizerAuthorizationStatus) {
    guard !operation.finished else { return }
    guard authorization == .authorized else {
      operation.finish(.failure(authorization == .restricted ? AppleSpeechError.restricted : AppleSpeechError.denied)); return
    }
    guard backend.supportsOnDeviceRecognition else {
      operation.finish(.failure(AppleSpeechError.onDeviceUnavailable)); return
    }
    guard backend.isAvailable else { operation.finish(.failure(AppleSpeechError.unavailable)); return }
    operation.handle = backend.recognize(fileURL: fileURL, requiresOnDeviceRecognition: true) { [weak operation] result in
      Task { @MainActor in
        operation?.finish(result.mapError { $0 as Error })
      }
    }
  }
}
