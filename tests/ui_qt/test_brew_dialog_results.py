"""Окно варки: результаты по исходу и только реальные зелья в «Известном» (FR-7.5)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from alchimist.core.models import BaseType, Outcome, ResultKind
from alchimist.services import build_app
from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog

#: Пример из обсуждения: на взрывной основе это даёт Динамит, Дымовую Бомбу и Кислоту.
SEED = {"semya-nochnogo-plameni": 1, "tkanevyy-list": 1}


@pytest.fixture
def campaign(tmp_path, qapp):
    """Справочник кампании и GM: знает все рецепты."""
    app = build_app(tmp_path / "campaign")
    app.create_character("GM")
    for ingredient_id, qty in SEED.items():
        app.inventory.set_reagent(ingredient_id, qty)
    return app


def _items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def test_known_potion_lists_only_what_this_set_makes(campaign) -> None:
    dialog = BrewDialog(campaign, BaseType.EXPLOSIVE, SEED)
    assert _items(dialog.result_kind) == ["Известное зелье", "Новое зелье"]
    assert dialog.result_kind.currentText() == "Известное зелье"
    assert set(_items(dialog.potion)) == {"Динамит", "Дымовая Бомба", "Кислота"}
    # Первым — самый простой бросок, как в подсказке «Совпадает с рецептом».
    hint = campaign.brewing.hint(BaseType.EXPLOSIVE, SEED)
    assert dialog.potion.currentData() == hint.candidates[0].potion.id


def test_gm_has_no_unlearned_recipes_but_can_create(campaign, monkeypatch) -> None:
    """GM знает всё: «Новое зелье» пусто, но новое можно завести «Создать…» и выбрать."""
    from alchimist.core.models import Potion, Rarity
    from alchimist.ui_qt.dialogs.potion_dialog import PotionDialog

    dialog = BrewDialog(campaign, BaseType.EXPLOSIVE, SEED)
    dialog.result_kind.setCurrentIndex(dialog.result_kind.findText("Новое зелье"))
    assert dialog.potion.count() == 0
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and dialog.create_potion.isEnabled()

    monkeypatch.setattr(PotionDialog, "exec", lambda self: True)
    monkeypatch.setattr(
        PotionDialog, "build", lambda self: Potion(id="", name="Взрыв-зелье", rarity=Rarity.COMMON)
    )
    dialog._create_potion()
    assert _items(dialog.potion) == ["Взрыв-зелье"] and ok.isEnabled()


def test_failure_results_and_jabberwock(campaign) -> None:
    dialog = BrewDialog(campaign, BaseType.LIQUID, SEED)
    dialog.failure.setChecked(True)
    assert _items(dialog.result_kind) == ["Ничего не вышло", "Бармаглот"]
    assert not dialog.potion.isEnabled()
    assert not dialog.portions.isEnabled()

    dialog.result_kind.setCurrentIndex(1)
    assert _items(dialog.potion) == ["Бармаглот"] and dialog.potion.isEnabled()
    request = dialog.request()
    assert (request.outcome, request.result_kind, request.potion_id) == (
        Outcome.FAILURE,
        ResultKind.JABBERWOCK,
        "barmaglot",
    )
    dialog._confirm()
    assert campaign.inventory.potion_qty("barmaglot") == 1
    entry = campaign.journal.entries()[0]
    assert entry.outcome is Outcome.FAILURE and entry.result.kind is ResultKind.JABBERWOCK
    assert entry.difficulty is None


def test_nothing_matches_means_no_success(campaign) -> None:
    """Ни один рецепт не покрыт: оба списка пусты, «Сварить» при успехе неактивна."""
    campaign.inventory.set_reagent("tkanevyy-list", 1)
    dialog = BrewDialog(campaign, BaseType.LIQUID, {"tkanevyy-list": 1})
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    for kind in ("Известное зелье", "Новое зелье"):
        dialog.result_kind.setCurrentIndex(dialog.result_kind.findText(kind))
        assert dialog.potion.count() == 0 and not ok.isEnabled(), kind
    dialog.failure.setChecked(True)
    assert ok.isEnabled()  # провал записать можно всегда


def test_unlearned_exact_recipe_is_the_new_potion(tmp_path, qapp, monkeypatch) -> None:
    """Неизученный рецепт, в который попали ровно, — это «Новое зелье» (FR-14.7)."""
    app = build_app(tmp_path / "player")
    app.create_character("Айн")
    reagents = {"shchelkorekh": 2, "svechnaya-roza": 1}  # Пламя Саламандры, вязкая
    for ingredient_id, qty in reagents.items():
        app.inventory.set_reagent(ingredient_id, qty)
    dialog = BrewDialog(app, BaseType.VISCOUS, reagents)
    assert "plamya-salamandry" not in [
        dialog.potion.itemData(i) for i in range(dialog.potion.count())
    ]  # в «Известном» его нет: рецепт не изучен
    dialog.result_kind.setCurrentIndex(dialog.result_kind.findText("Новое зелье"))
    assert _items(dialog.potion) == ["Пламя Саламандры"]
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _p, title, _t, *a, **k: asked.append(title) or QMessageBox.StandardButton.Yes,
    )
    dialog._confirm()
    assert app.inventory.potion_qty("plamya-salamandry") == 1
    assert asked == ["Открыт рецепт"]
    assert app.characters.active.knows("plamya-salamandry")
