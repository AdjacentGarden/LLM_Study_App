import Foundation
import XCTest

@testable import BookCourse

final class CloudRelayConfigurationTests: XCTestCase {
  private func isolatedDefaults() -> UserDefaults {
    UserDefaults(suiteName: "relay-test-" + UUID().uuidString)!
  }

  func testSimulatorMigratesDirectWindowsAddressAndKeepsRelayAfterRestart() throws {
    for old in ["http://100.86.143.88:8100/", "http://10.249.44.137:8100", "http://192.168.28.241:8100"] {
      let defaults = isolatedDefaults()
      defaults.set(old, forKey: "cloudpath.server")
      let first = try XCTUnwrap(CloudAPI.configuredURL(defaults: defaults, environment: [:], simulatorRelay: true))
      XCTAssertEqual(first.absoluteString, "http://127.0.0.1:18100")
      XCTAssertEqual(defaults.string(forKey: "cloudpath.server"), first.absoluteString)
      XCTAssertEqual(CloudAPI.configuredURL(defaults: defaults, environment: [:], simulatorRelay: true), first)
    }
  }

  func testFreshSimulatorPersistsRelayWithoutLaunchEnvironment() {
    let defaults = isolatedDefaults()
    XCTAssertEqual(CloudAPI.configuredURL(defaults: defaults, environment: [:], simulatorRelay: true)?.absoluteString,
      "http://127.0.0.1:18100")
    XCTAssertEqual(defaults.string(forKey: "cloudpath.server"), "http://127.0.0.1:18100")
  }

  func testExplicitTestAddressDoesNotOverwriteSavedService() {
    let defaults = isolatedDefaults()
    defaults.set("https://learning.example", forKey: "cloudpath.server")
    XCTAssertEqual(CloudAPI.configuredURL(defaults: defaults,
      environment: ["BOOKCOURSE_API_BASE_URL": "http://fixture.invalid"], simulatorRelay: true)?.absoluteString,
      "http://fixture.invalid")
    XCTAssertEqual(defaults.string(forKey: "cloudpath.server"), "https://learning.example")
  }

  func testCustomServiceAndPhysicalDeviceConfigurationAreRetained() {
    let defaults = isolatedDefaults()
    defaults.set("https://learning.example", forKey: "cloudpath.server")
    XCTAssertEqual(CloudAPI.configuredURL(defaults: defaults, environment: [:], simulatorRelay: true)?.absoluteString,
      "https://learning.example")
    defaults.set("http://100.86.143.88:8100", forKey: "cloudpath.server")
    XCTAssertEqual(CloudAPI.configuredURL(defaults: defaults, environment: [:], simulatorRelay: false)?.absoluteString,
      "http://100.86.143.88:8100")
  }

  func testLoopbackRelayBypassesSystemHTTPProxy() throws {
    let url = try XCTUnwrap(URL(string: "http://127.0.0.1:18100"))
    let config = CloudAPI.serviceSessionConfiguration(for: url)
    XCTAssertEqual(config.connectionProxyDictionary?.count, 0)
  }
}
