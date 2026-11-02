"""Страница «Могу сварить» (FR-5.1–5.5)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.matcher import BrewOption
from alchimist.core.models import (
    KIND_NAMES_RU,
    KIT_NAMES_RU,
    RARITY_NAMES_RU,
    PotionKind,
    Rarity,
)
from alchimist.i18n import _, plural_ru
from alchimist.services.brewing import DEFAULT_MAX_EXCESS, DEFAULT_MAX_PORTIONS
from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog, combination_text
from alchimist.ui_qt.pages.base import Page
from alchimist.ui_qt.theme import (
    bold,
    error_color,
    muted_color,
    on_theme_changed,
    warning_color,
)
from alchimist.ui_qt.widgets.common import (
    FilterBar,
    SearchBox,
    base_text,
    difficulty_tooltip,
    hint_label,
    page_heading,
)
from alchimist.ui_qt.widgets.element_badge import rarity_icon
from alchimist.ui_qt.widgets.flow import FlowLayout, group
from alchimist.ui_qt.widgets.row_buttons import RowButtonsDelegate

#: Значение «не фильтровать» в выпадающих списках, как в «Справочнике».
ANY = "__any__"

ROLE_OPTION = Qt.ItemDataRole.UserRole + 1
ROLE_QUEUE = Qt.ItemDataRole.UserRole + 2
#: Строка варианта: у неё в последней колонке кнопки «Сварить» и «В очередь».
ROLE_ACTIONS = Qt.ItemDataRole.UserRole + 3
#: Номера кнопок в строке варианта.
BUTTON_BREW, BUTTON_QUEUE = 0, 1


class CanBrewPage(Page):
    title = "Могу сварить"
    icon = "⚗"

    def __init__(self, app, bridge, parent: QWidget | None = None) -> None:
        super().__init__(app, bridge, parent)

        header = QHBoxLayout()
        header.addWidget(page_heading(_("Могу сварить")))
        header.addStretch(1)
        self.search = SearchBox(_("Поиск по названию…"))
        self.search.setMaximumWidth(280)
        header.addWidget(self.search)
        self.kits_label = QLabel()
        self.kits_label.setObjectName("hint")
        header.addWidget(self.kits_label)

        # Те же фильтры, что в «Справочнике»: привычки с одного экрана работают
        # на другом. «Рецепт известен» и «скрытые» здесь не нужны — в списке и так
        # только то, что можно сварить.
        self.filters = FilterBar()
        self.filters.add_combo(
            "rarity",
            _("Редкость"),
            [(_("любая"), ANY), *[(RARITY_NAMES_RU[r], r) for r in Rarity]],
            enum_type=Rarity,
        )
        self.filters.add_combo(
            "kind",
            _("Вид"),
            [(_("любой"), ANY), *[(KIND_NAMES_RU[k], k) for k in PotionKind]],
            enum_type=PotionKind,
        )
        self.family_filter = self.filters.add_combo("family", _("Семейство"), [(_("любое"), ANY)])
        self.filters.add_check("market", _("Чёрный рынок"))
        self.filters.add_stretch()
        self.filters.changed.connect(self.refresh)

        # П-8.1 и П-8.2: насколько широко искать. Чем больше, тем дольше пересчёт.
        # С переносом: в одну линию эта строка задавала ширину окна (issue #2).
        limits = FlowLayout(spacing=16)
        sort_label = QLabel(_("Сортировка"))
        self.sort_by = QComboBox()
        for text, key in (
            (_("по редкости"), "rarity"),
            (_("по названию"), "name"),
            (_("по сложности"), "difficulty"),
            (_("по числу варок"), "repeats"),
        ):
            self.sort_by.addItem(text, key)
        self.sort_by.setToolTip(_("В каком порядке показывать зелья"))
        self.sort_by.currentIndexChanged.connect(lambda _i: self.refresh())
        limits.addWidget(group(sort_label, self.sort_by))
        excess_label = QLabel(_("Лишних эссенций не больше"))
        self.max_excess = QSpinBox()
        self.max_excess.setRange(0, 6)
        self.max_excess.setValue(DEFAULT_MAX_EXCESS)
        self.max_excess.setToolTip(_("Каждая лишняя эссенция поднимает сложность на 1 (П-8.1)"))
        self.max_excess.valueChanged.connect(lambda _v: self.refresh())
        limits.addWidget(group(excess_label, self.max_excess))
        portions_label = QLabel(_("Порций не больше"))
        self.max_portions = QSpinBox()
        self.max_portions.setRange(1, 6)
        self.max_portions.setValue(DEFAULT_MAX_PORTIONS)
        self.max_portions.setToolTip(
            _("Каждая порция сверх первой поднимает сложность на 2 (П-8.2)")
        )
        self.max_portions.valueChanged.connect(lambda _v: self.refresh())
        limits.addWidget(group(portions_label, self.max_portions))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            [_("Зелье"), _("Основа и реагенты"), _("Порций"), _("Сл"), _("Повторов"), ""]
        )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        # Иначе последняя колонка тянется, а растягиваться должна колонка с реагентами.
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 260)
        # Кнопки рисуются делегатом, а не создаются виджетами в каждой строке:
        # на сотнях вариантов это в разы быстрее и выглядит так же.
        self.row_buttons = RowButtonsDelegate(
            self.tree,
            [_("Сварить"), _("В очередь")],
            ["", _("Отложить эти реагенты под будущую варку")],
            role=ROLE_ACTIONS,
        )
        self.tree.setItemDelegateForColumn(5, self.row_buttons)
        self.row_buttons.clicked.connect(self._row_button)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIconSize(QSize(10, 10))
        self.tree.setRootIsDecorated(True)
        self.tree.itemDoubleClicked.connect(self._brew_selected)

        self.empty = hint_label(
            _("Пока ничего не сварить. Загляните в «Почти готово» — там видно, чего не хватает.")
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── очередь (FR-12.x) ─────────────────────────────────────────────
        queue_box = QWidget()
        queue_layout = QVBoxLayout(queue_box)
        queue_layout.setContentsMargins(0, 0, 0, 0)

        queue_header = QHBoxLayout()
        self.queue_active = QCheckBox(_("Учитывать очередь"))
        self.queue_active.setToolTip(
            _("Реагенты из очереди считаются занятыми, и весь подбор идёт от остатка")
        )
        self.queue_active.toggled.connect(self._toggle_queue)
        queue_header.addWidget(self.queue_active)
        self.queue_summary = QLabel()
        self.queue_summary.setObjectName("hint")
        queue_header.addWidget(self.queue_summary)
        queue_header.addStretch(1)
        for text, tip, handler in (
            (_("↑"), _("Выше по очереди"), lambda: self._move_queued(-1)),
            (_("↓"), _("Ниже по очереди"), lambda: self._move_queued(1)),
            (_("Убрать"), _("Убрать из очереди"), self._remove_queued),
            (_("Очистить"), _("Очистить очередь целиком"), self._clear_queue),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, h=handler: h())
            queue_header.addWidget(button)
        queue_layout.addLayout(queue_header)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setColumnCount(5)
        self.queue_tree.setHeaderLabels(
            [_("Зелье"), _("Порций"), _("Сл"), _("Основа и реагенты"), _("Состояние")]
        )
        self.queue_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.queue_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 4):
            self.queue_tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.queue_tree.setColumnWidth(0, 260)
        self.queue_tree.setRootIsDecorated(False)
        self.queue_tree.setAlternatingRowColors(True)
        self.queue_tree.itemDoubleClicked.connect(lambda _i, _c: self._brew_queued())
        queue_layout.addWidget(self.queue_tree)

        self.queue_empty = hint_label(
            _(
                "Очередь пуста. Кнопка «В очередь» у варианта откладывает его реагенты, "
                "и список выше пересчитывается от остатка."
            )
        )
        self.queue_empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        queue_layout.addWidget(self.queue_empty)
        # Без этого подсказка о пустой очереди висит посреди панели.
        queue_layout.addStretch(1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.tree, 1)
        top_layout.addWidget(self.empty)
        splitter.addWidget(top)
        splitter.addWidget(queue_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        # Без явных размеров Qt отдаёт очереди высоту одной строки.
        splitter.setSizes([620, 200])
        queue_box.setMinimumHeight(150)
        self.queue_tree.setMinimumHeight(110)

        box = self.layout_box()
        box.addLayout(header)
        box.addWidget(self.filters)
        box.addLayout(limits)
        box.addWidget(splitter, 1)

        brew_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        brew_shortcut.activated.connect(self._brew_selected)
        self.search.focus_shortcut(self)
        self.search.search.connect(lambda _t: self.refresh())

        bridge.inventory_changed.connect(self.invalidate)
        bridge.catalog_changed.connect(self.invalidate)
        bridge.settings_changed.connect(self.invalidate)
        bridge.queue_changed.connect(self.invalidate)
        on_theme_changed(self._forget_actions_width)

    # ── отрисовка ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        super().refresh()
        self._refresh_queue()
        kits = ", ".join(KIT_NAMES_RU[k] for k in self.app.characters.kits)
        self.kits_label.setText(_("Набор: {kits}").format(kits=kits))

        self._refresh_families()
        needle = self.search.text().strip().casefold()
        rarity = self.filters.value("rarity")
        kind = self.filters.value("kind")
        family = self.filters.value("family")
        market = self.filters.value("market")

        rows = [
            row
            for row in self.app.brewing.can_brew(
                max_excess=self.max_excess.value(), max_portions=self.max_portions.value()
            )
            if (not needle or needle in row.potion.name.casefold())
            and (rarity == ANY or row.potion.rarity == rarity)
            and (kind == ANY or row.potion.kind == kind)
            and (family == ANY or row.potion.family == family)
            and (not market or row.potion.is_black_market)
        ]
        rows.sort(key=self._sort_key)

        self.tree.clear()
        self.tree.setColumnWidth(5, self.row_buttons.preferred_width())
        self.empty.setVisible(not rows)
        for row in rows:
            easiest = row.easiest
            parent = QTreeWidgetItem(
                [
                    row.potion.name,
                    "",
                    "",
                    str(easiest.difficulty.total),
                    f"×{row.max_repeats}",
                    "",
                ]
            )
            parent.setToolTip(3, difficulty_tooltip(easiest.difficulty))
            parent.setIcon(0, rarity_icon(row.potion.rarity))
            parent.setFont(0, bold(self.tree.font()))
            parent.setToolTip(0, RARITY_NAMES_RU[row.potion.rarity])
            # В строке показана сложность самого простого варианта, поэтому и
            # действие по ней — он же. Иначе «Сварить» на свёрнутом зелье варило
            # бы не то, чьё число игрок только что прочитал.
            parent.setData(0, ROLE_OPTION, easiest)
            self.tree.addTopLevelItem(parent)

            for option in row.options:
                reagents = f"{option.format_bases_ru()} · {combination_text(option.combination)}"
                if not option.combination.is_exact:
                    reagents += _("   (лишнее: {excess})").format(
                        excess=option.combination.excess.format_ru()
                    )
                child = QTreeWidgetItem(
                    [
                        "",
                        reagents,
                        f"×{option.portions}",
                        str(option.difficulty.total),
                        self._repeats_text(option.repeats),
                        "",
                    ]
                )
                child.setData(0, ROLE_OPTION, option)
                child.setData(0, ROLE_ACTIONS, True)
                child.setToolTip(3, difficulty_tooltip(option.difficulty))
                if not option.combination.is_exact:
                    child.setForeground(3, warning_color())
                child.setToolTip(
                    1,
                    _("Стоимость варианта: {cost}. Набор: {kit}").format(
                        cost=option.combination.cost, kit=KIT_NAMES_RU[option.kit]
                    ),
                )
                parent.addChild(child)
            parent.setExpanded(True)
        self.tree.setVisible(bool(rows))

    # ── кнопки строки ─────────────────────────────────────────────────────
    def _row_button(self, index, number: int) -> None:
        """Щелчок по нарисованной кнопке варианта."""
        item = self.tree.itemFromIndex(index)
        option = item.data(0, ROLE_OPTION) if item is not None else None
        if option is None:
            return
        if number == BUTTON_BREW:
            self._brew(option)
        elif number == BUTTON_QUEUE:
            self._enqueue(option)

    def _forget_actions_width(self) -> None:
        """Тема и шрифт меняют размеры кнопок — колонку надо мерить заново.

        Прибитое число здесь не годится: при другом системном шрифте, масштабе
        экрана или переводе надписи текст на кнопке просто обрезается.
        """
        self.row_buttons.forget_sizes()
        self.invalidate()

    def _sort_key(self, row) -> tuple:
        """Порядок списка. Название всегда добивает ключ, чтобы он был устойчивым."""
        name = row.potion.name.casefold()
        match self.sort_by.currentData():
            case "name":
                return (name,)
            case "difficulty":
                return (row.easiest.difficulty.total, int(row.potion.rarity), name)
            case "repeats":
                return (-row.max_repeats, int(row.potion.rarity), name)
            case _:
                return (int(row.potion.rarity), name)

    def _refresh_families(self) -> None:
        """Список семейств меняется вместе со справочником."""
        families = self.app.catalog.families()
        if self.family_filter.count() - 1 == len(families):
            return
        current = self.family_filter.currentData()
        self.family_filter.blockSignals(True)
        self.family_filter.clear()
        self.family_filter.addItem(_("любое"), ANY)
        for family in families:
            self.family_filter.addItem(family, family)
        self.family_filter.setCurrentIndex(max(0, self.family_filter.findData(current)))
        self.family_filter.blockSignals(False)

    @staticmethod
    def _repeats_text(repeats: int) -> str:
        word = plural_ru(repeats, _("раз"), _("раза"), _("раз"))
        return f"{repeats} {word}"

    # ── очередь (FR-12.x) ─────────────────────────────────────────────────
    def _refresh_queue(self) -> None:
        rows = self.app.queue.rows(
            self.app.inventory.reagent_quantities(),
            self.app.catalog.potion_map(),
            self.app.catalog.ingredient_map(),
        )
        self.queue_active.blockSignals(True)
        self.queue_active.setChecked(self.app.queue.is_active)
        self.queue_active.blockSignals(False)

        ingredients = self.app.catalog.ingredient_map()
        current = self._selected_queue_id()
        self.queue_tree.clear()
        for row in rows:
            entry = row.entry
            reagents = " + ".join(
                f"{ingredients[i].name}×{q}" if q > 1 else ingredients[i].name
                for i, q in sorted(entry.reagent_map().items())
                if i in ingredients
            )
            if row.feasible:
                state = _("отложено")
            else:
                short = ", ".join(
                    f"{ingredients[i].name}×{q}"
                    for i, q in sorted(row.missing.items())
                    if i in ingredients
                )
                state = _("не хватает: {what}").format(what=short)
            item = QTreeWidgetItem(
                [
                    row.potion.name,
                    f"×{row.portions}",
                    str(row.difficulty.total),
                    f"{base_text(self.app, entry.base_key)} · {reagents}",
                    state,
                ]
            )
            item.setData(0, ROLE_QUEUE, entry.id)
            item.setIcon(0, rarity_icon(row.potion.rarity))
            item.setToolTip(2, difficulty_tooltip(row.difficulty))
            if not row.feasible:
                item.setForeground(4, error_color())
            elif not self.app.queue.is_active:
                item.setForeground(0, muted_color())
            self.queue_tree.addTopLevelItem(item)
            if entry.id == current:
                self.queue_tree.setCurrentItem(item)

        self.queue_empty.setVisible(not rows)
        self.queue_tree.setVisible(bool(rows))
        reserved = self.app.brewing.reserved_quantities()
        if reserved:
            total = sum(reserved.values())
            self.queue_summary.setText(_("занято реагентов: {count}").format(count=total))
        else:
            self.queue_summary.setText("")

    def _selected_queue_id(self) -> str | None:
        item = self.queue_tree.currentItem()
        return item.data(0, ROLE_QUEUE) if item else None

    def _enqueue(self, option: BrewOption) -> None:
        self.app.queue.add(
            option.potion.id,
            option.base,
            option.combination.as_map(),
            option.portions,
        )

    def _remove_queued(self) -> None:
        entry_id = self._selected_queue_id()
        if entry_id:
            self.app.queue.remove(entry_id)

    def _move_queued(self, offset: int) -> None:
        entry_id = self._selected_queue_id()
        if entry_id:
            self.app.queue.move(entry_id, offset)

    def _clear_queue(self) -> None:
        if not self.app.queue.entries:
            return
        answer = QMessageBox.question(
            self, _("Очистить очередь"), _("Убрать из очереди все записи?")
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.queue.clear()

    def _toggle_queue(self, active: bool) -> None:
        self.app.queue.set_active(active)

    def _brew_queued(self) -> None:
        """Сварить отложенное: запись уйдёт из очереди сама (FR-12.4)."""
        entry_id = self._selected_queue_id()
        if not entry_id:
            return
        entry = self.app.queue.queue.entry(entry_id)
        if entry is None:
            return
        dialog = BrewDialog(
            self.app,
            entry.base_key,
            entry.reagent_map(),
            self,
            expected=self.app.catalog.potion(entry.potion_id),
        )
        dialog.exec()

    # ── действия ──────────────────────────────────────────────────────────
    def _brew_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        option = item.data(0, ROLE_OPTION)
        if isinstance(option, BrewOption):
            self._brew(option)

    def _brew(self, option: BrewOption) -> None:
        dialog = BrewDialog(
            self.app,
            option.base,
            option.combination.as_map(),
            self,
            expected=option.potion,
        )
        dialog.exec()
