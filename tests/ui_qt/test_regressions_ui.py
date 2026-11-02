"""Найденное на полном разборе кода: то, что видно только в интерфейсе."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QComboBox, QWidget

from alchimist.core.elements import ElementVector as EV
from alchimist.core.models import (
    ALL_BASES,
    Catalog,
    Ingredient,
    IngredientCategory,
    Potion,
    Rarity,
    Recipe,
    ResultKind,
)
from alchimist.services import build_app
from alchimist.ui_qt.main_window import MainWindow


def reagent(ident: str, name: str, rarity: Rarity, **elements: int) -> Ingredient:
    return Ingredient(
        id=ident,
        name=name,
        rarity=rarity,
        category=IngredientCategory.PLANT,
        elements=EV.from_dict(elements),
    )


# ── Сводная строка «Могу сварить» действует по тому, что показала ────────────
@pytest.fixture
def divergent_app(tmp_path, qapp):
    """Случай, где «самый простой» вариант и «самый дешёвый» — разные.

    Двойчатка одна даёт ровно две порции (Сл 12), а единственный способ сварить
    одну порцию — Глыба с довеском на три единицы (Сл 13).
    """
    potion = Potion(
        id="z",
        name="Зелье",
        rarity=Rarity.COMMON,
        recipe=Recipe(ALL_BASES, EV.from_dict({"fire": 1, "water": 1})),
    )
    items = [
        (reagent("dbl", "Двойчатка", Rarity.EPIC, fire=2, water=2), 1),
        (reagent("big", "Глыба", Rarity.LEGENDARY, fire=1, water=1, earth=3), 1),
    ]
    app = build_app(tmp_path)
    app.catalog.replace_all(Catalog([i for i, _q in items], [potion]))
    for ingredient, qty in items:
        app.inventory.set_reagent(ingredient.id, qty)
    return app


def test_can_brew_row_acts_on_the_option_it_shows(divergent_app, qapp) -> None:
    """В строке зелья — сложность самого простого варианта; кнопка варит его же."""
    from alchimist.ui_qt.pages.can_brew import ROLE_OPTION, CanBrewPage

    window = MainWindow(divergent_app)
    window.show()
    qapp.processEvents()
    window.go_to(CanBrewPage)
    page = next(p for p in window.pages if isinstance(p, CanBrewPage))
    page.max_excess.setValue(4)
    qapp.processEvents()

    item = page.tree.topLevelItem(0)
    option = item.data(0, ROLE_OPTION)
    assert item.text(3) == "12"
    assert option.difficulty.total == 12
    assert option.portions == 2
    window.close()


# ── Диалог варки подставляет найденное зелье ─────────────────────────────────
def test_brew_dialog_preselects_the_matching_potion(ui_app, qapp) -> None:
    """Qt отдаёт из `currentData()` строку, и сравнение `is` с `ResultKind` врало.

    Из-за этого диалог оставлял первое зелье по алфавиту, и варка могла уйти
    в журнал и в сумку под чужим именем.
    """
    from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog

    # «Обычный Яд» далеко не первый по алфавиту: подмена была бы заметна.
    potion = ui_app.catalog.potion("obychnyy-yad")
    reagents = {"sopli-trollya": 1, "podlunnukh": 1}
    for ingredient_id, qty in reagents.items():
        ui_app.inventory.set_reagent(ingredient_id, qty)

    base = potion.recipe.sorted_bases()[0]
    hint = ui_app.brewing.hint(base, reagents)
    assert hint.matches and hint.matches[0].id == potion.id

    dialog = BrewDialog(ui_app, base, reagents, None, expected=None)
    qapp.processEvents()
    assert dialog.potion.currentData() == potion.id
    assert dialog.request().potion_id == potion.id
    dialog.deleteLater()


def test_brew_dialog_keeps_the_players_own_choice(ui_app, qapp) -> None:
    """Подстановка не должна перебивать того, кто выбрал зелье сам."""
    from alchimist.core.models import BaseType
    from alchimist.services import build_app
    from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog

    app = build_app(ui_app.paths.root / "campaign")
    app.create_character("GM")
    reagents = {"semya-nochnogo-plameni": 1, "tkanevyy-list": 1}  # три известных зелья
    for ingredient_id, qty in reagents.items():
        app.inventory.set_reagent(ingredient_id, qty)
    dialog = BrewDialog(app, BaseType.EXPLOSIVE, reagents, None, expected=None)
    qapp.processEvents()
    last = dialog.potion.itemData(dialog.potion.count() - 1)
    assert last != dialog.potion.currentData()
    dialog.potion.setCurrentIndex(dialog.potion.count() - 1)
    qapp.processEvents()
    dialog.portions.setValue(1)  # любое изменение пересчитывает окно
    qapp.processEvents()
    assert dialog.potion.currentData() == last
    dialog.deleteLater()


# ── Слияние справочников: «взять чужое» должно доходить до сервиса ───────────
def test_merge_choice_survives_qt(qapp) -> None:
    """`StrEnum` из `currentData()` возвращается обычной строкой."""
    from alchimist.core.models import coerce_enum
    from alchimist.services.exchange import MergeChoice

    combo = QComboBox()
    combo.addItem("оставить моё", MergeChoice.KEEP_MINE)
    combo.addItem("взять чужое", MergeChoice.TAKE_THEIRS)
    combo.setCurrentIndex(1)

    raw = combo.currentData()
    assert raw is not MergeChoice.TAKE_THEIRS  # вот почему нужен coerce_enum
    assert coerce_enum(MergeChoice, raw, MergeChoice.KEEP_MINE) is MergeChoice.TAKE_THEIRS


def test_result_kind_survives_qt(qapp) -> None:
    from alchimist.core.models import coerce_enum

    combo = QComboBox()
    combo.addItem("Известное зелье", ResultKind.KNOWN)
    assert combo.currentData() is not ResultKind.KNOWN
    assert coerce_enum(ResultKind, combo.currentData(), ResultKind.NONE) is ResultKind.KNOWN


# ── Значки тем не залёживаются на диске ──────────────────────────────────────
def test_indicator_cache_follows_the_palette(qapp) -> None:
    """Меняются цвета — меняется и путь, иначе на диске остаются старые значки."""
    from dataclasses import replace

    from alchimist.ui_qt.theme import DARK, LIGHT, indicator_paths

    dark = indicator_paths(DARK)
    light = indicator_paths(LIGHT)
    assert dark.keys() == light.keys()
    assert dark["check_on_plain"] != light["check_on_plain"]

    repainted = indicator_paths(replace(DARK, accent="#00ff00"))
    assert repainted["check_on_plain"] != dark["check_on_plain"]


# ── окно меньше своего минимума (баг-репорт: «у GM плющит выбор персонажа») ───
def test_pages_scroll_instead_of_squashing(window, ui_app, qapp) -> None:
    """Тайловый оконный менеджер или маленький экран дают окну меньше его минимума.

    Без прокрутки Qt сжимал виджеты ниже нужного: у GM (лишняя строка «Это GM…»)
    выбор персонажа в «Настройках» сплющивался до нескольких пикселей, а в
    «Лаборатории» — поля суммы элементов.
    """
    from alchimist.ui_qt.pages.lab import LabPage
    from alchimist.ui_qt.pages.settings import SettingsPage

    ui_app.create_character("GM", seed_catalog=False)
    # Минимум окна больше не держится за высокие страницы: они прокручиваются.
    assert window.minimumSizeHint().height() < 500

    window.setMinimumSize(1, 1)  # как в тайловом менеджере: минимум окна не соблюдается
    window.resize(1180, 520)
    qapp.processEvents()

    window.go_to(SettingsPage)
    qapp.processEvents()
    settings = next(p for p in window.pages if isinstance(p, SettingsPage))
    assert settings.role_label.isVisible()  # та самая строка у GM
    combo = settings.character_combo
    assert combo.height() >= combo.sizeHint().height()

    window.go_to(LabPage)
    qapp.processEvents()
    lab = next(p for p in window.pages if isinstance(p, LabPage))
    for box in lab.elements._boxes.values():
        assert box.height() >= box.minimumSizeHint().height()


# ── issue #3: числа суммы элементов прятались за край поля ────────────────────
def test_read_only_counters_show_their_numbers(qapp) -> None:
    """В Qt 6.12 поле ввода без стрелок под таблицей стилей сжимало текст до пикселя.

    Сумма в «Лаборатории» и в окне варки — только показ, поэтому там подписи, а
    не поля ввода; число должно целиком помещаться в своё поле.
    """
    from PySide6.QtWidgets import QSpinBox

    from alchimist.ui_qt.widgets.element_counter import ElementCounters

    counters = ElementCounters(read_only=True, columns=1)
    counters.set_vector(EV.from_dict({"fire": 2, "magic": 12}))
    counters.show()
    qapp.processEvents()
    assert counters.findChildren(QSpinBox) == []
    assert counters.vector() == EV.from_dict({"fire": 2, "magic": 12})
    for field in counters._boxes.values():
        assert field.contentsRect().width() >= field.fontMetrics().horizontalAdvance(field.text())
    texts = {e.value: f.text() for e, f in counters._boxes.items()}
    assert texts["fire"] == "2" and texts["magic"] == "12" and texts["water"] == "0"


def test_no_buttonless_spin_boxes_anywhere(window, ui_app, qapp) -> None:
    """Ни одного поля ввода без стрелок: в Qt 6.12 их текст не виден."""
    from PySide6.QtWidgets import QAbstractSpinBox

    for page in window.pages:
        window.go_to(type(page))
        qapp.processEvents()
        for box in page.findChildren(QAbstractSpinBox):
            assert box.buttonSymbols() != QAbstractSpinBox.ButtonSymbols.NoButtons, page


# ── issue #2: окно не помещалось на экран 1366×768 ───────────────────────────
def test_window_fits_a_small_screen(window, ui_app, qapp) -> None:
    """Строки фильтров и кнопок переносятся, а не задают ширину всего окна."""
    from PySide6.QtWidgets import QTabWidget

    from alchimist.ui_qt.pages.catalog import CatalogPage

    ui_app.create_character("GM", seed_catalog=False)  # у GM кнопок и фильтров больше
    for page in window.pages:
        window.go_to(type(page))
        qapp.processEvents()
    # Экран из отчёта — 1366 px; запас под рамку окна и панели. Ширина зависит от
    # шрифта (в CI на Windows он заметно крупнее), поэтому порог — само требование,
    # а при падении видно, какая страница самая широкая.
    widths = sorted((p.minimumSizeHint().width(), type(p).__name__) for p in window.pages)
    assert window.minimumSizeHint().width() < 1280, widths[-3:]

    window.setMinimumSize(1, 1)
    window.resize(1000, 700)
    window.go_to(CatalogPage)
    catalog = next(p for p in window.pages if isinstance(p, CatalogPage))
    catalog.findChild(QTabWidget).setCurrentIndex(1)
    qapp.processEvents()
    bar = catalog.potions.filters
    assert bar.height() > bar.sizeHint().height()  # перенеслись на вторую строку
    items = [c for c in bar.children() if isinstance(c, QWidget) and c.isVisible()]
    assert items
    for item in items:
        assert bar.rect().contains(item.geometry()), item  # ничего не обрезано справа
