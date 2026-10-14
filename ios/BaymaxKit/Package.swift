// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BaymaxKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "BaymaxKit", targets: ["BaymaxKit"])],
    targets: [
        .target(name: "BaymaxKit"),
        .testTarget(name: "BaymaxKitTests", dependencies: ["BaymaxKit"]),
    ]
)
