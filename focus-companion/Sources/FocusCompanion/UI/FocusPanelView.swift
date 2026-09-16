import SwiftUI

/// Раскрытая панель фокуса, 376 × 260 pt (design.md §7.1).
///
/// Сетка: блок заголовка 40 + три строки по 56 + промежуток 12 + подвал 40.
/// Глаза и обе кнопки остаются СНАРУЖИ, в неподвижном правом крыле: панель
/// начинается сразу с заголовка, второго лица в ней нет.
struct FocusPanelView: View {
    var taskPanel: TaskPanelController
    var navigation: NavigationController
    var reduceMotion: Bool
    var reduceTransparency: Bool
    /// Ввод, drag или открытое меню удерживают панель открытой.
    var onHoldChange: (Bool) -> Void

    @State private var draftTitle = ""
    @State private var draftMinutes = CharacterConfig.defaultDeadlineMinutes
    @State private var editingSlot = false
    @State private var showsHistory = false
    @FocusState private var inputFocused: Bool

    private var tasks: [CompanionTask] { taskPanel.store.activeTasks }

    var body: some View {
        VStack(spacing: 0) {
            header
            slots
            Spacer(minLength: DesignTokens.Spacing.s)
            footer
        }
        .frame(
            width: CompanionGeometry.Metrics.focusWidth,
            height: CompanionGeometry.Metrics.focusHeight,
            alignment: .top
        )
        .onChange(of: inputFocused) { _, focused in
            onHoldChange(focused || showsHistory || taskPanel.pendingRestoreID != nil)
        }
        .onChange(of: showsHistory) { _, value in
            onHoldChange(value || inputFocused || taskPanel.pendingRestoreID != nil)
        }
        .background { escapeHandler }
        .sheetlessHistory(isPresented: $showsHistory) {
            HistoryView(taskPanel: taskPanel, reduceTransparency: reduceTransparency) {
                showsHistory = false
            }
        }
    }

    /// Escape: сначала закрывается редактор или история, и только потом сама
    /// панель. Срабатывает, когда панель стала key-окном, то есть после
    /// явного клика внутрь неё — без этого клавиатура принадлежит активному
    /// приложению, и перехватывать её у него компаньон не должен.
    private var escapeHandler: some View {
        Button("") {
            if showsHistory {
                showsHistory = false
            } else if editingSlot {
                editingSlot = false
                inputFocused = false
                draftTitle = ""
            } else {
                navigation.collapse()
            }
        }
        .keyboardShortcut(.escape, modifiers: [])
        .opacity(0)
        .frame(width: 0, height: 0)
        .accessibilityHidden(true)
    }

    // MARK: - Заголовок

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Сегодня в фокусе")
                .font(DesignTokens.Typography.panelTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
            Spacer()
            Text("\(tasks.count) / \(TaskStore.maxActiveSlots)")
                .font(DesignTokens.Typography.meta())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
            Menu {
                Button("История") { showsHistory = true }
                Divider()
                Picker("Характер", selection: Binding(
                    get: { taskPanel.mood },
                    set: { taskPanel.mood = $0 }
                )) {
                    ForEach(CharacterMood.allCases) { Text($0.displayName).tag($0) }
                }
                Picker("Утилизация", selection: Binding(
                    get: { taskPanel.preferredDisposal },
                    set: { taskPanel.preferredDisposal = $0 }
                )) {
                    ForEach(DisposalEffect.allCases) { Text($0.displayName).tag($0) }
                }
                Divider()
                Button("Не напоминать 30 минут") { taskPanel.snoozeReminders() }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Меню фокуса")
        }
        .padding(.horizontal, DesignTokens.Spacing.m)
        .frame(height: 40)
    }

    // MARK: - Три слота

    private var slots: some View {
        VStack(spacing: 0) {
            ForEach(0..<TaskStore.maxActiveSlots, id: \.self) { index in
                if index < tasks.count {
                    slotRow(tasks[index], index: index)
                } else {
                    emptySlot(isFirstEmpty: index == tasks.count)
                }
            }
        }
    }

