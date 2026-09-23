import AppKit
import SwiftUI

/// Владелец обоих окон компаньона и точка сборки всех контроллеров.
@MainActor
final class NotchWindowController {

    let stateMachine = CompanionStateMachine()
    let taskPanel: TaskPanelController
    let navigation = NavigationController()
    let recordings = RecordingsController()
    let clipboard = ClipboardService()
    let journal = JournalSync()
    let eyes: EyesViewModel
    let liveliness: LivelinessController

    private let hoverDetector = HoverDetector()
    private var topPanel: NotchPanel?
    private var drawerPanel: DrawerPanel?
    private var currentScreen: NSScreen?
    private var topHostingView: GestureHostingView<CompanionRootView>?
    private var drawerHostingView: GestureHostingView<DrawerRootView>?

    init() {
        let store = TaskStore()
        let machine = stateMachine
        let panel = TaskPanelController(store: store, stateMachine: machine, inbox: HandoffInbox())
        let eyesModel = EyesViewModel(stateMachine: machine)
        self.taskPanel = panel
        self.eyes = eyesModel
        self.liveliness = LivelinessController(stateMachine: machine, eyes: eyesModel, taskPanel: panel)

        if CompanionSettings.demoMode {
            clipboard.loadDemoItems(DemoFixtures.clipboardItems)
        }

        wireHoverDetector()
        wireNavigation()
        wireRecordings()
        wireHandoff()
        wireJournal()
        wireLiveliness()
    }

    // MARK: - Связывание

    private func wireHoverDetector() {
        hoverDetector.currentStateProvider = { [weak self] in
            guard let self else {
                return (taskCount: 0, isFocusOpen: false, isRecording: false, hasNotice: false)
            }
            return (
                taskCount: self.taskPanel.store.activeTasks.count,
                isFocusOpen: self.navigation.isFocusOpen,
                isRecording: self.recordings.captureState.isRecording,
                hasNotice: self.taskPanel.hasCompactNotice
            )
        }
        hoverDetector.onOpenFocus = { [weak self] in
            guard let self else { return }
            // Случайное наведение на глаза не имеет права переключить
            // пользователя с записей обратно на фокус.
            guard !self.navigation.isDrawerOpen else { return }
            withAnimation(DesignTokens.Motion.respectful(
                DesignTokens.Motion.focusExpand,
                reduceMotion: self.stateMachine.reduceMotion
            )) {
                self.navigation.openFocusHover()
            }
        }
        hoverDetector.onCloseFocus = { [weak self] in
            guard let self else { return }
            withAnimation(DesignTokens.Motion.respectful(
                DesignTokens.Motion.focusCollapse,
                reduceMotion: self.stateMachine.reduceMotion
            )) {
                self.navigation.closeFocusHover()
            }
        }
        hoverDetector.onMouseLocation = { [weak self] location in
            guard let self, let geometry = self.hoverDetector.geometry else { return }
            self.eyes.updateGaze(mouse: location, wing: geometry.wingRect)
        }
        hoverDetector.onWantsMouseEventsChange = { [weak self] wants in
            guard let self else { return }
            self.topPanel?.ignoresMouseEvents = !wants
            let allowsKey = wants && self.navigation.isFocusOpen
            self.topPanel?.allowsKeyWhenExpanded = allowsKey
            if !allowsKey {
                self.topPanel?.resignKey()
                self.topPanel?.makeFirstResponder(nil)
            }
        }
    }

    private func wireRecordings() {
        recordings.onActivityChange = { [weak self] in
            guard let self else { return }
            // Ошибка не должна молча раствориться в фоновом состоянии.
            if case .error = self.recordings.captureState {
                self.stateMachine.react(.error)
            }
            if case .error = self.recordings.summaryState {
                self.stateMachine.react(.error)
            }
            self.updateAmbient()
            // Ширина крыла зависит от того, идёт ли запись.
            self.reposition()
        }
    }

