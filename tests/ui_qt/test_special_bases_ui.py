"""Особые основы в интерфейсе (П-4.4)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from alchimist.core.elements import ElementVector as EV
from alchimist.core.models import (
    BaseType,
    Ingredient,
    IngredientCategory,
    Potion,
    Rarity,
    Recipe,
)
from alchimist.ui_qt.dialogs.potion_dialog import PotionDialog
from alchimist.ui_qt.pages.almost import AlmostPage
from alchimist.ui_qt.pages.can_brew import CanBrewPage
from alchimist.ui_qt.pages.lab import LabPage

BLOOD = "krov-vampira"
CURE = "lekarstvo"


@pytest.fixture
def vampire(ui_app):
    ui_app.add_ingredient(
        Ingredient(
            id="",
            name="Кровь вампира",
            rarity=Rarity.RARE,
            category=IngredientCategory.BASE,
            elements=EV.from_dict({"dark": 1, "magic": 1}),
        )
    )
    ui_app.add_potion(
        Potion(
            id="",
            name="Лекарство",
            rarity=Rarity.UNCOMMON,
            recipe=Recipe(frozenset(), EV.from_dict({"light": 2, "water": 1}), BLOOD),
        )
    )
    return ui_app


def page_of(window, page_class):
    window.go_to(page_class)
    return next(p for p in window.pages if isinstance(p, page_class))


def stock_left(page, ingredient_id: str) -> int | None:
    for row in range(page.stock.topLevelItemCount()):
        item = page.stock.topLevelItem(row)
        if item.data(0, Qt.ItemDataRole.UserRole) == ingredient_id:
            return int(item.text(2))
    return None


def select_base(page, base) -> None:
    page.base.setCurrentIndex(page.base.findData(base))


def test_lab_offers_special_base_only_from_the_bag(vampire, window) -> None:
    page = page_of(window, LabPage)
    assert page.base.findData(BLOOD) < 0  # в сумке нет — и в списке нет

    vampire.inventory.set_reagent(BLOOD, 2)
    page = page_of(window, LabPage)
    assert page.base.findData(BLOOD) >= 0
    assert stock_left(page, BLOOD) == 2

    select_base(page, BLOOD)
    assert stock_left(page, BLOOD) == 1  # одна штука ушла под основу


def test_lab_last_piece_leaves_the_cauldron_when_chosen_as_base(vampire, window) -> None:
    vampire.inventory.set_reagent(BLOOD, 1)
    page = page_of(window, LabPage)
    page.cauldron = {BLOOD: 1}
    select_base(page, BLOOD)
    assert BLOOD not in page.cauldron
    assert stock_left(page, BLOOD) == 0


def test_lab_warns_without_recipe_on_special_base(vampire, window) -> None:
    vampire.inventory.set_reagent(BLOOD, 1)
    vampire.inventory.set_reagent("shcholkorekh", 1)
    page = page_of(window, LabPage)
    select_base(page, BLOOD)
    page.cauldron = {"shcholkorekh": 1}
    page._recalc()
    text = page.hint._label.text()
    assert "скорее всего провал" in text
    assert "в сумму не идут" in text


def test_lab_finds_recipe_on_special_base(vampire, window) -> None:
    for ingredient_id in (BLOOD, "svechnaya-roza", "mylnaya-trava", "sopli-trollya"):
        vampire.inventory.set_reagent(ingredient_id, 1)
    page = page_of(window, LabPage)
    select_base(page, BLOOD)
    page.cauldron = {"svechnaya-roza": 1, "mylnaya-trava": 1, "sopli-trollya": 1}
    page._recalc()
    names = [
        page.candidates.topLevelItem(i).text(0) for i in range(page.candidates.topLevelItemCount())
    ]
    assert "Лекарство" in names
    assert "скорее всего провал" not in page.hint._label.text()


def test_can_brew_shows_special_base_name(vampire, window) -> None:
    for ingredient_id in (BLOOD, "svechnaya-roza", "mylnaya-trava", "sopli-trollya"):
        vampire.inventory.set_reagent(ingredient_id, 1)
    page = page_of(window, CanBrewPage)
    texts = []
    for row in range(page.tree.topLevelItemCount()):
        parent = page.tree.topLevelItem(row)
        if parent.text(0) == "Лекарство":
            texts = [parent.child(i).text(1) for i in range(parent.childCount())]
    assert texts and texts[0].startswith("Кровь вампира · ")


def test_almost_ready_says_which_base_is_missing(vampire, window) -> None:
    for ingredient_id in ("svechnaya-roza", "mylnaya-trava", "sopli-trollya"):
        vampire.inventory.set_reagent(ingredient_id, 1)
    page = page_of(window, AlmostPage)
    rows = {
        page.tree.topLevelItem(i).text(0): page.tree.topLevelItem(i)
        for i in range(page.tree.topLevelItemCount())
    }
    assert "Лекарство" in rows
    assert "основа «Кровь вампира»" in rows["Лекарство"].text(1)


def test_recipe_editor_saves_special_base(vampire, qapp) -> None:
    dialog = PotionDialog(vampire, vampire.catalog.potion(CURE))
    editor = dialog.recipe_editor
    assert editor.special.currentData() == BLOOD
    assert not any(c.isEnabled() for c in editor.bases.values())  # обычные основы не нужны
    assert dialog.build().recipe.required_base_id == BLOOD
    assert dialog.messages.isHidden() or "ни одна основа" not in dialog.messages._label.text()

    editor.special.setCurrentIndex(0)  # «нет — обычные основы»
    editor.bases[BaseType.LIQUID].setChecked(True)
    recipe = dialog.build().recipe
    assert recipe.required_base_id is None and recipe.bases == frozenset({BaseType.LIQUID})


def test_brew_dialog_caps_portions_by_bases(vampire, qapp) -> None:
    """Реагентов на три порции, крови две: больше двух порций выбрать нельзя."""
    from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog

    reagents = {"svechnaya-roza": 3, "mylnaya-trava": 3, "sopli-trollya": 3}
    for ingredient_id, qty in reagents.items():
        vampire.inventory.set_reagent(ingredient_id, qty)
    vampire.inventory.set_reagent(BLOOD, 2)

    dialog = BrewDialog(vampire, BLOOD, reagents)
    index = dialog.potion.findData(CURE)
    dialog.potion.setCurrentIndex(index)
    qapp.processEvents()
    assert dialog.portions.maximum() == 2
    assert dialog.portions.value() == 2

    # При провале порции тоже важны: основы уходят по одной на порцию.
    dialog.failure.setChecked(True)
    qapp.processEvents()
    assert dialog.portions.isEnabled()
    assert dialog.request().portions == 2
    dialog.close()
