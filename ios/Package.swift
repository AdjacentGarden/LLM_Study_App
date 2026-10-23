// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LearningCore",
    platforms: [.macOS(.v13), .iOS(.v18)],
    products: [.library(name: "LearningCore", targets: ["LearningCore"])],
    targets: [.target(name: "LearningCore"), .executableTarget(name: "CoreChecks", dependencies: ["LearningCore"], path: "Tests/LearningCoreTests"), .executableTarget(name: "APISmoke", dependencies: ["LearningCore"], path: "Tests/APISmoke")]
)
