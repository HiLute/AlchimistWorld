"""Интерфейс показывает порции, лишние эссенции и сложность (П-8)."""

from __future__ import annotations

import pytest

from alchimist.core.models import BaseType, Rarity, ResultKind
from alchimist.core.rules import brew_difficulty
from alchimist.services.brewing import BrewRequest
from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog
from alchimist.ui_qt.pages.can_brew import CanBrewPage
from alchimist.ui_qt.pages.journal import JournalPage
from alchimist.ui_qt.pages.lab import LabPage
from alchimist.ui_qt.widgets.common import difficulty_tooltip, format_difficulty


# ── подписи ──────────────────────────────────────────────────────────────────
def test_difficulty_text() -> None:
    difficulty = brew_difficulty(Rarity.EPIC, portions=2, excess_units=1)
    assert format_difficulty(difficulty) == "Сл 19  =  10 + 6 + 2 + 1"
    assert format_difficulty(difficulty, full=False) == "Сл 19"
    tooltip = difficulty_tooltip(difficulty)
    assert "эпический" in tooltip
    assert "Итого: 19" in tooltip


# ── «Могу сварить» ───────────────────────────────────────────────────────────
def test_can_brew_shows_portions_and_difficulty(window, ui_app) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 4)  # Огонь×1 ×4
    page = next(p for p in window.pages if isinstance(p, CanBrewPage))
    window.go_to(CanBrewPage)

    top = next(
        page.tree.topLevelItem(i)
        for i in range(page.tree.topLevelItemCount())
        if page.tree.topLevelItem(i).text(0) == "Алхимический Огонь"
    )
    assert top.text(3) == "10"  # проще всего — обычное зелье без лишнего

    by_portions = {top.child(i).text(2): top.child(i).text(3) for i in range(top.childCount())}
    assert by_portions["×1"] == "10"
    assert by_portions["×2"] == "12"  # П-8.2: вторая порция +2


def test_can_brew_excess_control(window, ui_app) -> None:
    """П-8.1: с лишними эссенциями варится то, что раньше не собиралось."""
    ui_app.inventory.set_reagent("krov-drakocherepakhi", 1)  # Вода×2, Огонь×1
    ui_app.inventory.set_reagent("shcholkorekh", 1)  # Огонь×1
    page = next(p for p in window.pages if isinstance(p, CanBrewPage))
    window.go_to(CanBrewPage)

    def names() -> set[str]:
        return {page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())}

    page.max_excess.setValue(0)
    assert "Алхимический Огонь" not in names()

    page.max_excess.setValue(2)
    assert "Алхимический Огонь" in names()
    top = next(
        page.tree.topLevelItem(i)
        for i in range(page.tree.topLevelItemCount())
        if page.tree.topLevelItem(i).text(0) == "Алхимический Огонь"
    )
    assert top.text(3) == "12"  # 10 + 0 + 0 + 2
    assert "лишнее" in top.child(0).text(1)


# ── Лаборатория ──────────────────────────────────────────────────────────────
def test_lab_lists_candidates(window, ui_app) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 2)
    ui_app.inventory.set_reagent("svechnaya-roza", 1)
    page = next(p for p in window.pages if isinstance(p, LabPage))
    window.go_to(LabPage)

    for name in ("Щёлкорех", "Щёлкорех", "Свечная Роза"):
        page._add(
            next(
                page.stock.topLevelItem(i)
                for i in range(page.stock.topLevelItemCount())
                if page.stock.topLevelItem(i).text(0) == name
            )
        )
    page.base.setCurrentIndex(1)  # вязкая
    page._recalc()

    rows = {
        page.candidates.topLevelItem(i).text(0): page.candidates.topLevelItem(i)
        for i in range(page.candidates.topLevelItemCount())
    }
    assert "Пламя Саламандры" in rows
    assert rows["Пламя Саламандры"].text(1) == "×1"
    assert rows["Пламя Саламандры"].text(2) == "12"  # необычное, без лишнего
    assert "Проще всего" in page.hint._label.text()


