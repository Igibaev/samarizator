import CoreGraphics
import Foundation

/// Чистая арифметика посадки компаньона на экран — без AppKit и без SwiftUI.
///
/// Вынесена отдельно от `NSScreen` намеренно: это единственная часть
/// раскладки, которую можно проверить, не запуская macOS (см.
/// `Tools/check_sources.py` и фикстуры `CompanionGeometry.canonical…`).
/// Все координаты — в системе AppKit: начало в левом НИЖНЕМ углу, Y растёт вверх.
struct CompanionGeometry: Equatable {

    // MARK: - Константы раскладки (design.md §4.1)

    enum Metrics {
        /// Ширина правого крыла notch без задач и с задачами — одна и та же:
        /// крыло не двигается при раскрытии focus.
        static let wingWidth: CGFloat = 164
        /// Крыло, расширенное под текстовый таймер записи (design.md §6.2).
        static let wingWidthWithTimer: CGFloat = 212
        /// Поля внутри крыла слева и справа.
        static let wingInset: CGFloat = 12

        /// Ширина полки задач и её вертикальная арифметика:
        /// 8 pt суммарных отступов + 24 pt на активную задачу.
        static let shelfWidth: CGFloat = 288
        static let shelfVerticalPadding: CGFloat = 8
        static let shelfRowHeight: CGFloat = 24

        /// Раскрытая панель фокуса при стандартном размере текста:
        /// 40 заголовок + 3 × 56 строки + 12 промежуток + 40 подвал.
        static let focusWidth: CGFloat = 376
        static let focusHeight: CGFloat = 260

        /// Верх полки и раскрытой панели — на 4 pt ниже нижнего края notch.
        static let bodyTopGap: CGFloat = 4

        /// Отступы правой панели от краёв рабочей области и от компаньона.
        static let drawerInset: CGFloat = 12
        /// Ниже этой ширины «половина экрана» перестаёт быть пригодной,
        /// и включается адаптивный режим (design.md §4.3).
        static let drawerMinComfortableWidth: CGFloat = 520
        /// Ниже этой ширины список записей сворачивается в кнопку «Все записи».
        static let drawerNarrowThreshold: CGFloat = 680
        /// Режим «Рядом» требует столько pt в области чтения.
        static let sideBySideMinimumWidth: CGFloat = 640
        /// Ширина колонки списка записей и промежуток до содержимого.
        static let recordingsListWidth: CGFloat = 180
        static let recordingsListGap: CGFloat = 16
        /// Внутренние поля правой панели.
        static let drawerPadding: CGFloat = 20

        /// Самостоятельная капсула на экране без выреза.
        static let standaloneWidth: CGFloat = 164

        /// Полоска короткого сообщения под полкой: «Задача сгорела · Вернуть»
        /// и подпись напоминания. Ширина — как у полки, чтобы не ломать ось.
        static let noticeHeight: CGFloat = 28
    }

    // MARK: - Вход

    /// Полный прямоугольник экрана.
    let screenFrame: CGRect
    /// Область без меню-бара и Dock.
    let visibleFrame: CGRect
    /// Физический вырез камеры; `nil` — экран без выреза.
    let notchRect: CGRect?
    /// Высота полосы меню-бара (нужна для экрана без выреза).
    let menuBarHeight: CGFloat
    /// Сколько активных задач висит под notch (0…3).
    let activeTaskCount: Int
    /// Раскрыта ли панель фокуса — от этого зависит нижняя граница компаньона.
    let isFocusOpen: Bool
    /// Идёт ли запись: при записи крыло может вырасти под метку времени.
    let isRecording: Bool
    /// Где стоит корпус: у выреза или там, куда его перетащили.
    let anchor: CompanionAnchor
    /// Показывается ли сейчас короткое сообщение под полкой.
    /// От этого зависит и нижняя граница компаньона, и область кликов:
    /// у сообщения есть кнопка «Вернуть», а значит оно обязано принимать нажатия.
    let hasCompactNotice: Bool

    init(
        screenFrame: CGRect,
        visibleFrame: CGRect,
        notchRect: CGRect?,
        menuBarHeight: CGFloat,
        activeTaskCount: Int = 0,
        isFocusOpen: Bool = false,
        isRecording: Bool = false,
        hasCompactNotice: Bool = false,
        anchor: CompanionAnchor = .notch
    ) {
        self.screenFrame = screenFrame
        self.visibleFrame = visibleFrame
        self.notchRect = notchRect
        self.menuBarHeight = menuBarHeight
        self.activeTaskCount = max(0, min(3, activeTaskCount))
        self.isFocusOpen = isFocusOpen
        self.isRecording = isRecording
        self.hasCompactNotice = hasCompactNotice
        self.anchor = anchor
    }

