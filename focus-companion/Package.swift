// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "FocusCompanion",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        // Нулевых зависимостей — решение осознанное, см. docs/focus-companion/PHASE-1-PROMPT.md.
        .executableTarget(
            name: "FocusCompanion",
            path: "Sources/FocusCompanion"
        )
    ]
)
