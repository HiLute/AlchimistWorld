"""Очередь варок: что игрок собирается сварить (FR-12.x).

Очередь — это план, а не факт. Её реагенты считаются занятыми, и весь подбор по
приложению идёт от остатка: так сразу видно, что если сварить одно, на другое
уже не хватит. Инвентарь при этом не трогается — списание происходит только при
настоящей варке.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

from alchimist.core.elements import ElementVector
from alchimist.core.errors import AlchimistError, ErrorCode, Message
from alchimist.core.models import (
    BaseKey,
    BaseType,
    BrewQueue,
    Ingredient,
    Potion,
    QueueEntry,
    ReagentStack,
    is_special_base_key,
)
from alchimist.core.rules import Difficulty, brew_difficulty
from alchimist.services.events import EventBus, QueueChanged
from alchimist.storage.repository import QueueRepository


def new_entry_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True, slots=True)
class QueueRow:
    """Строка очереди для интерфейса: запись плюс то, что про неё известно."""

    entry: QueueEntry
    potion: Potion
    #: Хватает ли реагентов с учётом записей выше по очереди.
    feasible: bool
    difficulty: Difficulty
    #: Чего не хватает, если не хватает: реагент → сколько недостаёт.
    missing: dict[str, int] = field(default_factory=dict)

    @property
    def portions(self) -> int:
        return self.entry.portions


@dataclass(slots=True)
class QueueService:
    repo: QueueRepository
    bus: EventBus
    _queue: BrewQueue = field(default_factory=BrewQueue)
    _notices: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._queue = self.repo.load()
        self._notices = list(getattr(self.repo, "notices", []) or [])

    @property
    def notices(self) -> list[Message]:
        return list(self._notices)

    # ── чтение ────────────────────────────────────────────────────────────
    @property
    def queue(self) -> BrewQueue:
        return self._queue

    @property
    def entries(self) -> tuple[QueueEntry, ...]:
        return self._queue.entries

    @property
    def is_active(self) -> bool:
        return self._queue.active

    @property
    def is_reserving(self) -> bool:
        """Влияет ли очередь сейчас на подбор."""
        return self._queue.is_reserving

    def reserved(self, have: dict[str, int]) -> dict[str, int]:
        return self._queue.reserved(have)

    def available(self, have: dict[str, int]) -> dict[str, int]:
        return self._queue.available(have)

    # ── запись ────────────────────────────────────────────────────────────
    def _apply(self, queue: BrewQueue) -> None:
        self._queue = queue
        self.repo.save(queue)
        self.bus.publish(QueueChanged())

    def set_active(self, active: bool) -> None:
        """FR-12.5: очередь можно отложить, не разбирая её."""
        if self._queue.active != active:
            self._apply(replace(self._queue, active=active))

    def add(
        self,
        potion_id: str,
        base: BaseKey,
        reagents: dict[str, int],
        portions: int = 1,
        note: str = "",
    ) -> QueueEntry:
        """Ставит в очередь конкретную варку: эти реагенты, эта основа, столько порций.

        Особая основа (П-4.4) тоже резервируется: по одной штуке на порцию.
        """
        picked = {i: q for i, q in reagents.items() if q > 0}
        if not picked:
            raise AlchimistError(ErrorCode.NO_REAGENTS)
        special = is_special_base_key(base)
        entry = QueueEntry(
            id=new_entry_id(),
            potion_id=potion_id,
            base=BaseType.LIQUID if special else base,
            base_ingredient_id=str(base) if special else None,
            reagents=tuple(ReagentStack(i, q) for i, q in sorted(picked.items())),
            portions=max(1, portions),
            note=note,
        )
        self._apply(replace(self._queue, entries=(*self._queue.entries, entry)))
        return entry

    def remove(self, entry_id: str) -> None:
        entries = tuple(e for e in self._queue.entries if e.id != entry_id)
        if len(entries) != len(self._queue.entries):
            self._apply(replace(self._queue, entries=entries))

    def remove_first_for(self, potion_id: str, reagents: dict[str, int] | None = None) -> bool:
        """Убирает запись после настоящей варки (FR-12.4).

        Сначала ищется запись ровно с теми же реагентами — игрок мог поставить в
        очередь несколько разных вариантов одного зелья. Если такой нет, убирается
        первая запись на это зелье.
        """
        picked = {i: q for i, q in (reagents or {}).items() if q > 0}
        exact = next(
            (
                e
                for e in self._queue.entries
                if e.potion_id == potion_id and e.reagent_map() == picked
            ),
            None,
        )
        target = exact or next((e for e in self._queue.entries if e.potion_id == potion_id), None)
        if target is None:
            return False
        self.remove(target.id)
        return True

    def move(self, entry_id: str, offset: int) -> None:
        """Порядок важен: запись выше занимает реагенты раньше."""
        entries = list(self._queue.entries)
        index = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
        if index is None:
            return
        target = max(0, min(len(entries) - 1, index + offset))
        if target == index:
            return
        entries.insert(target, entries.pop(index))
        self._apply(replace(self._queue, entries=tuple(entries)))

    def clear(self) -> None:
        if self._queue.entries:
            self._apply(replace(self._queue, entries=()))

    def set_note(self, entry_id: str, note: str) -> None:
        entries = tuple(
            replace(e, note=note) if e.id == entry_id else e for e in self._queue.entries
        )
        self._apply(replace(self._queue, entries=entries))

    # ── строки для интерфейса ─────────────────────────────────────────────
    def elements_of(self, entry: QueueEntry, ingredients: dict[str, Ingredient]) -> ElementVector:
        total = ElementVector()
        for ingredient_id, qty in entry.reagent_map().items():
            ingredient = ingredients.get(ingredient_id)
            if ingredient is not None:
                total = total + ingredient.elements * qty
        return total

    def rows(
        self,
        have: dict[str, int],
        potions: dict[str, Potion],
        ingredients: dict[str, Ingredient],
    ) -> list[QueueRow]:
        """Очередь с пометкой, на что уже не хватает (FR-12.3).

        Идём по порядку: запись выше занимает реагенты раньше, и та, которой не
        досталось, помечается вместе с недостачей.
        """
        rows: list[QueueRow] = []
        left = dict(have)
        for entry in self._queue.entries:
            potion = potions.get(entry.potion_id)
            if potion is None:
                continue
            needed = entry.needs()
            missing = {i: qty - left.get(i, 0) for i, qty in needed.items() if left.get(i, 0) < qty}
            if not missing:
                for ingredient_id, qty in needed.items():
                    left[ingredient_id] -= qty

            # Лишние эссенции считаются от того же набора, что и при варке (П-8.1).
            excess = 0
            if potion.recipe is not None:
                elements = self.elements_of(entry, ingredients)
                leftover = elements - potion.recipe.elements * entry.portions
                excess = leftover.clamped().total

            rows.append(
                QueueRow(
                    entry=entry,
                    potion=potion,
                    feasible=not missing,
                    difficulty=brew_difficulty(potion.rarity, entry.portions, excess),
                    missing=missing,
                )
            )
        return rows