    // MARK: - Корпус

    var hasNotch: Bool { notchRect != nil }

    /// Ширина крыла с учётом расширения под таймер записи.
    ///
    /// Расширение применяется только если справа реально есть свободные
    /// пиксели: перекрывать системные индикаторы меню-бара нельзя
    /// (design.md §4.2).
    var wingWidth: CGFloat {
        guard hasNotch else { return Metrics.standaloneWidth }
        guard isRecording else { return Metrics.wingWidth }
        let available = screenFrame.maxX - (notchRect?.maxX ?? screenFrame.midX)
        return available >= Metrics.wingWidthWithTimer
            ? Metrics.wingWidthWithTimer
            : Metrics.wingWidth
    }

    /// Хватает ли справа от выреза места под бесшовное крыло.
    /// Если нет — включается fallback с самостоятельной капсулой под меню-баром.
    var canAttachSeamlessWing: Bool {
        guard let notch = notchRect else { return false }
        return screenFrame.maxX - notch.maxX >= Metrics.wingWidth
    }

    /// Корпус с глазами и двумя кнопками.
    ///
    /// С вырезом — бесшовное правое продолжение: `wingLeft = notchRight`,
    /// та же высота и та же верхняя линия. Без выреза (или когда справа не
    /// хватает места) — самостоятельная капсула по центру под меню-баром.
    var wingRect: CGRect {
        if case .free(let xFraction, let yFraction) = anchor {
            return freeCapsuleRect(xFraction: xFraction, yFraction: yFraction)
        }
        if let notch = notchRect, canAttachSeamlessWing {
            return CGRect(x: notch.maxX, y: notch.minY, width: wingWidth, height: notch.height)
        }
        // Оба случая — и экран без выреза, и вырез без места справа — дают
        // одну и ту же самостоятельную капсулу СРАЗУ ПОД строкой меню:
        // поддельную камеру не рисуем, системные индикаторы не перекрываем.
        let height = notchRect?.height ?? menuBarHeight
        return CGRect(
            x: screenFrame.midX - Metrics.standaloneWidth / 2,
            y: screenFrame.maxY - menuBarHeight - height,
            width: Metrics.standaloneWidth,
            height: height
        )
    }

    /// Высота свободной капсулы: та же, что у пристыкованного крыла, чтобы
    /// персонаж не менял рост при перетаскивании.
    var capsuleHeight: CGFloat { notchRect?.height ?? menuBarHeight }

    /// Капсула в свободном положении, целиком внутри рабочей области.
    ///
    /// Доли считаются от `visibleFrame`, а не от `frame`: иначе капсулу можно
    /// было бы утащить под строку меню или под Dock и там потерять.
    private func freeCapsuleRect(xFraction: CGFloat, yFraction: CGFloat) -> CGRect {
        let width = wingWidth
        let height = capsuleHeight
        let minX = visibleFrame.minX
        let maxX = visibleFrame.maxX - width
        let minY = visibleFrame.minY
        let maxY = visibleFrame.maxY - height
        let x = minX + (maxX - minX) * min(max(xFraction, 0), 1)
        let y = minY + (maxY - minY) * min(max(yFraction, 0), 1)
        return CGRect(x: x, y: y, width: width, height: height)
    }

    /// Обратный перевод: прямоугольник капсулы в доли рабочей области.
    /// Нужен при перетаскивании — положение хранится долями.
    func fractions(forCapsuleOrigin origin: CGPoint) -> (x: CGFloat, y: CGFloat) {
        let width = wingWidth
        let height = capsuleHeight
        let spanX = max(1, visibleFrame.width - width)
        let spanY = max(1, visibleFrame.height - height)
        return (
            x: min(max((origin.x - visibleFrame.minX) / spanX, 0), 1),
            y: min(max((origin.y - visibleFrame.minY) / spanY, 0), 1)
        )
    }

    /// Крыло рисуется бесшовно (прямая левая сторона, скругления только справа)
    /// только когда физический вырез реально присутствует.
    var wingIsSeamless: Bool { anchor.isDocked && hasNotch && canAttachSeamlessWing }

