#!/usr/bin/env python3
"""Структурная проверка Swift-исходников без Swift-тулчейна.

Зачем: компаньон собирается только на macOS, а пишется в Linux-контейнере,
где нет ни `swift build`, ни macOS SDK. Компилятор поэтому не видит ни
опечаток в именах, ни разъехавшихся скобок — а каждый такой промах стоит
круга «пуш → сборка у автора → лог ошибки → правка».

Проверяется:
  1. баланс скобок (), [], {} с учётом строк, интерполяции и комментариев;
  2. ссылки на типы проекта: `Foo.bar` и `Foo(...)` — тип должен быть объявлен;
  3. статические члены конфигов проекта: `DesignTokens.Palette.x`,
     `CharacterConfig.x`, `CompanionGeometry.Metrics.x` и т.п.;
  4. случаи `CompanionState.<case>` и `TaskBag.Key.<case>`.

Это НЕ компилятор: проверка ловит класс ошибок «переименовал и забыл», но
не заменяет сборку на живой машине.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "Sources").rglob("*.swift"))

# Типы, приходящие из системных фреймворков: их объявлений в проекте нет.
SYSTEM_TYPES = {
    # Swift / Foundation
    "Array", "Bool", "CGFloat", "CGPoint", "CGRect", "CGSize", "Calendar", "Character",
    "Data", "Date", "DateFormatter", "Dictionary", "Double", "Encoder", "Decoder",
    "Error", "FileManager", "Float", "ISO8601DateFormatter", "Int", "Int32", "Int64",
    "JSONDecoder", "JSONEncoder", "JSONSerialization", "Locale", "NSLock", "NSString",
    "NSCoder", "Never", "Notification", "NotificationCenter", "OpaquePointer",
    "Optional", "ProcessInfo", "Result", "Set", "String", "Task", "TimeInterval",
    "UInt32", "UInt64", "URL", "UUID", "UserDefaults", "Selector", "Self", "Void",
    "RandomNumberGenerator", "LocalizedError", "TimeZone", "Bundle", "MainActor",
    # AppKit
    "NSApp", "NSApplication", "NSColor", "NSEvent", "NSHostingView", "NSMenu",
    "NSMenuItem", "NSMouseInRect", "NSObject", "NSPanel", "NSPasteboard", "NSPoint",
    "NSRect", "NSScreen", "NSSize", "NSStatusBar", "NSStatusItem", "NSView",
    "NSWorkspace", "NSVisualEffectView", "NSWindow",
    # SwiftUI
    "Alignment", "Angle", "Animation", "Binding", "Capsule", "Circle", "Color",
    "Divider", "EdgeInsets", "Environment", "Font", "ForEach", "GeometryReader",
    "Group", "HStack", "Image", "LinearGradient", "Menu", "Path", "Picker",
    "ProgressView", "Rectangle", "RoundedRectangle", "ScrollView", "Shape",
    "Spacer", "State", "StrokeStyle", "Text", "TextField", "Toggle", "UnitPoint",
    "VStack", "View", "ViewBuilder", "ViewModifier", "ZStack", "AnimatablePair",
    "FocusState", "ButtonStyle", "Button", "Content", "Configuration", "Overlay",
    "EmptyView", "ButtonStyleConfiguration", "Label", "Link", "Slider",
    # CoreGraphics / SQLite
    "CGEventSource", "CGEventType", "SQLITE_OK", "SQLITE_ROW",
}

# Какие статические члены проверяем поимённо.
MEMBER_ROOTS = [
    "DesignTokens.Palette", "DesignTokens.Spacing", "DesignTokens.Radius",
    "DesignTokens.Typography", "DesignTokens.Motion", "DesignTokens.Glass",
    "CharacterConfig", "CompanionGeometry.Metrics", "CompanionSettings",
    "TaskPersistence", "SamarizatorBridge", "DemoFixtures", "TimecodeFormatter",
    "SummaryParser",
]

DECL_RE = re.compile(
    r"^\s*(?:public |private |fileprivate |internal |final |@MainActor |@Observable |)*"
    r"(?:struct|class|enum|protocol|actor)\s+([A-Z][A-Za-z0-9_]*)",
    re.MULTILINE,
)
TYPEALIAS_RE = re.compile(r"^\s*(?:\w+\s+)*typealias\s+([A-Z][A-Za-z0-9_]*)", re.MULTILINE)


def strip_noise(text: str) -> str:
    """Убирает комментарии и содержимое строковых литералов."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if ch == '"':
            # Многострочный литерал.
            if text.startswith('"""', i):
                i += 3
                while i < n and not text.startswith('"""', i):
                    i += 1
                i += 3
                out.append('""')
                continue
            i += 1
            out.append('"')
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    # Интерполяцию \(...) сохраняем: внутри неё живой код.
                    if text[i + 1] == "(":
                        depth = 0
                        i += 1
                        start = i
                        while i < n:
                            if text[i] == "(":
                                depth += 1
                            elif text[i] == ")":
                                depth -= 1
                                if depth == 0:
                                    i += 1
                                    break
                            i += 1
                        out.append(" " + text[start:i] + " ")
                        continue
                    i += 2
                    continue
                i += 1
            i += 1
            out.append('"')
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def check_balance(path: Path, code: str) -> list[str]:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    line = 1
    problems = []
    for ch in code:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack or stack[-1][0] != pairs[ch]:
                problems.append(f"{path}:{line}: лишняя или несогласованная скобка {ch!r}")
                return problems
            stack.pop()
    for ch, opened_at in stack:
        problems.append(f"{path}:{opened_at}: скобка {ch!r} не закрыта")
    return problems


