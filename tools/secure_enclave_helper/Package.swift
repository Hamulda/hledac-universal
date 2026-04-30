// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "secure-enclave-helper",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "secure-enclave-helper",
            targets: ["App"]
        )
    ],
    dependencies: [],
    targets: [
        .executableTarget(
            name: "App",
            dependencies: [],
            path: "Sources",
            sources: ["main.swift", "Commands.swift", "EnclaveSigner.swift"]
        )
    ]
)