    /// Ось, по которой центрируются задачи и focus.
    /// С вырезом — центр выреза, иначе — центр самостоятельной капсулы.
    var bodyCenterX: CGFloat {
        if wingIsSeamless, let notch = notchRect { return notch.midX }
        return wingRect.midX
    }

    /// Верхняя граница полки и раскрытой панели.
    var bodyTop: CGFloat {
        let anchorBottom = wingIsSeamless ? (notchRect?.minY ?? wingRect.minY) : wingRect.minY
        return anchorBottom - Metrics.bodyTopGap
    }

    /// Хватает ли места, чтобы раскрыться ВНИЗ.
    ///
    /// Капсулу можно утащить к нижнему краю экрана, и тогда полка и панель
    /// фокуса ушли бы за границу. В этом случае они растут ВВЕРХ от капсулы.
    var growsUpward: Bool {
        guard !wingIsSeamless else { return false }
        return bodyTop - Metrics.focusHeight < visibleFrame.minY + Metrics.drawerInset
    }

    /// Нижняя граница поверхности, растущей вниз, либо верхняя — растущей вверх.
    private func surfaceRect(width: CGFloat, height: CGFloat, stackedBelow offset: CGFloat) -> CGRect {
        let x = bodyCenterX - width / 2
        if growsUpward {
            let bottom = wingRect.maxY + Metrics.bodyTopGap + offset
            return CGRect(x: x, y: bottom, width: width, height: height)
        }
        return CGRect(x: x, y: bodyTop - offset - height, width: width, height: height)
    }

    // MARK: - Полка задач и панель фокуса

    var shelfHeight: CGFloat {
        guard activeTaskCount > 0 else { return 0 }
        return Metrics.shelfVerticalPadding + Metrics.shelfRowHeight * CGFloat(activeTaskCount)
    }

    /// Компактная полка задач. `nil` при нуле задач — полки просто нет.
    var shelfRect: CGRect? {
        guard activeTaskCount > 0 else { return nil }
        return surfaceRect(width: Metrics.shelfWidth, height: shelfHeight, stackedBelow: 0)
    }

    /// Полоска сообщения под полкой. `nil` — сообщения нет.
    ///
    /// Нужна потому, что и подпись напоминания, и «Задача сгорела · Вернуть»
    /// обязаны быть видны в КОМПАКТНОМ виде: панели не открываются сами
    /// (design.md §11.3), а окно возврата живёт всего 8 секунд.
    var compactNoticeRect: CGRect? {
        guard hasCompactNotice, !isFocusOpen else { return nil }
        return surfaceRect(
            width: Metrics.shelfWidth,
            height: Metrics.noticeHeight,
            stackedBelow: shelfHeight + Metrics.bodyTopGap
        )
    }

    /// Раскрытая панель фокуса.
    var focusRect: CGRect {
        surfaceRect(width: Metrics.focusWidth, height: Metrics.focusHeight, stackedBelow: 0)
    }

    /// Самая нижняя видимая поверхность компаньона — от неё отсчитывается drawer.
    var companionBottom: CGFloat {
        if isFocusOpen { return min(wingRect.minY, focusRect.minY) }
        var bottom = wingRect.minY
        if let shelf = shelfRect { bottom = min(bottom, shelf.minY) }
        if let notice = compactNoticeRect { bottom = min(bottom, notice.minY) }
        return bottom
    }

    /// Прямоугольник окна верхней панели: объединение крыла и всего,
    /// что может раскрыться под notch. Окно одно — так стык крыла и полки
    /// не может разъехаться между двумя независимо позиционируемыми окнами.
    var topWindowRect: CGRect {
        var union = wingRect.union(focusRect)
        if let shelf = shelfRect { union = union.union(shelf) }
        if let notice = compactNoticeRect { union = union.union(notice) }
        return union
    }

    // MARK: - Правая панель (design.md §4.2, §4.3)

    /// Ширина правой панели: ровно половина доступной ширины, а на узком
    /// экране — осознанное исключение `min(520, W − 24)`.
    var drawerWidth: CGFloat {
        let half = visibleFrame.width / 2
        if half >= Metrics.drawerMinComfortableWidth { return half }
        return min(Metrics.drawerMinComfortableWidth, visibleFrame.width - 2 * Metrics.drawerInset)
    }