    private func wireHandoff() {
        // «Открыть» на полоске «принёс дела»: страница «Записи» с этой
        // встречей. Порядок важен: `show(page:)` через `onPresentationChange`
        // перечитывает базу (`syncDrawer` → `recordings.reload()`), выбор
        // записи — после этого.
        taskPanel.onOpenHandoff = { [weak self] meetingID in
            guard let self else { return }
            self.navigation.show(page: .recordings)
            self.recordings.select(meetingID)
        }
    }

    /// Каждое сохранение слотов — повод обновить заметку дня и «Поручения».
    private func wireJournal() {
        journal.toolchain = { [weak self] in self?.recordings.toolchain }
        taskPanel.store.onChange = { [weak self] in
            self?.journal.schedule()
        }
        journal.start()
    }

    /// Сон, пробуждение и реплики утром и вечером (переключатель в меню).
    private func wireLiveliness() {
        liveliness.toolchain = { [weak self] in self?.recordings.toolchain }
        liveliness.onRestingChange = { [weak self] in
            self?.updateAmbient()
        }
        liveliness.start()
    }

    /// Фон персонажа: запись и обработка важнее покоя, а покой — это
    /// обычный, сонный или спящий вид от `LivelinessController`.
    func updateAmbient() {
        let busy = recordings.ambientEmotion
        stateMachine.setAmbient(busy == .idle ? liveliness.resting : busy)
    }

    private func wireNavigation() {
        navigation.onPresentationChange = { [weak self] presentation in
            guard let self else { return }
            self.syncDrawer(for: presentation)
            self.reposition()
        }
    }

    // MARK: - Верхнее окно

    var isPanelVisible: Bool { topPanel?.isVisible ?? false }
    var panelIgnoresMouseEvents: Bool? { topPanel?.ignoresMouseEvents }

    func show(on screen: NSScreen? = nil) {
        let targetScreen = screen ?? NSScreen.screenWithMouse ?? NSScreen.main
        guard let targetScreen else { return }

        topPanel?.close()

        let geometry = currentGeometry(for: targetScreen)
        let panel = NotchPanel(contentRect: geometry.topWindowRect)
        let hosting = GestureHostingView(rootView: CompanionRootView(
            stateMachine: stateMachine,
            hoverDetector: hoverDetector,
            taskPanel: taskPanel,
            navigation: navigation,
            recordings: recordings,
            eyes: eyes
        ))
        // Жест считается «внутри компаньона», если начался в крыле, на полке
        // или в раскрытой панели фокуса — не где угодно по экрану.
        hosting.isGestureAllowed = { [weak self] point in
            guard let self, let geometry = self.hoverDetector.geometry else { return false }
            if geometry.wingRect.contains(point) { return true }
            if self.navigation.isFocusOpen, geometry.focusRect.contains(point) { return true }
            if let shelf = geometry.shelfRect, shelf.contains(point) { return true }
            return false
        }
        hosting.swipeRecognizer.onSwipe = { [weak self] direction in
            self?.handleSwipe(direction)
        }
        panel.contentView = hosting
        panel.ignoresMouseEvents = !hoverDetector.wantsMouseEvents
        panel.orderFrontRegardless()

        topPanel = panel
        topHostingView = hosting
        currentScreen = targetScreen
    }

    private func currentGeometry(for screen: NSScreen) -> CompanionGeometry {
        screen.companionGeometry(
            activeTaskCount: taskPanel.store.activeTasks.count,
            isFocusOpen: navigation.isFocusOpen,
            isRecording: recordings.captureState.isRecording,
            hasCompactNotice: taskPanel.hasCompactNotice
        )
    }

    func reposition() {
        let targetScreen = NSScreen.screenWithMouse ?? currentScreen ?? NSScreen.main
        guard let targetScreen else { return }
        if targetScreen !== currentScreen || topPanel == nil {
            show(on: targetScreen)
            return
        }
        let geometry = currentGeometry(for: targetScreen)
        // Окно верхней панели всегда в максимальном размере: анимировать
        // `setFrame` синхронно со SwiftUI-анимацией — источник рывков.
        topPanel?.setFrame(geometry.topWindowRect, display: true)
        if let drawerPanel, drawerPanel.isVisible {
            drawerPanel.setFrame(geometry.drawerRect, display: true)
        }
    }

