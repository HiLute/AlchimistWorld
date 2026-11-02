"""Страница «Журнал» (FR-8.x)."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from alchimist.core.errors import AlchimistError
from alchimist.core.models import (
    BASE_NAMES_RU,
    BaseType,
    JournalEntryType,
    Outcome,
    ResultKind,
)
from alchimist.i18n import _, describe
from alchimist.services.journal import JournalFilter
from alchimist.ui_qt.pages.base import Page
from alchimist.ui_qt.theme import error_color, muted_color
from alchimist.ui_qt.widgets.common import (
    FilterBar,
    SearchBox,
    base_text,
    hint_label,
    page_heading,
)
from alchimist.ui_qt.widgets.element_counter import ElementCounters

ANY = "__any__"

TYPE_NAMES = {
    JournalEntryType.BREW: "варка",
    JournalEntryType.USE: "использование",
    JournalEntryType.ADJUST: "правка инвентаря",
    JournalEntryType.DISTILL: "дистилляция",
}

RESULT_NAMES = {
    ResultKind.KNOWN: "известное зелье",
    ResultKind.NEW: "новое зелье",
    ResultKind.NOTHING: "ничего не вышло",
    ResultKind.JABBERWOCK: "Бармаглот",
    ResultKind.NONE: "—",
}


def _ingredient_name(ingredients: dict, ingredient_id: str) -> str:
    item = ingredients.get(ingredient_id)
    return item.name if item else ingredient_id


class JournalPage(Page):
    title = "Журнал"
    icon = "📜"

    def __init__(self, app, bridge, parent: QWidget | None = None) -> None:
        super().__init__(app, bridge, parent)

        header = QHBoxLayout()
        header.addWidget(page_heading(_("Журнал")))
        header.addStretch(1)
        self.search = SearchBox(_("Поиск по заметке или зелью…"))
        self.search.setMaximumWidth(300)
        self.search.search.connect(lambda _t: self.refresh())
        header.addWidget(self.search)

        self.filters = FilterBar()
        self.filters.add_combo(
            "type",
            _("Тип"),
            [(_("любой"), ANY), *[(TYPE_NAMES[t], t) for t in JournalEntryType]],
            enum_type=JournalEntryType,
        )
        self.filters.add_combo(
            "outcome",
            _("Исход"),
            [(_("любой"), ANY), (_("успех"), Outcome.SUCCESS), (_("провал"), Outcome.FAILURE)],
            enum_type=Outcome,
        )
        self.filters.add_combo(
            "base",
            _("Основа"),
            [(_("любая"), ANY), *[(BASE_NAMES_RU[b], b) for b in BaseType]],
            enum_type=BaseType,
        )
        self.potion_filter = self.filters.add_combo("potion", _("Зелье"), [(_("любое"), ANY)])
        self.period = QCheckBox(_("Период"))
        self.since = QDateEdit(QDate.currentDate().addMonths(-1))
        self.until = QDateEdit(QDate.currentDate())
        for editor in (self.since, self.until):
            editor.setCalendarPopup(True)
            editor.setEnabled(False)
            editor.dateChanged.connect(lambda _d: self.refresh())
        self.period.toggled.connect(self._toggle_period)
        self.filters.add_widget(self.period, self.since, QLabel("—"), self.until)
        self.filters.add_stretch()
        self.filters.changed.connect(self.refresh)

        # FR-8.4: «пробовал ли я Огонь+Вода на вязкой основе?»
        self.by_combination = QCheckBox(_("Искать комбинацию"))
        self.by_combination.toggled.connect(self._toggle_combination)
        # Четыре в строку: семь в одну линию задавали ширину всего окна (issue #2).
        self.combination = ElementCounters(columns=4, maximum=9)
        self.combination.setEnabled(False)
        self.combination.changed.connect(lambda _v: self.refresh())

        combination_row = QHBoxLayout()
        combination_row.addWidget(self.by_combination)
        combination_row.addWidget(self.combination)
        combination_row.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            [
                _("Когда"),
                _("Что"),
                _("Основа"),
                _("Реагенты / зелье"),
                _("Сумма"),
                _("Сл"),
                _("Итог"),
            ]
        )
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in (0, 1, 2, 4, 5, 6):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAlternatingRowColors(True)

        buttons = QHBoxLayout()
        self.save_recipe_button = QPushButton(_("Сохранить как рецепт"))
        self.save_recipe_button.setToolTip(
            _("Записать комбинацию из этой варки в рецепт получившегося зелья (П-7.5)")
        )
        self.save_recipe_button.clicked.connect(self._save_recipe)
        buttons.addWidget(self.save_recipe_button)
        self.undo_button = QPushButton(_("Отменить запись"))
        self.undo_button.setToolTip(
            _("Вернуть реагенты и убрать полученное — для варки и для разбора (FR-7.6, FR-13.7)")
        )
        self.undo_button.clicked.connect(self._undo)
        buttons.addWidget(self.undo_button)
        buttons.addStretch(1)

        box = self.layout_box()
        box.addLayout(header)
        box.addWidget(self.filters)
        box.addLayout(combination_row)
        box.addWidget(self.tree, 1)
        box.addLayout(buttons)
        box.addWidget(hint_label(_("Отменённые варки остаются в журнале зачёркнутыми.")))

        self.search.focus_shortcut(self)
        bridge.journal_changed.connect(self.invalidate)
        bridge.catalog_changed.connect(self.invalidate)

    def _toggle_combination(self, on: bool) -> None:
        self.combination.setEnabled(on)
        self.refresh()

    def _toggle_period(self, on: bool) -> None:
        self.since.setEnabled(on)
        self.until.setEnabled(on)
        self.refresh()

    def refresh(self) -> None:
        super().refresh()
        # Записать рецепт в справочник — правка справочника, это может только GM (FR-14.6).
        self.save_recipe_button.setVisible(self.app.can_edit_catalog)
        potions = self.app.catalog.potion_map()
        ingredients = self.app.catalog.ingredient_map()

        names = sorted({p.name: p.id for p in potions.values()}.items())
        if self.potion_filter.count() - 1 != len(names):
            current = self.potion_filter.currentData()
            self.potion_filter.blockSignals(True)
            self.potion_filter.clear()
            self.potion_filter.addItem(_("любое"), ANY)
            for name, potion_id in names:
                self.potion_filter.addItem(name, potion_id)
            index = self.potion_filter.findData(current)
            self.potion_filter.setCurrentIndex(max(0, index))
            self.potion_filter.blockSignals(False)

        entry_type = self.filters.value("type")
        outcome = self.filters.value("outcome")
        base = self.filters.value("base")
        potion_id = self.filters.value("potion")
        criteria = JournalFilter(
            types=None if entry_type == ANY else frozenset({entry_type}),
            outcomes=None if outcome == ANY else frozenset({outcome}),
            base=None if base == ANY else base,
            potion_id=None if potion_id == ANY else potion_id,
            since=self.since.date().toPython() if self.period.isChecked() else None,
            until=self.until.date().toPython() if self.period.isChecked() else None,
            elements=self.combination.vector() if self.by_combination.isChecked() else None,
            text=self.search.text().strip(),
        )

        self.tree.clear()
        entry_names = {pid: p.name for pid, p in potions.items()}
        for entry in self.app.journal.filtered(criteria, entry_names):
            if entry.type is JournalEntryType.BREW:
                what = " + ".join(
                    f"{_ingredient_name(ingredients, r.ingredient_id)}×{r.qty}"
                    for r in entry.reagents
                )
                result = RESULT_NAMES.get(
                    entry.result.kind if entry.result else ResultKind.NONE, "—"
                )
                if entry.result and entry.result.potion_id in potions:
                    result = f"{potions[entry.result.potion_id].name} ×{entry.yield_qty}"
                if entry.outcome is Outcome.FAILURE:
                    jabberwock = entry.result and entry.result.kind is ResultKind.JABBERWOCK
                    result = _("провал: Бармаглот") if jabberwock else _("провал")
                if entry.result and entry.result.potion_id in potions and entry.portions > 1:
                    result = f"{potions[entry.result.potion_id].name} ×{entry.yield_qty}"
                columns = [
                    entry.ts.strftime("%Y-%m-%d %H:%M"),
                    TYPE_NAMES[entry.type],
                    base_text(self.app, entry.base_key),
                    what,
                    entry.elements.format_ru(),
                    str(entry.difficulty) if entry.difficulty is not None else "—",
                    result,
                ]
            elif entry.type is JournalEntryType.DISTILL:
                what = " + ".join(
                    f"{_ingredient_name(ingredients, r.ingredient_id)}×{r.qty}"
                    for r in entry.reagents
                )
                got = " + ".join(
                    f"{_ingredient_name(ingredients, r.ingredient_id)}×{r.qty}"
                    for r in entry.produced
                )
                columns = [
                    entry.ts.strftime("%Y-%m-%d %H:%M"),
                    TYPE_NAMES[entry.type],
                    "—",
                    what,
                    entry.elements.format_ru(),
                    "—",
                    got or "—",
                ]
            else:
                name = (
                    potions[entry.potion_id].name
                    if entry.potion_id in potions
                    else (entry.potion_id or "—")
                )
                columns = [
                    entry.ts.strftime("%Y-%m-%d %H:%M"),
                    TYPE_NAMES[entry.type],
                    "—",
                    name,
                    "",
                    "",
                    f"−{entry.qty}",
                ]
            item = QTreeWidgetItem(columns)
            item.setData(0, Qt.ItemDataRole.UserRole, entry.id)
            if entry.note:
                item.setToolTip(3, entry.note)
            if entry.undone:
                font = item.font(0)
                font.setStrikeOut(True)
                for column in range(self.tree.columnCount()):
                    item.setFont(column, font)
                    item.setForeground(column, muted_color())
            elif entry.outcome is Outcome.FAILURE:
                item.setForeground(6, error_color())
            if entry.type is JournalEntryType.BREW and not entry.excess.is_empty:
                item.setToolTip(
                    5, _("Лишние эссенции: {excess}").format(excess=entry.excess.format_ru())
                )
            self.tree.addTopLevelItem(item)

    def _selected_entry(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        try:
            return self.app.journal.entry(item.data(0, Qt.ItemDataRole.UserRole))
        except AlchimistError:
            return None

    def _save_recipe(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if (
            entry.type is not JournalEntryType.BREW
            or entry.result is None
            or not entry.result.potion_id
        ):
            QMessageBox.information(
                self, _("Нечего сохранять"), _("Выберите удачную варку с получившимся зельем.")
            )
            return
        try:
            potion, messages = self.app.save_as_recipe(entry.id)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
            return
        if messages:
            QMessageBox.warning(
                self,
                _("Рецепт сохранён с предупреждениями"),
                "\n".join(describe(m) for m in messages),
            )
        else:
            QMessageBox.information(
                self,
                _("Готово"),
                _("Рецепт «{name}» записан.").format(name=potion.name),
            )

    def _undo(self) -> None:
        entry = self._selected_entry()
        undoable = (JournalEntryType.BREW, JournalEntryType.DISTILL)
        if entry is None or entry.type not in undoable or entry.undone:
            return
        distilled = entry.type is JournalEntryType.DISTILL
        answer = QMessageBox.question(
            self,
            _("Отменить разбор") if distilled else _("Отменить варку"),
            _("Вернуть реагенты и забрать полученные эссенции?")
            if distilled
            else _("Вернуть реагенты и убрать полученное зелье?"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if distilled:
                self.app.distilling.undo(entry.id)
            else:
                self.app.brewing.undo_brew(entry.id)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
