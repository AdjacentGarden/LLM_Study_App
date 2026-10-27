import Foundation
import Speech
import XCTest

@testable import BookCourse

@MainActor private final class SpeechTestHandle: AppleSpeechRecognitionHandle {
  var cancellations = 0
  func cancel() { cancellations += 1 }
}

@MainActor private final class SpeechTestBackend: AppleSpeechRecognitionBackend {
  var authorizationStatus: SFSpeechRecognizerAuthorizationStatus = .authorized
  var supportsOnDeviceRecognition = true
  var isAvailable = true
  var authorizations = 0
  var recognitions = 0
  var requiredOnDevice: Bool?
  var result: Result<String, AppleSpeechError>? = .success("化学反应的能量变化。")
  var authorization: (@Sendable (SFSpeechRecognizerAuthorizationStatus) -> Void)?
  var completion: (@Sendable (Result<String, AppleSpeechError>) -> Void)?
  let handle = SpeechTestHandle()
  func authorize(_ completion: @escaping @Sendable (SFSpeechRecognizerAuthorizationStatus) -> Void) {
    authorizations += 1
    authorization = completion
  }
  func recognize(fileURL: URL, requiresOnDeviceRecognition: Bool,
    completion: @escaping @Sendable (Result<String, AppleSpeechError>) -> Void) -> (any AppleSpeechRecognitionHandle)? {
    recognitions += 1
    requiredOnDevice = requiresOnDeviceRecognition
    self.completion = completion
    if let result { completion(result) }
    return handle
  }
}

/// Only injected callbacks and a local byte fixture are used; no recognizer,
/// system permission prompt, microphone, or speech model is started.
@MainActor final class AppleSpeechTranscriberTests: XCTestCase {
  private func audioFile() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("speech-test-\(UUID()).m4a")
    try Data(repeating: 1, count: 64).write(to: url)
    addTeardownBlock { try? FileManager.default.removeItem(at: url) }
    return url
  }
  func testRecognitionRequiresInstalledOnDeviceModel() async throws {
    let backend = SpeechTestBackend()
    let text = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
      .transcribe(fileURL: audioFile())
    XCTAssertEqual(text, "化学反应的能量变化。")
    XCTAssertEqual(backend.requiredOnDevice, true)
    XCTAssertEqual(backend.recognitions, 1)
    XCTAssertEqual(backend.authorizations, 0)
    XCTAssertEqual(backend.handle.cancellations, 1)
  }
  func testMissingAssetsFailsBeforeAuthorizationOrRecognition() async throws {
    let backend = SpeechTestBackend()
    backend.supportsOnDeviceRecognition = false
    backend.authorizationStatus = .notDetermined
    do {
      _ = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
        .transcribe(fileURL: audioFile())
      XCTFail("Missing local model must not fall back to cloud")
    } catch { XCTAssertEqual(error as? AppleSpeechError, .onDeviceUnavailable) }
    XCTAssertEqual(backend.authorizations, 0)
    XCTAssertEqual(backend.recognitions, 0)
  }
  func testMissingPrivacyDescriptionFailsWithoutSystemPrompt() async throws {
    let backend = SpeechTestBackend()
    backend.authorizationStatus = .notDetermined
    do {
      _ = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: false)
        .transcribe(fileURL: audioFile())
      XCTFail("Missing plist key must be handled before system authorization")
    } catch { XCTAssertEqual(error as? AppleSpeechError, .permissionDescriptionMissing) }
    XCTAssertEqual(backend.authorizations, 0)
  }
  func testDeniedAndRestrictedPermissionsNeverStartRecognition() async throws {
    for (status, expected) in [(SFSpeechRecognizerAuthorizationStatus.denied, AppleSpeechError.denied),
      (.restricted, .restricted)] {
      let backend = SpeechTestBackend()
      backend.authorizationStatus = status
      do {
        _ = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
          .transcribe(fileURL: audioFile())
        XCTFail("Forbidden permission accepted")
      } catch { XCTAssertEqual(error as? AppleSpeechError, expected) }
      XCTAssertEqual(backend.recognitions, 0)
    }
  }
  func testTimeoutCancelsRecognizerAndLateCallbackIsIgnored() async throws {
    let backend = SpeechTestBackend()
    backend.result = nil
    do {
      _ = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
        .transcribe(fileURL: audioFile(), timeoutSeconds: 0.01)
      XCTFail("Unfinished recognition must time out")
    } catch { XCTAssertEqual(error as? AppleSpeechError, .timedOut) }
    XCTAssertEqual(backend.handle.cancellations, 1)
    backend.completion?(.success("迟到的内容"))
    await Task.yield()
    XCTAssertEqual(backend.handle.cancellations, 1)
  }
  func testCancellationWhileAuthorizingCannotStartLateRecognition() async throws {
    let backend = SpeechTestBackend()
    backend.authorizationStatus = .notDetermined
    let recognizer = AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
    let file = try audioFile()
    let task = Task { try await recognizer.transcribe(fileURL: file) }
    for _ in 0..<100 where backend.authorizations == 0 { await Task.yield() }
    XCTAssertEqual(backend.authorizations, 1)
    task.cancel()
    do { _ = try await task.value; XCTFail("Cancelled transcription returned text") }
    catch { XCTAssertTrue(error is CancellationError) }
    backend.authorization?(.authorized)
    await Task.yield()
    XCTAssertEqual(backend.recognitions, 0)
  }
  func testCapabilityDoesNotRequestPermission() {
    let backend = SpeechTestBackend()
    backend.authorizationStatus = .notDetermined
    let capability = AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true).capability
    XCTAssertTrue(capability.available)
    XCTAssertTrue(capability.needsAuthorization)
    XCTAssertEqual(backend.authorizations, 0)
    XCTAssertEqual(backend.recognitions, 0)
  }
  func testNonLocalURLIsRejectedWithoutSpeechInvocation() async throws {
    let backend = SpeechTestBackend()
    do {
      _ = try await AppleSpeechTranscriber(backend: backend, permissionDescriptionPresent: true)
        .transcribe(fileURL: URL(string: "https://example.invalid/audio.m4a")!)
      XCTFail("Speech input must be a saved local file")
    } catch { XCTAssertEqual(error as? AppleSpeechError, .invalidAudio) }
    XCTAssertEqual(backend.recognitions, 0)
  }
  func testInstalledSystemSpeechCapabilityWithoutRequestingAuthorization() {
    let status = SFSpeechRecognizer.authorizationStatus()
    let system = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
    let capability = AppleSpeechTranscriber.capability()
    let hasDescription = !(Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") as? String ?? "").isEmpty
    let supported = system?.supportsOnDeviceRecognition == true
    let expected = hasDescription && status != .denied && status != .restricted
      && supported && system?.isAvailable == true
    print("APPLE_SPEECH_CAPABILITY supportsOnDevice=\(supported) available=\(capability.available) authorizationRaw=\(status.rawValue) needsAuthorization=\(capability.needsAuthorization) privacyDescription=\(hasDescription) notice=\(capability.message)")
    XCTAssertEqual(capability.supportsOnDeviceRecognition, supported)
    XCTAssertEqual(capability.needsAuthorization, status == .notDetermined)
    XCTAssertEqual(capability.available, expected)
    XCTAssertFalse(capability.message.isEmpty)
    XCTAssertEqual(SFSpeechRecognizer.authorizationStatus(), status)
  }
}
