"""Миграции формата файлов (NFR-4, 03 §6.10).

`MIGRATIONS[kind]` — цепочка функций `v1 → v2 → …`. При загрузке файл с меньшей
`schema_version` прогоняется по цепочке, а исходник сохраняется как `файл.v1.bak`.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from alchimist.core.errors import ErrorCode, StorageError

#: Текущие версии схем.
CURRENT_VERSIONS: dict[str, int] = {
    "ingredients": 3,
    "potions": 1,
    "inventory": 1,
    "journal": 5,
    "queue": 2,
    "settings": 1,
    "catalog-export": 3,
    "character": 2,
}

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _journal_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Правило о порциях и лишних эссенциях появилось позже журнала (П-8).

    В старых записях варка всегда была на одну порцию и ровно по рецепту,
    поэтому порций там одна, лишнего нет, а сложность неизвестна: тогда её
    не считали, и выдумывать задним числом нечего.
    """
    entries = []
    for entry in data.get("entries", []):
        if entry.get("type") == "brew":
            entry = {
                **entry,
                "portions": entry.get("portions", 1),
                "excess": entry.get("excess", {}),
                "difficulty": entry.get("difficulty"),
            }
        entries.append(entry)
    return {**data, "entries": entries}


def _journal_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Дистилляция — новый тип записи (П-9), старым записям он не нужен.

    Данные не меняются: версия поднята, чтобы приложение постарше не прочитало
    разбор на эссенции как варку — тип записи оно не знает и молча подставит
    свой, а это уже неправда в журнале.
    """
    return data


def _ingredient_v1_to_v2(item: dict[str, Any]) -> dict[str, Any]:
    """Категории «Трава» больше нет: травы — это растения (П-3.3).

    Флаг `is_herb` уходит вместе с ней: травник теперь работает ровно с категорией
    «Растение» (П-6.3). Реагент, у которого флаг стоял не у растения, травнику больше
    не доступен — в данных кампании таких не было.
    """
    item = {key: value for key, value in item.items() if key != "is_herb"}
    if item.get("category") == "herb":
        item["category"] = "plant"
    return item


def _ingredients_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Растения вместо трав (см. `_ingredient_v1_to_v2`).

    Версия поднята ещё и ради категории «Основа» (П-4.4): приложение постарше её не
    знает и на справочнике с особой основой упало бы, а так честно скажет, что формат
    новее, чем оно понимает.
    """
    return {**data, "ingredients": [_ingredient_v1_to_v2(i) for i in data.get("ingredients", [])]}


def _journal_v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """У варки появилась особая основа (`base_ingredient`, П-4.4); старым записям её нет.

    Версия поднята, чтобы приложение постарше не переписало журнал без этого ключа:
    иначе отмена такой варки не вернула бы основу в сумку.
    """
    return data


def _ingredients_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """У особой основы может быть вторая категория (`secondary_category`, П-3.3).

    Данные не меняются: у старых реагентов второй категории нет. Версия поднята,
    чтобы приложение постарше не переписало справочник, молча потеряв её — тогда
    основа-растение перестала бы подходить набору травника.
    """
    return data


def _journal_v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Особая основа теперь тратится по одной на порцию; у варки есть `base_qty`.

    Старые записи не меняются: без ключа читается 1 — до 2.6 основа уходила одна
    на варку, и столько же вернёт отмена. Версия поднята, чтобы приложение постарше
    не переписало журнал без `base_qty`: тогда отмена новой варки на трёх порциях
    вернула бы одну основу вместо трёх.
    """
    return data


def _queue_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Запись очереди может стоять на особой основе (`base_ingredient`, П-4.4).

    Данные не меняются; версия поднята по той же причине, что и у журнала.
    """
    return data


def _character_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Реагенты теперь тоже изучаются (FR-14.9): у персонажа появился `known_ingredients`.

    Ключ здесь не добавляется: список зависит от справочника, а его миграция не
    видит. Без ключа сервис персонажей при загрузке впишет все реагенты справочника —
    до 2.5 персонаж знал их все, и отнимать уже открытое при обновлении нечестно.
    Версия поднята, чтобы 2.4 не переписала файл, молча потеряв изученное.
    """
    return data


#: Цепочки миграций: MIGRATIONS["ingredients"][1] превращает v1 в v2.
MIGRATIONS: dict[str, dict[int, Migration]] = {
    "ingredients": {1: _ingredients_v1_to_v2, 2: _ingredients_v2_to_v3},
    "potions": {},
    "inventory": {},
    "journal": {
        1: _journal_v1_to_v2,
        2: _journal_v2_to_v3,
        3: _journal_v3_to_v4,
        4: _journal_v4_to_v5,
    },
    "queue": {1: _queue_v1_to_v2},
    "settings": {},
    # Файл обмена устроен как справочник: реагенты в нём мигрируют так же.
    "catalog-export": {1: _ingredients_v1_to_v2, 2: _ingredients_v2_to_v3},
    "character": {1: _character_v1_to_v2},
}


def schema_version(data: dict[str, Any]) -> int:
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        raise StorageError(ErrorCode.STORAGE_UNKNOWN_SCHEMA, version=version)
    return version


def migrate(kind: str, data: dict[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    """Прогоняет данные по цепочке до текущей версии.

    Перед первой миграцией исходник копируется в `файл.v<N>.bak`.
    """
    target = CURRENT_VERSIONS[kind]
    version = schema_version(data)
    if version > target:
        raise StorageError(
            ErrorCode.STORAGE_UNKNOWN_SCHEMA,
            path=str(source) if source else None,
            version=version,
            supported=target,
        )
    if version == target:
        return data

    if source is not None and source.exists():
        with suppress(OSError):
            shutil.copy2(source, source.with_suffix(f"{source.suffix}.v{version}.bak"))

    chain = MIGRATIONS[kind]
    while version < target:
        step = chain.get(version)
        if step is None:
            raise StorageError(
                ErrorCode.STORAGE_UNKNOWN_SCHEMA,
                path=str(source) if source else None,
                version=version,
                supported=target,
            )
        data = step(data)
        version += 1
        data["schema_version"] = version
    return data


def load_json(path: Path, kind: str) -> dict[str, Any]:
    """Читает JSON и приводит к текущей схеме.

    Повреждённый файл не роняет приложение (NFR-8): бросается StorageError
    с путём и номером строки, интерфейс предлагает откатиться на `.bak`.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=str(path), reason=str(exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StorageError(
            ErrorCode.STORAGE_CORRUPT,
            path=str(path),
            line=exc.lineno,
            column=exc.colno,
            reason=exc.msg,
        ) from exc
    if not isinstance(data, dict):
        raise StorageError(ErrorCode.STORAGE_CORRUPT, path=str(path), reason="ожидался объект")
    return migrate(kind, data, source=path)