    private func slotRow(_ task: CompanionTask, index: Int) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
            HStack(spacing: DesignTokens.Spacing.xs) {
                CompletionRing(isDone: false) { taskPanel.complete(task) }
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.title)
                        .font(DesignTokens.Typography.taskTitle())
                        .foregroundStyle(DesignTokens.Palette.textPrimary)
                        .lineLimit(2)
                    if !task.note.isEmpty {
                        Text(task.note)
                            .font(DesignTokens.Typography.caption())
                            .foregroundStyle(DesignTokens.Palette.textTertiary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: DesignTokens.Spacing.xs)
                // Время не дублируется огромными цифрами: только компактная метка.
                Text(task.remainingLabel(now: taskPanel.now))
                    .font(DesignTokens.Typography.meta())
                    .foregroundStyle(color(for: task))
                    .fixedSize()
            }
            deadlineLine(task)
        }
        .padding(.horizontal, DesignTokens.Spacing.m)
        .frame(height: 56)
        .background(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                .fill(Color.clear)
        )
        .contextMenu {
            Menu("Перенести срок") {
                ForEach(CharacterConfig.deadlinePresets, id: \.self) { minutes in
                    Button("На \(minutes) минут") { taskPanel.reschedule(task, byMinutes: minutes) }
                }
            }
            if index > 0 {
                Button("Выше") { taskPanel.move(task, to: index - 1) }
            }
            if index < tasks.count - 1 {
                Button("Ниже") { taskPanel.move(task, to: index + 1) }
            }
            Divider()
            Button("Убрать из фокуса") { taskPanel.archive(task) }
        }
        .modifier(DisposalModifier(
            isActive: taskPanel.runningDisposal?.taskID == task.id,
            effect: taskPanel.runningDisposal?.effect ?? .burn,
            reduceMotion: reduceMotion
        ))
    }

    private func deadlineLine(_ task: CompanionTask) -> some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle().fill(Color.white.opacity(0.06))
                Rectangle()
                    .fill(color(for: task))
                    .frame(width: proxy.size.width * task.remainingFraction(now: taskPanel.now))
            }
        }
        .frame(height: 1)
        .padding(.leading, 24 + DesignTokens.Spacing.xs)
    }

    private func color(for task: CompanionTask) -> Color {
        if task.remainingSeconds(now: taskPanel.now) <= 0 { return DesignTokens.Palette.accentRecording }
        if task.isDueSoon(now: taskPanel.now) { return DesignTokens.Palette.accentWarning }
        return DesignTokens.Palette.textSecondary
    }

    // MARK: - Пустой слот

    @ViewBuilder
    private func emptySlot(isFirstEmpty: Bool) -> some View {
        if isFirstEmpty && editingSlot {
            addForm
        } else {
            Button {
                editingSlot = true
                inputFocused = true
            } label: {
                HStack(spacing: DesignTokens.Spacing.xs) {
                    Image(systemName: "plus")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(DesignTokens.Palette.textTertiary)
                        .frame(width: 24, height: 24)
                    Text("Добавить важное дело")
                        .font(DesignTokens.Typography.taskTitle())
                        .foregroundStyle(DesignTokens.Palette.textTertiary)
                    Spacer()
                }
                .padding(.horizontal, DesignTokens.Spacing.m)
                .frame(height: 56)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    private var addForm: some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
            TextField("Название дела", text: $draftTitle)
                .textFieldStyle(.plain)
                .font(DesignTokens.Typography.taskTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
                .focused($inputFocused)
                .onSubmit(commit)
                .padding(.vertical, 4)
                .padding(.horizontal, DesignTokens.Spacing.xs)
                .background(
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(DesignTokens.Palette.surfaceRaised)
                )
                .keyboardFocusRing(inputFocused, cornerRadius: 6)
            HStack(spacing: DesignTokens.Spacing.xxs) {
                // Срок обязателен и виден ДО сохранения.
                ForEach(CharacterConfig.deadlinePresets, id: \.self) { minutes in
                    presetChip(minutes)
                }
                Spacer(minLength: 0)
                Button("В фокус", action: commit)
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
            }
            Text("После срока задача уйдёт в историю")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
        }
        .padding(.horizontal, DesignTokens.Spacing.m)
        .frame(height: 56)
    }

    private func presetChip(_ minutes: Int) -> some View {
        Button {
            draftMinutes = minutes
        } label: {
            Text("\(minutes) мин")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(
                    draftMinutes == minutes
                        ? DesignTokens.Palette.textPrimary
                        : DesignTokens.Palette.textSecondary
                )
                .padding(.horizontal, DesignTokens.Spacing.xs)
                .padding(.vertical, 3)
                .background(
                    Capsule().fill(
                        draftMinutes == minutes
                            ? DesignTokens.Palette.accentSelection.opacity(0.28)
                            : DesignTokens.Palette.surfaceRaised
                    )
                )
        }
        .buttonStyle(.plain)
    }

    private func commit() {
        guard taskPanel.addTask(title: draftTitle, minutes: draftMinutes) == .added else { return }
        draftTitle = ""
        editingSlot = false
        inputFocused = false
    }

    // MARK: - Подвал

    private var footer: some View {
        VStack(spacing: DesignTokens.Spacing.xxs) {
            if let notice = noticeText {
                HStack(spacing: DesignTokens.Spacing.xs) {
                    Text(notice)
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                        .lineLimit(2)
                    Spacer(minLength: 0)
                    if let expired = taskPanel.recentlyExpired {
                        Button("Вернуть") { _ = taskPanel.restore(expired) }
                            .buttonStyle(.plain)
                            .font(DesignTokens.Typography.caption())
                            .foregroundStyle(DesignTokens.Palette.accentSelection)
                    }
                }
                .padding(.horizontal, DesignTokens.Spacing.m)
            }
            HStack(spacing: DesignTokens.Spacing.s) {
                Text("Три дела. Одно внимание.")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
                Spacer(minLength: 0)
                // Видимая альтернатива каждому жесту.
                ForEach(CompanionPage.allCases) { page in
                    pageButton(page)
                }
            }
            .padding(.horizontal, DesignTokens.Spacing.m)
        }
        .frame(height: 40, alignment: .bottom)
        .padding(.bottom, DesignTokens.Spacing.s)
    }

    private var noticeText: String? {
        if let expired = taskPanel.recentlyExpired {
            return "Задача «\(expired.title)» сгорела"
        }
        if let message = taskPanel.slotsFullMessage { return message }
        if let message = taskPanel.catchUpMessage { return message }
        if let caption = taskPanel.reminderCaption { return caption }
        return nil
    }

    private func pageButton(_ page: CompanionPage) -> some View {
        Button {
            navigation.show(page: page)
        } label: {
            Text(page.title)
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(
                    navigation.page == page
                        ? DesignTokens.Palette.textPrimary
                        : DesignTokens.Palette.textSecondary
                )
                .padding(.horizontal, DesignTokens.Spacing.xs)
                .padding(.vertical, 3)
                .background(
                    Capsule().fill(
                        navigation.page == page
                            ? DesignTokens.Palette.accentSelection.opacity(0.25)
                            : Color.clear
                    )
                )
        }
        .buttonStyle(.plain)
        .help("Открыть «\(page.title)»")
    }
}

/// История открывается НАКЛАДКОЙ внутри панели, а не системным листом:
/// у компаньона нет обычного окна, к которому лист можно прикрепить.
extension View {
    func sheetlessHistory<Overlay: View>(
        isPresented: Binding<Bool>,
        @ViewBuilder content: () -> Overlay
    ) -> some View {
        overlay {
            if isPresented.wrappedValue {
                content()
            }
        }
    }
}
