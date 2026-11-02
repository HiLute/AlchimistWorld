"""Перевод моделей в JSON и обратно (03 §6.3–6.6)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from alchimist.core.elements import ElementVector
from alchimist.core.errors import ErrorCode, StorageError
from alchimist.core.models import (
    BaseType,
    BrewQueue,
    BrewResult,
    Character,
    Ingredient,
    IngredientCategory,
    Inventory,
    JournalEntry,
    JournalEntryType,
    Kit,
    Outcome,
    Potion,
    PotionKind,
    PotionStack,
    QueueEntry,
    Rarity,
    ReagentStack,
    Recipe,
    ResultKind,
    Role,
)
from alchimist.storage.migrations import CURRENT_VERSIONS


def dumps(data: Any) -> str:
    """UTF-8, ensure_ascii=False, отступ 2, перевод строки в конце (NFR-3)."""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any, path: str = "") -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


def _enum(kind: type, value: Any, default: Any, path: str) -> Any:
    try:
        return kind(value)
    except ValueError as exc:
        raise StorageError(
            ErrorCode.STORAGE_CORRUPT, path=path, reason=f"{value!r}: {exc}"
        ) from exc
    except TypeError:
        return default


# ── Реагенты (03 §6.3) ────────────────────────────────────────────────────────
def ingredient_to_dict(item: Ingredient) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "rarity": int(item.rarity),
        "category": str(item.category.value),
    }
    # Вторая категория бывает только у особой основы — у остальных ключа нет.
    if item.secondary_category is not None:
        data["secondary_category"] = str(item.secondary_category.value)
    data.update(
        {
            "elements": item.elements.to_dict(),
            "habitats": list(item.habitats),
            "description": item.description,
            "hidden": item.hidden,
            "updated_at": _dt(item.updated_at),
        }
    )
    return data


def ingredient_from_dict(data: dict[str, Any], path: str = "") -> Ingredient:
    try:
        return Ingredient(
            id=str(data["id"]),
            name=str(data["name"]),
            rarity=_enum(Rarity, int(data.get("rarity", 1)), Rarity.COMMON, path),
            category=_enum(
                IngredientCategory,
                data.get("category", "other"),
                IngredientCategory.OTHER,
                path,
            ),
            elements=ElementVector.from_dict(data.get("elements") or {}),
            habitats=[str(h) for h in data.get("habitats", [])],
            description=str(data.get("description", "")),
            hidden=bool(data.get("hidden", False)),
            updated_at=_parse_dt(data.get("updated_at"), path),
            secondary_category=data.get("secondary_category") or None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


# ── Зелья (03 §6.4) ───────────────────────────────────────────────────────────
def recipe_to_dict(recipe: Recipe | None) -> dict[str, Any] | None:
    if recipe is None:
        return None
    return {
        "bases": [str(b.value) for b in recipe.sorted_bases()],
        "elements": recipe.elements.to_dict(),
        "required_base_id": recipe.required_base_id,
    }


def recipe_from_dict(data: dict[str, Any] | None, path: str = "") -> Recipe | None:
    if not data:
        return None
    try:
        bases = frozenset(_enum(BaseType, b, BaseType.LIQUID, path) for b in data.get("bases", []))
        return Recipe(
            bases=bases,
            elements=ElementVector.from_dict(data.get("elements") or {}),
            required_base_id=data.get("required_base_id") or None,
        )
    except (TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


def potion_to_dict(item: Potion) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "family": item.family,
        "rarity": int(item.rarity),
        "kind": str(item.kind.value),
        "tags": sorted(item.tags),
        "description_md": item.description_md,
        "recipe": recipe_to_dict(item.recipe),
        "recipe_note": item.recipe_note,
        "hidden": item.hidden,
        "updated_at": _dt(item.updated_at),
    }


def potion_from_dict(data: dict[str, Any], path: str = "") -> Potion:
    try:
        return Potion(
            id=str(data["id"]),
            name=str(data["name"]),
            rarity=_enum(Rarity, int(data.get("rarity", 1)), Rarity.COMMON, path),
            kind=_enum(PotionKind, data.get("kind", "potion"), PotionKind.POTION, path),
            family=data.get("family") or None,
            tags={str(t) for t in data.get("tags", [])},
            description_md=str(data.get("description_md", "")),
            recipe=recipe_from_dict(data.get("recipe"), path),
            recipe_note=data.get("recipe_note") or None,
            hidden=bool(data.get("hidden", False)),
            updated_at=_parse_dt(data.get("updated_at"), path),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


# ── Инвентарь (03 §6.5) ───────────────────────────────────────────────────────
def inventory_to_dict(inventory: Inventory) -> dict[str, Any]:
    return {
        "schema_version": CURRENT_VERSIONS["inventory"],
        "reagents": [
            {"ingredient_id": s.ingredient_id, "qty": s.qty, "note": s.note}
            for s in sorted(inventory.reagents, key=lambda s: s.ingredient_id)
        ],
        "potions": [
            {"potion_id": s.potion_id, "qty": s.qty, "note": s.note}
            for s in sorted(inventory.potions, key=lambda s: s.potion_id)
        ],
    }


def inventory_from_dict(data: dict[str, Any], path: str = "") -> Inventory:
    try:
        return Inventory(
            reagents=tuple(
                ReagentStack(str(r["ingredient_id"]), int(r.get("qty", 0)), str(r.get("note", "")))
                for r in data.get("reagents", [])
            ),
            potions=tuple(
                PotionStack(str(p["potion_id"]), int(p.get("qty", 0)), str(p.get("note", "")))
                for p in data.get("potions", [])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


# ── Очередь варок (03 §6.7) ───────────────────────────────────────────────────
def queue_to_dict(queue: BrewQueue) -> dict[str, Any]:
    return {
        "schema_version": CURRENT_VERSIONS["queue"],
        "active": queue.active,
        "entries": [
            {
                "id": entry.id,
                "potion_id": entry.potion_id,
                "base": str(entry.base.value),
                "reagents": [
                    {"ingredient_id": r.ingredient_id, "qty": r.qty} for r in entry.reagents
                ],
                "portions": entry.portions,
                "note": entry.note,
                "base_ingredient": entry.base_ingredient_id,
            }
            for entry in queue.entries
        ],
    }


def queue_from_dict(data: dict[str, Any], path: str = "") -> BrewQueue:
    try:
        entries = tuple(
            QueueEntry(
                id=str(raw["id"]),
                potion_id=str(raw["potion_id"]),
                base=_enum(BaseType, raw.get("base", "liquid"), BaseType.LIQUID, path),
                reagents=tuple(
                    ReagentStack(str(r["ingredient_id"]), int(r.get("qty", 1)))
                    for r in raw.get("reagents", [])
                ),
                portions=int(raw.get("portions", 1) or 1),
                note=str(raw.get("note", "")),
                base_ingredient_id=raw.get("base_ingredient") or None,
            )
            for raw in data.get("entries", [])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc
    return BrewQueue(entries, bool(data.get("active", True)))


# ── Журнал (03 §6.6) ──────────────────────────────────────────────────────────
def entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": entry.id,
        "ts": entry.ts.isoformat(),
        "type": str(entry.type.value),
    }
    if entry.type is JournalEntryType.BREW:
        data["base"] = str(entry.base.value) if entry.base else None
        # Особая основа (П-4.4) — отдельным ключом: в `reagents` её нет, в сумму она не шла.
        data["base_ingredient"] = entry.base_ingredient_id
        if entry.base_ingredient_id:
            data["base_qty"] = entry.base_qty
        data["reagents"] = [
            {"ingredient_id": r.ingredient_id, "qty": r.qty} for r in entry.reagents
        ]
        data["elements"] = entry.elements.to_dict()
        data["outcome"] = str(entry.outcome.value) if entry.outcome else None
        data["result"] = (
            {
                "kind": str(entry.result.kind.value),
                "potion_id": entry.result.potion_id,
            }
            if entry.result
            else None
        )
        data["yield"] = entry.yield_qty
        data["portions"] = entry.portions
        data["excess"] = entry.excess.to_dict()
        data["difficulty"] = entry.difficulty
    else:
        data["potion_id"] = entry.potion_id
        data["qty"] = entry.qty
        if entry.type in (JournalEntryType.ADJUST, JournalEntryType.DISTILL) and entry.reagents:
            data["reagents"] = [
                {"ingredient_id": r.ingredient_id, "qty": r.qty} for r in entry.reagents
            ]
        if entry.type is JournalEntryType.DISTILL:
            data["elements"] = entry.elements.to_dict()
            data["produced"] = [
                {"ingredient_id": r.ingredient_id, "qty": r.qty} for r in entry.produced
            ]
    data["note"] = entry.note
    data["undone"] = entry.undone
    return data


def entry_from_dict(data: dict[str, Any], path: str = "") -> JournalEntry:
    try:
        entry_type = _enum(JournalEntryType, data.get("type", "brew"), JournalEntryType.BREW, path)
        result_raw = data.get("result")
        result = (
            BrewResult(
                kind=_enum(ResultKind, result_raw.get("kind", "none"), ResultKind.NONE, path),
                potion_id=result_raw.get("potion_id"),
            )
            if isinstance(result_raw, dict)
            else None
        )
        base_raw = data.get("base")
        outcome_raw = data.get("outcome")
        return JournalEntry(
            id=str(data["id"]),
            ts=_parse_dt(data["ts"], path) or datetime.now().astimezone(),
            type=entry_type,
            base=_enum(BaseType, base_raw, None, path) if base_raw else None,
            reagents=tuple(
                ReagentStack(str(r["ingredient_id"]), int(r.get("qty", 1)))
                for r in data.get("reagents", [])
            ),
            elements=ElementVector.from_dict(data.get("elements") or {}),
            outcome=_enum(Outcome, outcome_raw, None, path) if outcome_raw else None,
            result=result,
            yield_qty=int(data.get("yield", 1)),
            portions=int(data.get("portions", 1) or 1),
            excess=ElementVector.from_dict(data.get("excess") or {}),
            difficulty=(int(data["difficulty"]) if data.get("difficulty") is not None else None),
            produced=tuple(
                ReagentStack(str(r["ingredient_id"]), int(r.get("qty", 1)))
                for r in data.get("produced", [])
            ),
            potion_id=data.get("potion_id"),
            qty=int(data.get("qty", 0)),
            note=str(data.get("note", "")),
            undone=bool(data.get("undone", False)),
            base_ingredient_id=data.get("base_ingredient") or None,
            # До 2.6 особая основа уходила одна на варку, сколько бы ни было порций.
            base_qty=int(data.get("base_qty", 1) or 1),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc


# ── Персонаж (03 §6.12) ───────────────────────────────────────────────────────
def character_to_dict(character: Character) -> dict[str, Any]:
    """`id` не пишется: это имя папки профиля, и расходиться им незачем."""
    return {
        "schema_version": CURRENT_VERSIONS["character"],
        "name": character.name,
        "role": str(character.role.value),
        "kits": [str(k.value) for k in character.kits],
        "known_recipes": sorted(character.known_recipes),
        "known_ingredients": sorted(character.known_ingredients or ()),
        "show_unknown": character.show_unknown,
    }


def character_from_dict(data: dict[str, Any], character_id: str, path: str = "") -> Character:
    try:
        kits = []
        for raw in data.get("kits", []):
            try:
                kits.append(Kit(raw))
            except ValueError:
                continue  # набор из будущей версии — пропускаем, как и в settings.toml
        return Character(
            id=character_id,
            name=str(data.get("name") or character_id),
            role=_enum(Role, data.get("role", Role.PLAYER.value), Role.PLAYER, path),
            kits=tuple(kits),
            known_recipes=frozenset(str(i) for i in data.get("known_recipes", [])),
            show_unknown=bool(data.get("show_unknown", True)),
            # Ключа нет у персонажа из версии до 2.5: реагенты ему впишет сервис.
            known_ingredients=(
                None
                if data.get("known_ingredients") is None
                else frozenset(str(i) for i in data["known_ingredients"])
            ),
        )
    except (TypeError, ValueError) as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=path, reason=str(exc)) from exc
