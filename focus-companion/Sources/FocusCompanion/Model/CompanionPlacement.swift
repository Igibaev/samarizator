import AppKit

/// Где сейчас живёт корпус компаньона.
///
/// Два состояния, и они дают РАЗНУЮ форму:
/// - `.notch` — бесшовное правое продолжение выреза, левая сторона прямая;
/// - `.free` — самостоятельная капсула со скруглением всех углов, стоит там,
///   куда её перетащили.
///
/// Точка хранится в долях от рабочей области, а не в пунктах: при смене
/// разрешения или переезде на другой монитор компаньон иначе оказался бы
/// за краем экрана.
enum CompanionAnchor: Equatable {
    case notch
    case free(xFraction: CGFloat, yFraction: CGFloat)

    var isDocked: Bool { self == .notch }
}

/// Хранилище положения — своё на каждый экран.
///
/// Экран опознаётся по идентификатору дисплея: `localizedName` у двух
/// одинаковых мониторов совпадает, и они делили бы одну позицию.
enum CompanionPlacementStore {

    private static let defaults = UserDefaults.standard
    private static let prefix = "companion.placement."

    static func key(for screen: NSScreen) -> String {
        let number = screen.deviceDescription[
            NSDeviceDescriptionKey("NSScreenNumber")
        ] as? NSNumber
        let identifier = number.map(\.stringValue)
            ?? "\(screen.localizedName)-\(Int(screen.frame.width))x\(Int(screen.frame.height))"
        return prefix + identifier
    }

    static func anchor(for screen: NSScreen) -> CompanionAnchor {
        guard let stored = defaults.array(forKey: key(for: screen)) as? [Double],
              stored.count == 2 else {
            // По умолчанию — у выреза. На экране без выреза геометрия сама
            // отдаст самостоятельную капсулу под строкой меню.
            return .notch
        }
        return .free(xFraction: CGFloat(stored[0]), yFraction: CGFloat(stored[1]))
    }

    static func save(_ anchor: CompanionAnchor, for screen: NSScreen) {
        switch anchor {
        case .notch:
            defaults.removeObject(forKey: key(for: screen))
        case .free(let x, let y):
            defaults.set([Double(x), Double(y)], forKey: key(for: screen))
        }
    }
}
