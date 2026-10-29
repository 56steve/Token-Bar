// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "token-bar",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "token-bar", targets: ["TokenBarApp"]),
    ],
    targets: [
        .target(name: "TokenBarCore"),
        .executableTarget(name: "TokenBarApp", dependencies: ["TokenBarCore"]),
        .testTarget(name: "TokenBarCoreTests", dependencies: ["TokenBarCore"]),
    ]
)
