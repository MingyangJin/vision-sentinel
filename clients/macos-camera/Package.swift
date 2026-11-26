// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VisionSentinelCamera",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .library(name: "VisionSentinelCameraCore", targets: ["VisionSentinelCameraCore"]),
        .executable(name: "VisionSentinelCamera", targets: ["VisionSentinelCameraApp"]),
    ],
    targets: [
        .target(
            name: "VisionSentinelCameraCore",
            path: "Sources/VisionSentinelCameraCore"
        ),
        .executableTarget(
            name: "VisionSentinelCameraApp",
            dependencies: ["VisionSentinelCameraCore"],
            path: "Sources/VisionSentinelCameraApp"
        ),
        .testTarget(
            name: "VisionSentinelCameraCoreTests",
            dependencies: ["VisionSentinelCameraCore"],
            path: "Tests/VisionSentinelCameraCoreTests"
        ),
    ]
)
