// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VisionSentinelCore",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(
            name: "VisionSentinelCore",
            targets: ["VisionSentinelCore"]
        ),
    ],
    targets: [
        .target(
            name: "VisionSentinelCore",
            path: "Sources/VisionSentinelCore"
        ),
        .testTarget(
            name: "VisionSentinelCoreTests",
            dependencies: ["VisionSentinelCore"],
            path: "Tests/VisionSentinelCoreTests"
        ),
    ]
)