    func moveToScreen(_ screen: NSScreen) {
        show(on: screen)
        syncDrawer(for: navigation.presentation)
    }

    // MARK: - Правая панель

    private func syncDrawer(for presentation: PanelPresentation) {
        guard presentation.isDrawerOpen else {
            drawerPanel?.stopWatchingOutsideClicks()
            drawerPanel?.orderOut(nil)
            return
        }
        guard let screen = currentScreen ?? NSScreen.screenWithMouse ?? NSScreen.main else { return }
        let geometry = currentGeometry(for: screen)

        if drawerPanel == nil {
            let panel = DrawerPanel(contentRect: geometry.drawerRect)
            panel.onOutsideClick = { [weak self] in
                guard let self, !self.navigation.drawerPinned else { return }
                self.navigation.closeDrawer()
            }
            // Оболочка создаётся ОДИН раз: пересоздание `NSHostingView` при
            // каждом переключении страницы сбрасывало бы поиск, выделение и
            // позицию чтения — а их требуется сохранять (design.md §9.1).
            let hosting = GestureHostingView(rootView: DrawerRootView(
                navigation: navigation,
                clipboard: clipboard,
                recordings: recordings,
                taskPanel: taskPanel,
                geometry: geometry
            ))
            hosting.isGestureAllowed = { [weak panel] point in
                guard let panel else { return false }
                // В drawer жест распознаётся только в навигационной шапке:
                // списки и выделяемый текст сохраняют свои обычные жесты.
                let header = NSRect(
                    x: panel.frame.minX,
                    y: panel.frame.maxY - 48 - CompanionGeometry.Metrics.drawerPadding,
                    width: panel.frame.width,
                    height: 48 + CompanionGeometry.Metrics.drawerPadding
                )
                return header.contains(point)
            }
            hosting.swipeRecognizer.onSwipe = { [weak self] direction in
                self?.handleSwipe(direction)
            }
            panel.contentView = hosting
            drawerHostingView = hosting
            drawerPanel = panel
        }
        guard let drawerPanel else { return }

        drawerPanel.setFrame(geometry.drawerRect, display: true)
        drawerPanel.orderFrontRegardless()
        if navigation.drawerPinned {
            drawerPanel.stopWatchingOutsideClicks()
        } else {
            drawerPanel.startWatchingOutsideClicks()
        }
        // Демонстрационный набор не затираем повторной загрузкой из базы.
        if navigation.page == .recordings, !recordings.isDemo {
            recordings.reload()
        }
    }

    // MARK: - Жесты

    private func handleSwipe(_ direction: SwipeDirection) {
        withAnimation(DesignTokens.Motion.respectful(
            DesignTokens.Motion.pageChange,
            reduceMotion: stateMachine.reduceMotion
        )) {
            navigation.advance(direction)
        }
    }

    // MARK: - Фикстуры и демонстрация

    func applyFixture(_ fixture: CompanionFixture) {
        taskPanel.loadFixture(fixture)
        switch fixture {
        case .recordingInProgress:
            recordings.loadDemo()
            recordings.performRecordAction()
            stateMachine.setAmbient(.listening)
        case .summaryGenerating:
            recordings.loadDemo()
            recordings.requestSummary()
            stateMachine.setAmbient(.thinking)
        case .processingError:
            recordings.loadDemo()
            recordings.select("demo-voice")
            stateMachine.react(.error)
        default:
            stateMachine.setAmbient(.idle)
        }
        reposition()
    }

    func leaveFixture() {
        taskPanel.leaveFixture()
        recordings.reload()
        stateMachine.setAmbient(.idle)
        reposition()
    }
}
