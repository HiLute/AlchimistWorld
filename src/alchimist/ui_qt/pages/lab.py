"""Страница «Лаборатория»: ручной конструктор и разбор на эссенции (FR-7.3–7.5, FR-13.x).

Котёл и экстракт устроены одинаково — набрать реагенты и посмотреть, что выйдет, —
поэтому левая и средняя колонки у них общие, а различается только правая: там либо
список зелий со сложностью броска, либо список эссенций.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.distill import MAX_REAGENTS
from alchimist.core.errors import AlchimistError
from alchimist.core.models import (
    KIT_NAMES_RU,
    Outcome,
    ResultKind,
    as_base_key,
    is_special_base_key,
)
from alchimist.i18n import _, describe
from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog
from alchimist.ui_qt.pages.base import Page
from alchimist.ui_qt.theme import accent_color, muted_color, warning_color
from alchimist.ui_qt.widgets.common import (
    MessageStrip,
    SearchBox,
    difficulty_tooltip,
    fill_base_combo,
    format_difficulty,
    hint_label,
    page_heading,
)
from alchimist.ui_qt.widgets.element_counter import ElementCounters

#: Режимы правой колонки.
BREW, DISTILL = "brew", "distill"


class LabPage(Page):
    """Слева — инвентарь, по центру — котёл, справа — сумма элементов и подсказка."""

    title = "Лаборатория"
    icon = "🧪"
    scrollable = True

    def __init__(self, app, bridge, parent: QWidget | None = None) -> None:
        super().__init__(app, bridge, parent)
        self.cauldron: dict[str, int] = {}

        box = self.layout_box()
        header = QHBoxLayout()
        header.addWidget(page_heading(_("Лаборатория")))
        header.addStretch(1)
        header.addWidget(QLabel(_("Режим")))
        self.mode = QComboBox()
        self.mode.addItem(_("Варка"), BREW)
        self.mode.addItem(_("Дистилляция"), DISTILL)
        self.mode.setToolTip(_("Дистилляция разбирает реагенты на эссенции и зелья не варит (П-9)"))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        header.addWidget(self.mode)
        self.base_label = QLabel(_("Основа"))
        header.addWidget(self.base_label)
        # Три типа основы и особые основы из сумки (П-4.4): см. `_fill_bases`.
        self.base = QComboBox()
        self.base.setToolTip(
            _(
                "Особая основа работает только в рецептах, которые требуют именно её; "
                "её элементы в сумму не идут"
            )
        )
        fill_base_combo(self.base, None, [])
        self.base.currentIndexChanged.connect(lambda _i: self._base_changed())
        header.addWidget(self.base)
        box.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── инвентарь ─────────────────────────────────────────────────────
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(QLabel(_("Инвентарь")))
        self.search = SearchBox(_("Поиск реагента…"))
        self.search.search.connect(lambda _t: self._fill_stock())
        left_box.addWidget(self.search)
        self.stock = QTreeWidget()
        self.stock.setColumnCount(3)
        self.stock.setHeaderLabels([_("Реагент"), _("Элементы"), _("Есть")])
        self.stock.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.stock.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.stock.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.stock.itemDoubleClicked.connect(lambda item, _c: self._add(item))
        left_box.addWidget(self.stock, 1)
        self.add_button = QPushButton(_("Положить в котёл →"))
        self.add_button.clicked.connect(lambda: self._add(self.stock.currentItem()))
        left_box.addWidget(self.add_button)
        splitter.addWidget(left)

        # ── котёл ─────────────────────────────────────────────────────────
        middle = QWidget()
        middle_box = QVBoxLayout(middle)
        middle_box.setContentsMargins(0, 0, 0, 0)
        self.pot_label = QLabel(_("Котёл"))
        middle_box.addWidget(self.pot_label)
        self.pot = QTreeWidget()
        self.pot.setColumnCount(3)
        self.pot.setHeaderLabels([_("Реагент"), _("Элементы"), _("Штук")])
        self.pot.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.pot.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.pot.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.pot.itemDoubleClicked.connect(lambda item, _c: self._remove(item))
        middle_box.addWidget(self.pot, 1)
        pot_buttons = QHBoxLayout()
        remove_button = QPushButton(_("← Убрать"))
        remove_button.clicked.connect(lambda: self._remove(self.pot.currentItem()))
        clear_button = QPushButton(_("Вылить"))
        clear_button.clicked.connect(self._clear)
        pot_buttons.addWidget(remove_button)
        pot_buttons.addWidget(clear_button)
        pot_buttons.addStretch(1)
        middle_box.addLayout(pot_buttons)
        splitter.addWidget(middle)

        # ── сумма и подсказка ─────────────────────────────────────────────
        right = QWidget()
        right.setMinimumWidth(340)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        totals = QGroupBox(_("Сумма элементов"))
        totals_box = QVBoxLayout(totals)
        self.elements = ElementCounters(read_only=True, columns=1)
        totals_box.addWidget(self.elements)
        self.total_label = QLabel()
        self.total_label.setObjectName("hint")
        totals_box.addWidget(self.total_label)
        right_box.addWidget(totals)

        self.panels = QStackedWidget()
        self.panels.addWidget(self._brew_panel())
        self.panels.addWidget(self._distill_panel())
        right_box.addWidget(self.panels, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 4)
        box.addWidget(splitter, 1)
        self.footer = hint_label()
        box.addWidget(self.footer)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self._commit)
        self.search.focus_shortcut(self)
        self._mode_changed()

        bridge.inventory_changed.connect(self.invalidate)
        bridge.catalog_changed.connect(self.invalidate)
        bridge.journal_changed.connect(self.invalidate)
        bridge.settings_changed.connect(self.invalidate)
        bridge.queue_changed.connect(self.invalidate)

    # ── правые панели ─────────────────────────────────────────────────────
    def _brew_panel(self) -> QWidget:
        panel = QWidget()
        panel_box = QVBoxLayout(panel)
        panel_box.setContentsMargins(0, 0, 0, 0)
        candidates = QGroupBox(_("Что получится"))
        candidates_box = QVBoxLayout(candidates)
        self.candidates = QTreeWidget()
        self.candidates.setColumnCount(3)
        self.candidates.setHeaderLabels([_("Зелье"), _("Порций"), _("Сл")])
        self.candidates.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.candidates.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.candidates.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.candidates.setRootIsDecorated(False)
        self.candidates.setAlternatingRowColors(True)
        self.candidates.setMinimumHeight(120)
        self.candidates.itemDoubleClicked.connect(lambda _i, _c: self._brew())
        candidates_box.addWidget(self.candidates)
        panel_box.addWidget(candidates, 1)

        self.hint = MessageStrip()
        panel_box.addWidget(self.hint)
        panel_box.addStretch(0)
        self.brew_button = QPushButton(_("Сварить (Ctrl+Enter)"))
        self.brew_button.clicked.connect(self._brew)
        panel_box.addWidget(self.brew_button)
        return panel

    def _distill_panel(self) -> QWidget:
        panel = QWidget()
        panel_box = QVBoxLayout(panel)
        panel_box.setContentsMargins(0, 0, 0, 0)
        essences = QGroupBox(_("Что выйдет"))
        essences_box = QVBoxLayout(essences)
        self.essences = QTreeWidget()
        self.essences.setColumnCount(2)
        self.essences.setHeaderLabels([_("Эссенция"), _("Штук")])
        self.essences.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.essences.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.essences.setRootIsDecorated(False)
        self.essences.setAlternatingRowColors(True)
        self.essences.setMinimumHeight(120)
        self.essences.itemDoubleClicked.connect(lambda _i, _c: self._distill())
        essences_box.addWidget(self.essences)
        panel_box.addWidget(essences, 1)

        self.distill_hint = MessageStrip()
        panel_box.addWidget(self.distill_hint)
        panel_box.addStretch(0)
        self.distill_button = QPushButton(_("Разобрать (Ctrl+Enter)"))
        self.distill_button.clicked.connect(self._distill)
        panel_box.addWidget(self.distill_button)
        return panel

    # ── режим ─────────────────────────────────────────────────────────────
    @property
    def distilling(self) -> bool:
        return self.mode.currentData() == DISTILL

    def _mode_changed(self, _index: int = 0) -> None:
        """Основа нужна только варке, а разбор ограничен пятью реагентами (П-9.1)."""
        distilling = self.distilling
        self.panels.setCurrentIndex(1 if distilling else 0)
        self.base_label.setVisible(not distilling)
        self.base.setVisible(not distilling)
        self.add_button.setText(
            _("Положить в экстракт →") if distilling else _("Положить в котёл →")
        )
        self.footer.setText(
            _(
                "Дистилляция разбирает положенное на эссенции: сумма по каждой стихии "
                "дробится на самые крупные эссенции, какие влезают (П-9.2)."
            )
            if distilling
            else _(
                "Двойной щелчок кладёт реагент в котёл и вынимает обратно. "
                "Сумма считается на лету. Отложенное в очередь сюда не попадает."
            )
        )
        self.refresh()

    # ── наполнение ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        super().refresh()
        self._fill_bases()
        # Занятое очередью в котёл не кладётся (FR-12.6), как и особая основа под котлом.
        available = self.app.brewing.available_quantities()
        self.cauldron = {
            i: min(q, available.get(i, 0) - self._held(i))
            for i, q in self.cauldron.items()
            if available.get(i, 0) - self._held(i) > 0
        }
        self._fill_stock()
        self._fill_pot()
        self._recalc()

    def _fill_stock(self) -> None:
        needle = self.search.text().strip().casefold()
        self.stock.clear()
        ingredients = self.app.catalog.ingredient_map()
        available = self.app.brewing.available_quantities()
        rows = [
            row
            for row in self.app.inventory.reagent_rows(ingredients)
            if available.get(row.ingredient.id, 0) > 0
        ]
        for row in rows:
            if needle and needle not in row.ingredient.name.casefold():
                continue
            free = available.get(row.ingredient.id, 0)
            left = free - self.cauldron.get(row.ingredient.id, 0) - self._held(row.ingredient.id)
            item = QTreeWidgetItem(
                [row.ingredient.name, row.ingredient.elements.format_ru(), str(left)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row.ingredient.id)
            if left <= 0:
                item.setForeground(0, muted_color())
            self.stock.addTopLevelItem(item)

    def _fill_pot(self) -> None:
        ingredients = self.app.catalog.ingredient_map()
        self.pot.clear()
        for ingredient_id, qty in sorted(self.cauldron.items()):
            ingredient = ingredients.get(ingredient_id)
            if ingredient is None or qty <= 0:
                continue
            item = QTreeWidgetItem([ingredient.name, ingredient.elements.format_ru(), str(qty)])
            item.setData(0, Qt.ItemDataRole.UserRole, ingredient_id)
            self.pot.addTopLevelItem(item)
        if self.distilling:
            self.pot_label.setText(
                _("Дистиллирующий экстракт — {count} из {maximum}").format(
                    count=self._in_pot(), maximum=MAX_REAGENTS
                )
            )
        else:
            self.pot_label.setText(_("Котёл"))

    def _in_pot(self) -> int:
        return sum(self.cauldron.values())

    # ── действия ──────────────────────────────────────────────────────────
    def _add(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        # П-9.1: в экстракт больше пяти реагентов просто не влезает.
        if self.distilling and self._in_pot() >= MAX_REAGENTS:
            return
        ingredient_id = item.data(0, Qt.ItemDataRole.UserRole)
        free = self.app.brewing.available_quantities().get(ingredient_id, 0)
        if self.cauldron.get(ingredient_id, 0) + self._held(ingredient_id) >= free:
            return
        self.cauldron[ingredient_id] = self.cauldron.get(ingredient_id, 0) + 1
        self._fill_stock()
        self._fill_pot()
        self._recalc()

    def _remove(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        ingredient_id = item.data(0, Qt.ItemDataRole.UserRole)
        left = self.cauldron.get(ingredient_id, 0) - 1
        if left > 0:
            self.cauldron[ingredient_id] = left
        else:
            self.cauldron.pop(ingredient_id, None)
        self._fill_stock()
        self._fill_pot()
        self._recalc()

    def _clear(self) -> None:
        self.cauldron.clear()
        self._fill_stock()
        self._fill_pot()
        self._recalc()

    # ── особая основа (П-4.4) ─────────────────────────────────────────────
    def _special_base(self) -> str | None:
        base = as_base_key(self.base.currentData())
        return str(base) if is_special_base_key(base) else None

    def _held(self, ingredient_id: str) -> int:
        """Выбранная основой особая основа занимает одну штуку из сумки."""
        return 1 if self._special_base() == ingredient_id else 0

    def _fill_bases(self) -> None:
        """Особые основы — только те, что есть в сумке и не заняты очередью."""
        available = self.app.brewing.available_quantities()
        specials = [i for i in self.app.catalog.special_bases() if available.get(i.id, 0) > 0]
        fill_base_combo(self.base, self.base.currentData(), specials)

    def _base_changed(self) -> None:
        """Последнюю штуку особой основы нельзя положить и основой, и в котёл."""
        special = self._special_base()
        if special is not None:
            free = self.app.brewing.available_quantities().get(special, 0)
            in_pot = self.cauldron.get(special, 0)
            if in_pot + 1 > free:
                if in_pot > 1:
                    self.cauldron[special] = in_pot - 1
                else:
                    self.cauldron.pop(special, None)
        self._fill_stock()
        self._fill_pot()
        self._recalc()

    def _commit(self) -> None:
        """Ctrl+Enter делает то, что сейчас на виду."""
        self._distill() if self.distilling else self._brew()

    def _recalc(self) -> None:
        self._recalc_distill() if self.distilling else self._recalc_brew()

    # ── варка ─────────────────────────────────────────────────────────────
    def _recalc_brew(self) -> None:
        base = self.base.currentData()
        hint = self.app.brewing.hint(base, self.cauldron)
        self.elements.set_vector(hint.elements)
        self.total_label.setText(_("Всего единиц: {total}").format(total=hint.elements.total))
        self.brew_button.setEnabled(bool(self.cauldron))
        self._fill_candidates(hint)

        if hint.elements.is_empty:
            self.hint.show_text(_("Положите реагенты в котёл."))
            return
        lines: list[str] = []
        if hint.candidates:
            best = hint.candidates[0]
            lines.append(
                f'<span style="color:{accent_color().name()}">'
                + _("Проще всего: {count}× «{name}», {difficulty}").format(
                    count=best.portions,
                    name=best.potion.name,
                    difficulty=format_difficulty(best.difficulty, full=False),
                )
                + "</span>"
            )
        elif self._special_base() is not None:
            # Рецептов на особых основах единицы: без рецепта это почти наверняка провал.
            lines.append(
                f'<span style="color:{warning_color().name()}">'
                + _("Рецепта на этой основе нет — скорее всего провал")
                + "</span>"
            )
        else:
            lines.append(_("Ни один известный рецепт этим не покрыть."))
        if self._special_base() is not None:
            lines.append(_("Элементы особой основы в сумму не идут (П-4.4)."))
        if hint.was_tried:
            last = hint.history[0]
            lines.append(_("Уже пробовали: {result}").format(result=_result_text(last)))
        if not hint.allowed_kits:
            lines.append(
                f'<span style="color:{warning_color().name()}">'
                + _("Выбранные наборы такую варку не разрешают (П-6.3)")
                + "</span>"
            )
        else:
            kits = ", ".join(KIT_NAMES_RU[k] for k in hint.allowed_kits)
            lines.append(_("Разрешает: {kits}").format(kits=kits))
        self.hint.show_text("<br>".join(lines))

    def _fill_candidates(self, hint) -> None:
        """Что выйдет из котла: порции и сложность броска (П-8)."""
        self.candidates.clear()
        for candidate in hint.candidates:
            item = QTreeWidgetItem(
                [candidate.potion.name, f"×{candidate.portions}", str(candidate.difficulty.total)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, candidate.potion.id)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            tip = difficulty_tooltip(candidate.difficulty)
            if not candidate.is_exact:
                tip = (
                    _("Лишние эссенции: {excess}").format(excess=candidate.excess.format_ru())
                    + "\n"
                    + tip
                )
                item.setForeground(2, warning_color())
            item.setToolTip(0, tip)
            item.setToolTip(2, tip)
            self.candidates.addTopLevelItem(item)

    def _selected_candidate(self) -> str | None:
        item = self.candidates.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _brew(self) -> None:
        if not self.cauldron:
            return
        base = self.base.currentData()
        hint = self.app.brewing.hint(base, self.cauldron)
        chosen = self._selected_candidate()
        expected = None
        if chosen:
            expected = self.app.catalog.potion(chosen)
        elif hint.candidates:
            expected = hint.candidates[0].potion
        dialog = BrewDialog(self.app, base, dict(self.cauldron), self, expected=expected)
        if dialog.exec():
            self.cauldron.clear()
            self.refresh()

    # ── дистилляция ───────────────────────────────────────────────────────
    def _recalc_distill(self) -> None:
        preview = self.app.distilling.preview(self.cauldron)
        self.elements.set_vector(preview.plan.elements)
        self.total_label.setText(
            _("Всего единиц: {total}").format(total=preview.plan.elements.total)
        )
        self.distill_button.setEnabled(preview.ok)
        self._fill_essences(preview)

        if not self.cauldron:
            # Про флакон говорим сразу: без него разбора не будет (П-9.6), и лучше
            # узнать об этом до того, как набор собран.
            self.distill_hint.show_text(
                _("Положите в экстракт до {maximum} реагентов. Флаконов в сумке: {flasks}").format(
                    maximum=MAX_REAGENTS, flasks=preview.extract_qty
                )
            )
            return
        if preview.messages and not preview.ok:
            self.distill_hint.show_messages(list(preview.messages))
            return
        lines: list[str] = []
        if preview.plan.broken_down:
            lines.append(
                f'<span style="color:{accent_color().name()}">'
                + _("Одинокая эссенция рассыплется на фосфорицирующие (П-9.5)")
                + "</span>"
            )
        lines.append(_("Выйдет эссенций: {count}").format(count=preview.count))
        if preview.spends_extract and preview.extract is not None:
            lines.append(
                _("Спишется флакон «{name}», останется {left}").format(
                    name=preview.extract.name, left=preview.extract_qty - 1
                )
            )
        # Предупреждение про недостающий флакон уже в `messages` — дописываем под него.
        self.distill_hint.show_messages(list(preview.messages), "<br>".join(lines))

    def _fill_essences(self, preview) -> None:
        self.essences.clear()
        for row in preview.rows:
            item = QTreeWidgetItem([row.ingredient.name, f"×{row.count}"])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            have = self.app.inventory.reagent_qty(row.ingredient.id)
            item.setToolTip(
                0,
                _("{elements}; в сумке уже {have}").format(
                    elements=row.ingredient.elements.format_ru(), have=have
                ),
            )
            self.essences.addTopLevelItem(item)

    def _distill(self) -> None:
        preview = self.app.distilling.preview(self.cauldron)
        if not preview.ok:
            return
        got = ", ".join(f"{row.ingredient.name} ×{row.count}" for row in preview.rows)
        answer = QMessageBox.question(
            self,
            _("Разобрать на эссенции"),
            _("Реагенты исчезнут, в сумке появится: {got}.\nПродолжить?").format(got=got),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.app.distilling.distill(dict(self.cauldron))
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
            return
        self.cauldron.clear()
        self.refresh()


def _result_text(entry) -> str:
    if entry.outcome is Outcome.FAILURE:
        if entry.result is not None and entry.result.kind is ResultKind.JABBERWOCK:
            return _("провал: Бармаглот")
        return _("провал")
    if entry.result is None:
        return _("неизвестно")
    match entry.result.kind:
        case ResultKind.NOTHING:
            return _("ничего не вышло")
        case ResultKind.JABBERWOCK:
            return _("Бармаглот")
        case _:
            return _("получилось зелье")