    /// Правая панель начинается ниже обеих видимых поверхностей компаньона,
    /// чтобы не перекрывать ни глаза, ни висящие задачи.
    var drawerRect: CGRect {
        let width = drawerWidth
        let x = visibleFrame.maxX - Metrics.drawerInset - width
        let topInset = drawerTopInset(drawerLeftEdge: x)
        let height = max(0, visibleFrame.height - topInset - Metrics.drawerInset)
        return CGRect(
            x: x,
            y: visibleFrame.maxY - topInset - height,
            width: width,
            height: height
        )
    }

    /// Насколько ниже верхней границы рабочей области начинается правая панель.
    ///
    /// Смысл правила из design.md §4.2 — не перекрыть глаза и висящие задачи.
    /// Пока компаньон стоит у выреза, он всегда над панелью, и отступ считается
    /// от его нижней границы. Но перетащенная капсула может оказаться слева,
    /// где панели она вообще не мешает: опускать панель из-за неё значило бы
    /// терять высоту ни за что. Поэтому отступ считается только при реальном
    /// пересечении по горизонтали.
    private func drawerTopInset(drawerLeftEdge: CGFloat) -> CGFloat {
        let companionSpan = companionHorizontalSpan
        let overlapsHorizontally = companionSpan.maxX > drawerLeftEdge
            && companionSpan.minX < visibleFrame.maxX
        guard overlapsHorizontally else { return Metrics.drawerInset }
        let companionDepth = max(0, visibleFrame.maxY - companionBottom)
        return max(Metrics.drawerInset, companionDepth + Metrics.drawerInset)
    }

    /// Горизонтальные границы всего, что компаньон сейчас рисует.
    private var companionHorizontalSpan: (minX: CGFloat, maxX: CGFloat) {
        var rect = wingRect
        if isFocusOpen {
            rect = rect.union(focusRect)
        } else {
            if let shelf = shelfRect { rect = rect.union(shelf) }
            if let notice = compactNoticeRect { rect = rect.union(notice) }
        }
        return (rect.minX, rect.maxX)
    }

    /// Ширина области чтения внутри правой панели (без колонки списка).
    var drawerReadingWidth: CGFloat {
        let inner = drawerWidth - 2 * Metrics.drawerPadding
        guard showsRecordingsList else { return inner }
        return inner - Metrics.recordingsListWidth - Metrics.recordingsListGap
    }

    /// На узкой панели список записей заменяется кнопкой «Все записи».
    var showsRecordingsList: Bool { drawerWidth >= Metrics.drawerNarrowThreshold }

    /// Режим «Рядом» доступен, только если области чтения хватает ширины.
    var allowsSideBySide: Bool { drawerReadingWidth >= Metrics.sideBySideMinimumWidth }

    // MARK: - Ховер-область (design.md §7.2)

    /// Объединение корпуса, полки/панели и короткого коридора между ними.
    ///
    /// Коридор нужен для диагонального перехода «глаза → строки задач»:
    /// без него курсор на миг оказывается вне обеих поверхностей и запускает
    /// таймер закрытия. Коридор участвует только в отслеживании ховера и
    /// никогда не перехватывает клики.
    var hoverUnionRect: CGRect {
        var union = wingRect
        if isFocusOpen {
            union = union.union(focusRect)
        } else {
            if let shelf = shelfRect { union = union.union(shelf) }
            if let notice = compactNoticeRect { union = union.union(notice) }
        }
        // Коридор: прямоугольник от нижней границы крыла до верха раскрытой
        // поверхности, по горизонтали — от левого края этой поверхности до
        // правого края крыла.
        let lower = isFocusOpen ? focusRect : (shelfRect ?? wingRect)
        let corridor = CGRect(
            x: min(lower.minX, wingRect.minX),
            y: lower.maxY,
            width: max(lower.maxX, wingRect.maxX) - min(lower.minX, wingRect.minX),
            height: max(0, wingRect.minY - lower.maxY)
        )
        return union.union(corridor)
    }

    // MARK: - Локальные координаты внутри окна верхней панели

    /// Перевод прямоугольника экрана в координаты SwiftUI внутри окна панели
    /// (начало в левом ВЕРХНЕМ углу окна, Y растёт вниз).
    func local(_ rect: CGRect, in window: CGRect) -> CGRect {
        CGRect(
            x: rect.minX - window.minX,
            y: window.maxY - rect.maxY,
            width: rect.width,
            height: rect.height
        )
    }
}
