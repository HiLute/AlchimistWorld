"""Оформление: две темы, палитра и общий стиль (FR-9.5).

Тема — свойство интерфейса, а не предметной области, поэтому все цвета живут здесь.
Ядро задаёт только «канонический» оттенок каждой стихии (02 §6); под тёмный и
светлый фон он подбирается отдельно, иначе Свет теряется на пергаменте, а Воздух —
на чернилах.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from alchimist.core.elements import Element
from alchimist.core.models import Rarity, Theme


@dataclass(frozen=True, slots=True)
class Palette:
    """Набор цветов одной темы."""

    name: Theme

    #: Фон окна и «воздух» между панелями.
    window: str
    #: Поля ввода, списки, таблицы.
    surface: str
    #: Приподнятое: карточки, шапки таблиц, боковая панель.
    raised: str
    #: Полоса чередования строк.
    alternate: str
    border: str
    border_strong: str

    text: str
    muted: str
    #: Латунь при свече — акцент всего приложения.
    accent: str
    accent_hover: str
    on_accent: str
    #: Выделенная строка списка.
    selection: str
    on_selection: str

    warning: str
    error: str
    success: str

    elements: dict[Element, str] = field(default_factory=dict)
    rarities: dict[Rarity, str] = field(default_factory=dict)


DARK = Palette(
    name=Theme.DARK,
    window="#181a21",
    surface="#20232c",
    raised="#272b36",
    alternate="#1d2028",
    border="#333847",
    border_strong="#454c60",
    text="#e7e3d9",
    muted="#8d93a3",
    accent="#d2a344",
    accent_hover="#e3b559",
    on_accent="#17181d",
    selection="#b8862b",
    on_selection="#17181d",
    warning="#e0a23e",
    error="#e06a5e",
    success="#5cb874",
    elements={
        Element.FIRE: "#ef6a5f",
        Element.WATER: "#5b9bf0",
        Element.AIR: "#a9c9e6",
        Element.EARTH: "#c08a4e",
        Element.LIGHT: "#f0cb5c",
        Element.DARK: "#a179e0",
        Element.MAGIC: "#e874c0",
    },
    rarities={
        Rarity.COMMON: "#9aa0ad",
        Rarity.UNCOMMON: "#5cb874",
        Rarity.RARE: "#5b9bf0",
        Rarity.EPIC: "#b57ae0",
        Rarity.LEGENDARY: "#e8944a",
    },
)

LIGHT = Palette(
    name=Theme.LIGHT,
    window="#f2ece0",
    surface="#fbf8f1",
    raised="#fffdf8",
    alternate="#ece5d7",
    border="#d9cfba",
    border_strong="#bfb198",
    text="#2f2a23",
    muted="#78705f",
    accent="#9a6b18",
    accent_hover="#b37f22",
    on_accent="#fffdf8",
    selection="#c89a3c",
    on_selection="#2f2a23",
    warning="#a96a12",
    error="#b23b30",
    success="#2f7d45",
    elements={
        Element.FIRE: "#c13a30",
        Element.WATER: "#2a5fb5",
        Element.AIR: "#5e93bb",
        Element.EARTH: "#7a4e24",
        Element.LIGHT: "#b8891a",
        Element.DARK: "#6a3fa5",
        Element.MAGIC: "#b13a8c",
    },
    rarities={
        Rarity.COMMON: "#7a7266",
        Rarity.UNCOMMON: "#2f7d45",
        Rarity.RARE: "#1c6bb8",
        Rarity.EPIC: "#7d3a9c",
        Rarity.LEGENDARY: "#c2681a",
    },
)

PALETTES: dict[Theme, Palette] = {Theme.DARK: DARK, Theme.LIGHT: LIGHT}

_current: Palette = DARK
#: Стиль Qt ставится один раз за запуск: смена стиля поверх уже поставленной
#: таблицы стилей заставляет Qt разбирать её без готового стиля и ругаться.
_style_applied = False


# ── оповещение о смене темы ───────────────────────────────────────────────────
class _Notifier(QObject):
    """Виджеты, которые кешируют цвета, пересобирают их по этому сигналу."""

    changed = Signal()


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def on_theme_changed(slot) -> None:
    """Подписка виджета на смену темы."""
    notifier().changed.connect(slot)


# ── текущая тема ──────────────────────────────────────────────────────────────
def current_theme() -> Theme:
    return _current.name


def element_color(element: Element) -> QColor:
    return QColor(_current.elements[element])


def rarity_color(rarity: Rarity) -> QColor:
    return QColor(_current.rarities[Rarity(rarity)])


def muted_color() -> QColor:
    return QColor(_current.muted)


def warning_color() -> QColor:
    return QColor(_current.warning)


def error_color() -> QColor:
    return QColor(_current.error)


def accent_color() -> QColor:
    return QColor(_current.accent)


def readable_text_color(background: QColor) -> QColor:
    """Чёрный или белый — смотря что читается на фоне."""
    luminance = 0.299 * background.red() + 0.587 * background.green() + 0.114 * background.blue()
    return QColor("#17181d") if luminance > 150 else QColor("#f5f2ea")


def bold(font: QFont) -> QFont:
    font = QFont(font)
    font.setBold(True)
    return font


# ── значки флажков ────────────────────────────────────────────────────────────
#: Стиль со своими правилами перестаёт рисовать «галочку», поэтому она рисуется
#: сама и подкладывается в таблицу стилей картинкой.
_INDICATOR_SIZE = 16
#: Ключ — тема вместе с отпечатком цветов: по одному имени темы разные
#: палитры делили бы один и тот же набор готовых значков.
_indicator_cache: dict[tuple[Theme, str], dict[str, str]] = {}


def _draw_indicator(colors: Palette, *, checked: bool, radio: bool, hover: bool) -> QPixmap:
    ratio = 2
    size = _INDICATOR_SIZE
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRectF(1, 1, size - 2, size - 2)
    border = QColor(colors.accent if (checked or hover) else colors.border_strong)
    painter.setPen(QPen(border, 1.4))
    painter.setBrush(QColor(colors.accent) if checked else QColor(colors.surface))
    if radio:
        painter.drawEllipse(box)
    else:
        painter.drawRoundedRect(box, 4, 4)

    if checked:
        mark = QColor(colors.on_accent)
        if radio:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(mark)
            painter.drawEllipse(box.adjusted(4.2, 4.2, -4.2, -4.2))
        else:
            painter.setPen(QPen(mark, 2.0, c=Qt.PenCapStyle.RoundCap, j=Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline(
                [
                    box.topLeft() + QRectF(3.2, 7.4, 0, 0).topLeft(),
                    box.topLeft() + QRectF(5.9, 10.2, 0, 0).topLeft(),
                    box.topLeft() + QRectF(10.8, 3.8, 0, 0).topLeft(),
                ]
            )
    painter.end()
    return pixmap


def _fingerprint(colors: Palette) -> str:
    """Короткий отпечаток цветов темы: меняются цвета — меняется и путь."""
    parts = [
        colors.surface,
        colors.border,
        colors.border_strong,
        colors.accent,
        colors.accent_hover,
        colors.on_accent,
        colors.muted,
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _drop_stale(root: Path, *, keep: Path) -> None:
    """Убирает значки прежних палитр: они больше никогда не понадобятся.

    Папка без отпечатка осталась от версий до этой схемы — её тоже подбираем.
    """
    theme = keep.name.split("-")[0]
    for folder in root.iterdir():
        same_theme = folder.name == theme or folder.name.startswith(f"{theme}-")
        if folder.is_dir() and same_theme and folder != keep:
            with suppress(OSError):
                shutil.rmtree(folder)


def indicator_paths(colors: Palette) -> dict[str, str]:
    """Рисует значки один раз на тему и возвращает пути для таблицы стилей."""
    key = (colors.name, _fingerprint(colors))
    cached = _indicator_cache.get(key)
    if cached is not None:
        return cached

    # Имя приложения в путь не берём: Qt подставляет туда argv[0], а он может
    # содержать что угодно, и тогда url() в таблице стилей не разбирается.
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    base = Path(root) if root else Path(tempfile.gettempdir())
    # В имени папки — отпечаток цветов: иначе после правки палитры на диске
    # навсегда остались бы значки старой, ведь готовый файл мы не перерисовываем.
    root_folder = base / "AlchimistWorld" / "indicators"
    folder = root_folder / f"{colors.name.value}-{key[1]}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _drop_stale(root_folder, keep=folder)
    except OSError:
        folder = Path(tempfile.mkdtemp(prefix="alchimist-indicators-"))

    paths: dict[str, str] = {}
    for radio in (False, True):
        for checked in (False, True):
            for hover in (False, True):
                key = (
                    f"{'radio' if radio else 'check'}"
                    f"_{'on' if checked else 'off'}"
                    f"_{'hover' if hover else 'plain'}"
                )
                target = folder / f"{key}.png"
                if not target.exists():
                    _draw_indicator(colors, checked=checked, radio=radio, hover=hover).save(
                        str(target), "PNG"
                    )
                paths[key] = target.as_posix()
    _indicator_cache[key] = paths
    return paths


# ── применение ────────────────────────────────────────────────────────────────
def qpalette(colors: Palette) -> QPalette:
    """QPalette для стандартных элементов Qt (диалоги, всплывающие подсказки)."""
    p = QPalette()
    window = QColor(colors.window)
    surface = QColor(colors.surface)
    text = QColor(colors.text)
    muted = QColor(colors.muted)

    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, surface)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.alternate))
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, QColor(colors.raised))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.raised))
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Highlight, QColor(colors.selection))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.on_selection))
    p.setColor(QPalette.ColorRole.Link, QColor(colors.accent))
    p.setColor(QPalette.ColorRole.Mid, QColor(colors.border))
    p.setColor(QPalette.ColorRole.Dark, QColor(colors.border_strong))

    for group in (QPalette.ColorGroup.Disabled,):
        p.setColor(group, QPalette.ColorRole.Text, muted)
        p.setColor(group, QPalette.ColorRole.WindowText, muted)
        p.setColor(group, QPalette.ColorRole.ButtonText, muted)
    return p


def stylesheet(c: Palette) -> str:
    """Общий стиль. Скруглений немного, акцент — латунь при свече."""
    i = indicator_paths(c)
    return f"""
