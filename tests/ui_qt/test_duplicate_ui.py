"""Issue #1: «Дублировать…» в справочнике — новая запись по образцу выбранной."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox, QTabWidget

from alchimist.ui_qt.dialogs.ingredient_dialog import IngredientDialog
from alchimist.ui_qt.dialogs.potion_dialog import PotionDialog
from alchimist.ui_qt.pages.catalog import CatalogPage, copy_name


@pytest.fixture
def boxes(monkeypatch) -> list[str]:
    """Окна-сообщения не ждут нажатия: их заголовки просто записываются."""
    shown: list[str] = []
    for name in ("warning", "information"):
        monkeypatch.setattr(
            QMessageBox, name, lambda _parent, title, _text, *a, **k: shown.append(title)
        )
    return shown


@pytest.fixture
def gm_catalog(window, ui_app, qapp, boxes):
    ui_app.create_character("GM", seed_catalog=False)
    window.go_to(CatalogPage)
    qapp.processEvents()
    return next(p for p in window.pages if isinstance(p, CatalogPage))


def _select(tab, item_id) -> None:
    for row in range(tab.tree.topLevelItemCount()):
        item = tab.tree.topLevelItem(row)
        if item.data(0, 0x0100) == item_id:
            tab.tree.setCurrentItem(item)
            return
    raise AssertionError(f"{item_id} нет в списке")


def _accept(monkeypatch, dialog_class, seen: list) -> None:
    def fake_exec(self) -> bool:
        seen.append(self.windowTitle())
        return True

    monkeypatch.setattr(dialog_class, "exec", fake_exec)


def test_copy_name_skips_taken_names() -> None:
    assert copy_name("Зелье", ["Зелье"]) == "Зелье (копия)"
    assert copy_name("Зелье", ["Зелье", "зелье (КОПИЯ)"]) == "Зелье (копия 2)"
    assert copy_name("Зелье", ["Зелье (копия)", "Зелье (копия 2)"]) == "Зелье (копия 3)"


def test_duplicate_potion_keeps_everything_but_id_and_name(
    gm_catalog, ui_app, monkeypatch, boxes
) -> None:
    gm_catalog.findChild(QTabWidget).setCurrentIndex(1)
    tab = gm_catalog.potions
    assert any(b.text() == "Дублировать…" and b.isVisibleTo(tab) for b in tab.gm_buttons)
    source = next(p for p in ui_app.catalog.catalog.potions if p.recipe is not None)
    _select(tab, source.id)
    seen: list[str] = []
    _accept(monkeypatch, PotionDialog, seen)

    tab.duplicate()
    tab.duplicate()

    assert seen == ["Новое зелье", "Новое зелье"]
    copies = {p.name: p for p in ui_app.catalog.catalog.potions if p.name.startswith(source.name)}
    first, second = copies[f"{source.name} (копия)"], copies[f"{source.name} (копия 2)"]
    for copy in (first, second):
        assert copy.id not in {source.id, ""}
        assert copy.recipe == source.recipe
        assert (copy.rarity, copy.kind, copy.family) == (source.rarity, source.kind, source.family)
    assert ui_app.catalog.raw_potion(source.id) == source  # образец не тронут
    # Тот же рецепт у двух зелий — коллизия П-5.6: GM об этом предупредили.
    assert boxes == ["Сохранено с предупреждениями"] * 2


def test_duplicate_ingredient(gm_catalog, ui_app, monkeypatch) -> None:
    tab = gm_catalog.ingredients
    source = ui_app.catalog.catalog.ingredients[0]
    _select(tab, source.id)
    seen: list[str] = []
    _accept(monkeypatch, IngredientDialog, seen)

    tab.duplicate()

    assert seen == ["Новый реагент"]
    copy = next(i for i in ui_app.catalog.catalog.ingredients if i.name == f"{source.name} (копия)")
    assert copy.id != source.id
    assert (copy.elements, copy.rarity, copy.category) == (
        source.elements,
        source.rarity,
        source.category,
    )


def test_players_cannot_duplicate(window, ui_app, qapp, play_as) -> None:
    play_as()
    window.go_to(CatalogPage)
    qapp.processEvents()
    page = next(p for p in window.pages if isinstance(p, CatalogPage))
    tab = page.ingredients
    assert not any(b.isVisibleTo(tab) for b in tab.gm_buttons)
    before = len(ui_app.catalog.catalog.ingredients)
    tab.duplicate()
    assert len(ui_app.catalog.catalog.ingredients) == before