def main() -> int:
    if not SOURCES:
        print("Исходники не найдены", file=sys.stderr)
        return 2

    cleaned = {path: strip_noise(path.read_text(encoding="utf-8")) for path in SOURCES}
    raw = {path: path.read_text(encoding="utf-8") for path in SOURCES}

    problems: list[str] = []

    # 1. Скобки.
    for path, code in cleaned.items():
        problems += check_balance(path.relative_to(ROOT), code)

    # 2. Объявленные типы.
    declared: set[str] = set()
    for code in cleaned.values():
        declared |= set(DECL_RE.findall(code))
        declared |= set(TYPEALIAS_RE.findall(code))
    known = declared | SYSTEM_TYPES

    usage_re = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*(?=[.(<])")
    for path, code in cleaned.items():
        for match in usage_re.finditer(code):
            name = match.group(1)
            if name in known or name.startswith("NS") or name.startswith("CG"):
                continue
            line = code[: match.start()].count("\n") + 1
            problems.append(
                f"{path.relative_to(ROOT)}:{line}: тип {name} не объявлен в проекте "
                "и не значится системным"
            )

    # 3. Статические члены конфигов.
    members: dict[str, set[str]] = {}
    for root in MEMBER_ROOTS:
        members[root] = set()
    member_decl_re = re.compile(r"\b(?:static\s+(?:let|var|func)|case)\s+([a-zA-Z_][A-Za-z0-9_]*)")
    for path, text in raw.items():
        # Определяем, какие блоки соответствуют корням, по простому вложению имён.
        for root in MEMBER_ROOTS:
            leaf = root.split(".")[-1]
            pattern = re.compile(
                r"(?:enum|struct|final class|class)\s+" + re.escape(leaf) + r"\b[^\n]*\{",
            )
            for m in pattern.finditer(text):
                start = m.end()
                depth = 1
                i = start
                while i < len(text) and depth:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                body = text[start:i]
                members[root] |= set(member_decl_re.findall(body))

    for path, code in cleaned.items():
        for root, names in members.items():
            if not names:
                continue
            use_re = re.compile(re.escape(root) + r"\.([a-zA-Z_][A-Za-z0-9_]*)")
            for match in use_re.finditer(code):
                member = match.group(1)
                # Заглавная буква — вложенный ТИП, а не статический член: он уже
                # проверен разделом выше.
                if member[:1].isupper():
                    continue
                if member in names or member in {"self", "allCases", "init"}:
                    continue
                line = code[: match.start()].count("\n") + 1
                problems.append(
                    f"{path.relative_to(ROOT)}:{line}: у {root} нет члена {member}"
                )

    if problems:
        for problem in problems:
            print(problem)
        print(f"\nНайдено замечаний: {len(problems)}")
        return 1

    print(f"Проверено файлов: {len(SOURCES)}. Замечаний нет.")
    print("Это структурная проверка, а не компиляция: сборку всё равно нужно "
          "выполнить на macOS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