QWidget {{
    color: {c.text};
}}
QMainWindow, QDialog {{
    background: {c.window};
}}

/* ── боковая панель ─────────────────────────────────────────────── */
QListWidget#nav {{
    background: {c.raised};
    border: none;
    border-right: 1px solid {c.border};
    outline: none;
    padding: 8px 6px;
}}
QListWidget#nav::item {{
    padding: 9px 12px;
    border-radius: 7px;
    margin: 1px 4px;
    color: {c.text};
}}
QListWidget#nav::item:hover {{
    background: {c.surface};
}}
QListWidget#nav::item:selected {{
    background: {c.accent};
    color: {c.on_accent};
    font-weight: 600;
}}
QLabel#navSeparator {{
    color: {c.border};
}}

/* ── заголовки и подписи ────────────────────────────────────────── */
QLabel#pageTitle {{
    font-size: 18px;
    font-weight: 600;
    color: {c.text};
    padding-bottom: 2px;
}}
QFrame#titleRule {{
    background: {c.accent};
    border: none;
    max-height: 2px;
    min-height: 2px;
}}
QLabel#hint {{
    color: {c.muted};
}}
QLabel#sectionLabel {{
    color: {c.muted};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ── таблицы и списки ───────────────────────────────────────────── */
QTreeWidget, QTreeView, QListWidget, QTableWidget {{
    background: {c.surface};
    alternate-background-color: {c.alternate};
    border: 1px solid {c.border};
    border-radius: 8px;
    outline: none;
    selection-background-color: {c.selection};
    selection-color: {c.on_selection};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 4px 2px;
    border: none;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {c.raised};
}}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {c.selection};
    color: {c.on_selection};
}}
QHeaderView::section {{
    background: {c.raised};
    color: {c.muted};
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {c.border};
    border-right: 1px solid {c.border};
    font-weight: 600;
}}
QHeaderView::section:last {{
    border-right: none;
}}
QTreeWidget QHeaderView::section:hover {{
    color: {c.text};
}}