def test_lab_shows_nothing_when_no_recipe_covered(window, ui_app) -> None:
    ui_app.inventory.set_reagent("fanana", 1)  # Воздух×1
    page = next(p for p in window.pages if isinstance(p, LabPage))
    window.go_to(LabPage)
    page._add(page.stock.topLevelItem(0))
    page._recalc()
    assert page.candidates.topLevelItemCount() == 0
    assert "не покрыть" in page.hint._label.text()


# ── Диалог варки ─────────────────────────────────────────────────────────────
def test_brew_dialog_difficulty(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 4)
    dialog = BrewDialog(
        ui_app,
        BaseType.LIQUID,
        {"shcholkorekh": 4},
        expected=ui_app.catalog.potion("alkhimicheskiy-ogon"),
    )
    # На четыре Огня приходится две порции — по умолчанию варим обе: и зелий
    # больше, и бросок не сложнее, чем если бы две единицы ушли в излишек.
    assert dialog.portions.maximum() == 2
    assert dialog.portions.value() == 2
    assert dialog.difficulty_label.text() == "Сл 12  =  10 + 0 + 2 + 0"
    assert dialog.excess_label.text() == "нет"
    assert dialog.request().portions == 2

    # Если игрок сам убавит порции, остаток уйдёт в лишние эссенции.
    dialog.portions.setValue(1)
    assert dialog.difficulty_label.text() == "Сл 12  =  10 + 0 + 0 + 2"
    assert dialog.excess_label.text() == "Огонь×2"
    assert dialog.request().portions == 1


def test_brew_dialog_brews_two_portions(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 4)
    dialog = BrewDialog(
        ui_app,
        BaseType.LIQUID,
        {"shcholkorekh": 4},
        expected=ui_app.catalog.potion("alkhimicheskiy-ogon"),
    )
    assert dialog.portions.value() == 2  # по умолчанию варим всё, на что хватило
    dialog._confirm()

    assert ui_app.inventory.potion_qty("alkhimicheskiy-ogon") == 2
    entry = ui_app.journal.entries()[0]
    assert entry.portions == 2
    assert entry.difficulty == 12


def test_brew_dialog_without_recipe(tmp_path, qapp) -> None:
    """Неизученный рецепт игроку не виден — сложность считать не по чему (FR-14.3)."""
    from alchimist.services import build_app

    app = build_app(tmp_path / "player")
    app.create_character("Айн")
    reagents = {"shchelkorekh": 2, "svechnaya-roza": 1}  # Пламя Саламандры, не изучено
    for ingredient_id, qty in reagents.items():
        app.inventory.set_reagent(ingredient_id, qty)
    dialog = BrewDialog(app, BaseType.VISCOUS, reagents)
    dialog.result_kind.setCurrentIndex(dialog.result_kind.findText("Новое зелье"))
    assert dialog.potion.currentData() == "plamya-salamandry"
    assert "не считается" in dialog.difficulty_label.text()


# ── Журнал ───────────────────────────────────────────────────────────────────
def test_journal_shows_difficulty(window, ui_app) -> None:
    ui_app.inventory.set_reagent("krov-drakocherepakhi", 1)
    ui_app.inventory.set_reagent("shcholkorekh", 1)
    ui_app.brewing.brew(
        BrewRequest(
            base=BaseType.LIQUID,
            reagents={"krov-drakocherepakhi": 1, "shcholkorekh": 1},
            result_kind=ResultKind.KNOWN,
            potion_id="alkhimicheskiy-ogon",
        )
    )
    page = next(p for p in window.pages if isinstance(p, JournalPage))
    window.go_to(JournalPage)
    item = page.tree.topLevelItem(0)
    assert item.text(5) == "12"
    assert "Вода×2" in item.toolTip(5)


@pytest.mark.parametrize("portions,expected", [(1, "10"), (2, "12"), (3, "14")])
def test_journal_difficulty_grows_with_portions(window, ui_app, portions, expected) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 2 * portions)
    ui_app.brewing.brew(
        BrewRequest(
            base=BaseType.LIQUID,
            reagents={"shcholkorekh": 2 * portions},
            result_kind=ResultKind.KNOWN,
            potion_id="alkhimicheskiy-ogon",
            portions=portions,
        )
    )
    page = next(p for p in window.pages if isinstance(p, JournalPage))
    window.go_to(JournalPage)
    assert page.tree.topLevelItem(0).text(5) == expected