/* ── ввод ───────────────────────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {c.selection};
    selection-color: {c.on_selection};
}}
/* Число только для показа (сумма элементов): выглядит как поле, но это подпись. */
QLabel#valueField {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
    padding: 5px 8px;
    min-width: 3em;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {c.accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QDateEdit:disabled {{
    color: {c.muted};
    background: {c.window};
}}
QComboBox QAbstractItemView {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
    padding: 3px;
}}
/* ── кнопки ─────────────────────────────────────────────────────── */
QPushButton {{
    background: {c.raised};
    border: 1px solid {c.border};
    border-radius: 6px;
    padding: 6px 14px;
    color: {c.text};
}}
QPushButton:hover {{
    border-color: {c.accent};
    color: {c.accent_hover};
}}
QPushButton:pressed {{
    background: {c.accent};
    color: {c.on_accent};
    border-color: {c.accent};
}}
QPushButton:disabled {{
    color: {c.muted};
    border-color: {c.border};
    background: {c.window};
}}
QPushButton:default {{
    border: 1px solid {c.accent};
    font-weight: 600;
}}

/* ── группы и карточки ──────────────────────────────────────────── */
QGroupBox {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {c.accent};
}}
QFrame#card {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 8px;
}}
QTextBrowser {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 8px;
    padding: 8px;
}}

/* ── вкладки ────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {c.border};
    border-radius: 8px;
    top: -1px;
    background: {c.window};
}}
QTabBar::tab {{
    background: transparent;
    color: {c.muted};
    padding: 7px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}}
QTabBar::tab:hover {{
    color: {c.text};
}}
QTabBar::tab:selected {{
    background: {c.window};
    color: {c.accent};
    border-color: {c.border};
    border-bottom-color: {c.window};
    font-weight: 600;
}}

/* ── меню и строка состояния ────────────────────────────────────── */
QMenuBar {{
    background: {c.raised};
    border-bottom: 1px solid {c.border};
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {c.accent};
    color: {c.on_accent};
    border-radius: 4px;
}}
QMenu {{
    background: {c.raised};
    border: 1px solid {c.border};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {c.accent};
    color: {c.on_accent};
}}
QMenu::separator {{
    height: 1px;
    background: {c.border};
    margin: 4px 8px;
}}
QStatusBar {{
    background: {c.raised};
    border-top: 1px solid {c.border};
    color: {c.muted};
}}
QStatusBar::item {{
    border: none;
}}

/* ── прочее ─────────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
}}
QCheckBox::indicator:unchecked {{ image: url({i["check_off_plain"]}); }}
QCheckBox::indicator:unchecked:hover {{ image: url({i["check_off_hover"]}); }}
QCheckBox::indicator:checked {{ image: url({i["check_on_plain"]}); }}
QCheckBox::indicator:checked:hover {{ image: url({i["check_on_hover"]}); }}
QRadioButton::indicator:unchecked {{ image: url({i["radio_off_plain"]}); }}
QRadioButton::indicator:unchecked:hover {{ image: url({i["radio_off_hover"]}); }}
QRadioButton::indicator:checked {{ image: url({i["radio_on_plain"]}); }}
QRadioButton::indicator:checked:hover {{ image: url({i["radio_on_hover"]}); }}
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:hover {{
    background: {c.border};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0;
}}
QScrollBar::handle {{
    background: {c.border_strong};
    border-radius: 5px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{
    background: {c.accent};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QToolTip {{
    background: {c.raised};
    color: {c.text};
    border: 1px solid {c.accent};
    border-radius: 5px;
    padding: 4px 7px;
}}
"""


def apply_theme(app: QApplication, theme: Theme | str) -> Palette:
    """Ставит палитру и стиль. Возвращает применённые цвета."""
    global _current, _style_applied
    _current = PALETTES.get(Theme(theme), DARK)
    # Fusion — единственный стиль, одинаково слушающийся палитры на всех трёх ОС.
    if not _style_applied:
        app.setStyleSheet("")
        app.setStyle("Fusion")
        _style_applied = True
    app.setPalette(qpalette(_current))
    app.setStyleSheet(stylesheet(_current))
    notifier().changed.emit()
    return _current
