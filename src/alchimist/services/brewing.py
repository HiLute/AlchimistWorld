"""«Могу сварить», «Почти готово», варка и лаборатория (FR-5.x, 6.x, 7.x)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from alchimist.core.elements import ElementVector
from alchimist.core.errors import AlchimistError, ErrorCode, Message
from alchimist.core.matcher import (
    AlmostReady,
    BrewablePotion,
    StockItem,
    almost_ready,
    brewable,
    portions_and_excess,
    stock_from,
)
from alchimist.core.models import (
    BaseKey,
    BrewResult,
    Ingredient,
    JournalEntry,
    JournalEntryType,
    Kit,
    Outcome,
    Potion,
    ReagentStack,
    Recipe,
    ResultKind,
    as_base_key,
    coerce_enum,
    is_special_base_key,
)
from alchimist.core.rules import (
    Difficulty,
    brew_difficulty,
    kit_allows_base,
    kit_allows_ingredient,
    kit_allows_potion,
)
from alchimist.services.catalog import CatalogService
from alchimist.services.events import EventBus, InventoryChanged, JournalChanged
from alchimist.services.inventory import InventoryService
from alchimist.services.journal import JournalService, new_id, now
from alchimist.services.queue import QueueService
from alchimist.storage.atomic import abort_all, commit_all

#: Значения по умолчанию для экрана «Могу сварить» (П-8.1, П-8.2). Их можно
#: поднять на самом экране: чем шире поиск, тем дольше пересчёт на большой сумке.
DEFAULT_MAX_EXCESS = 2
DEFAULT_MAX_PORTIONS = 3


@dataclass(frozen=True, slots=True)
class BrewCandidate:
    """Что получится из набранного: зелье, порции, лишнее и сложность (П-8)."""

    potion: Potion
    portions: int
    excess: ElementVector
    difficulty: Difficulty

    @property
    def is_exact(self) -> bool:
        """Ровное совпадение — как было до правила о лишних эссенциях."""
        return self.excess.is_empty


@dataclass(frozen=True, slots=True)
class LabHint:
    """Подсказка конструктора (FR-7.4, П-8)."""

    elements: ElementVector
    #: Что из этого получится, от самого простого броска к самому сложному.
    candidates: tuple[BrewCandidate, ...] = ()
    #: Записи журнала с той же комбинацией (П-7.6).
    history: tuple[JournalEntry, ...] = ()
    #: Разрешена ли такая варка выбранными наборами.
    allowed_kits: tuple[Kit, ...] = ()

    @property
    def matches(self) -> tuple[Potion, ...]:
        """Зелья, чей рецепт покрыт набранным."""
        return tuple(c.potion for c in self.candidates)

    @property
    def exact(self) -> tuple[BrewCandidate, ...]:
        """Те, что сходятся ровно, без лишнего."""
        return tuple(c for c in self.candidates if c.is_exact)

    @property
    def best(self) -> BrewCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def is_known(self) -> bool:
        return bool(self.candidates)

    @property
    def was_tried(self) -> bool:
        return bool(self.history)


@dataclass(frozen=True, slots=True)
class BrewRequest:
    """Что уходит в диалог варки (FR-7.1)."""

    #: Тип основы или id особой основы (П-4.4).
    base: BaseKey
    reagents: dict[str, int]
    outcome: Outcome = Outcome.SUCCESS
    result_kind: ResultKind = ResultKind.KNOWN
    potion_id: str | None = None
    #: Сколько порций варится за раз (П-8.2). Столько же зелий и получится.
    portions: int = 1
    yield_qty: int = 1
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", as_base_key(self.base))
        object.__setattr__(self, "outcome", coerce_enum(Outcome, self.outcome, Outcome.SUCCESS))
        object.__setattr__(
            self, "result_kind", coerce_enum(ResultKind, self.result_kind, ResultKind.NONE)
        )

    def with_(self, **changes: object) -> BrewRequest:
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class BrewOutcome:
    """Что получилось: запись журнала и предупреждения."""

    entry: JournalEntry
    messages: tuple[Message, ...] = ()


@dataclass(slots=True)
class BrewingService:
    """Связывает справочник, инвентарь и журнал.

    Варка меняет два файла, поэтому оба `.tmp` готовятся заранее и заменяются
    подряд: это и есть атомарность из FR-7.2 (03 §6.2).
    """

    catalog: CatalogService
    inventory: InventoryService
    journal: JournalService
    bus: EventBus
    #: Очередь варок: её реагенты считаются занятыми (FR-12.2).
    queue: QueueService | None = None
    kits: tuple[Kit, ...] = (Kit.ALCHEMIST,)
    _cache: dict[str, object] = field(default_factory=dict)

    def set_kits(self, kits: tuple[Kit, ...]) -> None:
        self.kits = tuple(kits) or (Kit.ALCHEMIST,)
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()

    # ── подготовка входа подбора ──────────────────────────────────────────
    def available_quantities(self) -> dict[str, int]:
        """Что свободно: инвентарь минус занятое очередью (FR-12.2).

        Весь подбор считает от этого числа, а не от инвентаря, поэтому сразу
        видно: если отложить одно, на другое уже может не хватить.
        """
        have = self.inventory.reagent_quantities()
        if self.queue is None or not self.queue.is_reserving:
            return have
        return self.queue.available(have)

    def reserved_quantities(self) -> dict[str, int]:
        """Сколько каждого реагента занято очередью."""
        if self.queue is None or not self.queue.is_reserving:
            return {}
        return self.queue.reserved(self.inventory.reagent_quantities())

    def stock(self) -> list[StockItem]:
        return stock_from(self.catalog.ingredient_map(), self.available_quantities())

    # ── FR-5.x «Могу сварить» ─────────────────────────────────────────────
    def can_brew(
        self, max_excess: int | None = None, max_portions: int | None = None
    ) -> list[BrewablePotion]:
        """Что можно сварить. По умолчанию — с лишними эссенциями и порциями (П-8)."""
        excess = DEFAULT_MAX_EXCESS if max_excess is None else max(0, max_excess)
        portions = DEFAULT_MAX_PORTIONS if max_portions is None else max(1, max_portions)
        key = f"can_brew:{excess}:{portions}"
        cached = self._cache.get(key)
        if cached is None:
            cached = brewable(
                self.catalog.potions(),
                self.stock(),
                self.kits,
                max_excess=excess,
                max_portions=portions,
            )
            self._cache[key] = cached
        return cached  # type: ignore[return-value]

    # ── FR-6.x «Почти готово» ─────────────────────────────────────────────
    def almost(self, max_missing: int | None = None) -> list[AlmostReady]:
        key = f"almost:{max_missing}"
        cached = self._cache.get(key)
        if cached is None:
            cached = almost_ready(
                self.catalog.potions(),
                self.stock(),
                self.kits,
                # Чем закрыть недостачу — только тем, что персонаж знает (FR-14.11).
                self.catalog.known_ingredients_list(),
                brewable_ids=[row.potion.id for row in self.can_brew()],
                max_missing=max_missing,
            )
            self._cache[key] = cached
        return cached  # type: ignore[return-value]

    # ── FR-7.3, 7.4 Лаборатория ───────────────────────────────────────────
    def combination_elements(self, reagents: dict[str, int]) -> ElementVector:
        """Живой пересчёт суммы элементов (FR-7.3)."""
        ingredients = self.catalog.ingredient_map()
        total = ElementVector()
        for ingredient_id, qty in reagents.items():
            ingredient = ingredients.get(ingredient_id)
            if ingredient and qty > 0:
                total = total + ingredient.elements * qty
        return total

    def candidates_for(
        self,
        base: BaseKey,
        elements: ElementVector,
        limit: int = 12,
        *,
        max_portions: int | None = None,
    ) -> list[BrewCandidate]:
        """Что получится из этой суммы на этой основе (П-8.1, П-8.2).

        С правилом о лишних эссенциях подходит любое зелье, чей рецепт покрыт
        набранным: лишнее просто поднимает сложность. Перебирать нечего —
        достаточно пройти по рецептам и посчитать порции с остатком.

        `max_portions` — потолок порций: на особой основе их не больше, чем основ.
        """
        found: list[BrewCandidate] = []
        for potion in self.catalog.potions():
            recipe = potion.recipe
            # Особая основа подходит только рецептам, которые требуют именно её (П-4.4),
            # а рецепты на особой основе не варятся на обычной.
            if recipe is None or base not in recipe.base_keys():
                continue
            portions, excess = portions_and_excess(elements, recipe.elements)
            if portions <= 0:
                continue
            if max_portions is not None and portions > max_portions:
                portions = max(1, max_portions)
                excess = elements - recipe.elements * portions
            found.append(
                BrewCandidate(
                    potion,
                    portions,
                    excess,
                    brew_difficulty(potion.rarity, portions, excess.total),
                )
            )
        # При равной сложности лучше то, где нет лишнего и выходит больше порций.
        found.sort(
            key=lambda c: (
                c.difficulty.total,
                c.excess.total,
                -c.portions,
                c.potion.rarity,
                c.potion.name,
            )
        )
        return found[:limit]

    def hint(self, base: BaseKey | None, reagents: dict[str, int]) -> LabHint:
        """Что выйдет, чем это грозит и не пробовали ли уже (FR-7.4, П-8)."""
        elements = self.combination_elements(reagents)
        if base is None or elements.is_empty:
            return LabHint(elements)
        limit = self.special_base_limit(base, reagents)
        candidates = tuple(self.candidates_for(base, elements, max_portions=limit))
        history = tuple(self.journal.combination_history(base, elements))
        matches = tuple(c.potion for c in candidates)
        return LabHint(elements, candidates, history, self._allowed_kits(base, reagents, matches))

    def _allowed_kits(
        self, base: BaseKey, reagents: dict[str, int], matches: tuple[Potion, ...]
    ) -> tuple[Kit, ...]:
        """Какие наборы разрешают такую варку целиком (03 §4.3)."""
        ingredients = self.catalog.ingredient_map()
        allowed: list[Kit] = []
        for kit in self.kits:
            if not kit_allows_base(kit, base):
                continue
            if any(
                not kit_allows_ingredient(kit, ingredients[i]) for i in reagents if i in ingredients
            ):
                continue
            if matches and not any(kit_allows_potion(kit, p) for p in matches):
                continue
            allowed.append(kit)
        return tuple(allowed)

    def special_base_limit(self, base: BaseKey | None, reagents: dict[str, int]) -> int | None:
        """На сколько порций хватит особой основы (по одной на порцию). None — основа обычная.

        Штуки той же основы, положенные в котёл реагентами, основой уже не будут.
        """
        if not is_special_base_key(base):
            return None
        special = str(base)
        return max(0, self.inventory.reagent_qty(special) - reagents.get(special, 0))

    def max_portions_for(self, potion_id: str, reagents: dict[str, int] | None = None) -> int:
        """Сколько порций даёт набранное (или весь инвентарь, если ничего не задано)."""
        potion = self.catalog.potion(potion_id)
        if potion.recipe is None:
            return 0
        if reagents is None:
            elements = self.inventory.element_summary(self.catalog.ingredient_map())
        else:
            elements = self.combination_elements(reagents)
        return potion.recipe.elements.max_repeats_in(elements)

    # ── FR-7.1, 7.2 Варка ─────────────────────────────────────────────────
    def preview(self, request: BrewRequest) -> LabHint:
        return self.hint(request.base, request.reagents)

    def brew(self, request: BrewRequest) -> BrewOutcome:
        """Списывает реагенты, при успехе добавляет зелье, пишет журнал. Атомарно."""
        if request.base is None:
            raise AlchimistError(ErrorCode.NO_BASE)
        reagents = {i: q for i, q in request.reagents.items() if q > 0}
        if not reagents:
            raise AlchimistError(ErrorCode.NO_REAGENTS)

        special = request.base if is_special_base_key(request.base) else None
        elements = self.combination_elements(reagents)
        # Нехватка реагентов — первое, о чём надо сказать: дальше нет смысла считать.
        self.inventory.consume_preview(reagents)

        # Потом — сколько порций на самом деле варится: от этого зависит,
        # сколько особой основы уйдёт.
        portions = max(1, request.portions)
        excess = ElementVector()
        difficulty: int | None = None
        result = BrewResult(ResultKind.NONE)
        made = None
        # Бармаглот — исход провала (П-5.9), но зелье всё равно появляется в сумке.
        jabberwock = request.result_kind is ResultKind.JABBERWOCK
        if request.outcome is Outcome.SUCCESS or jabberwock:
            result = BrewResult(request.result_kind, request.potion_id)
            if request.result_kind in (ResultKind.KNOWN, ResultKind.NEW, ResultKind.JABBERWOCK):
                if not request.potion_id:
                    raise AlchimistError(ErrorCode.NOT_FOUND, id=None)
                made = self.catalog.potion(request.potion_id)
                # Сколько порций вышло и что осталось лишним — по рецепту (П-8).
                if made.recipe is not None and request.outcome is Outcome.SUCCESS:
                    possible, _leftover = portions_and_excess(elements, made.recipe.elements)
                    if possible > 0:
                        portions = max(1, min(portions, possible))
                        excess = elements - made.recipe.elements * portions
                    difficulty = brew_difficulty(made.rarity, portions, excess.total).total

        # Особая основа (П-4.4) списывается из сумки по одной на порцию — и при
        # провале тоже: на сколько порций варили, столько и ушло. В сумму элементов
        # она не идёт: `elements` считаются только по реагентам котла.
        consumed = dict(reagents)
        if special is not None:
            base_ingredient = self.catalog.ingredient(special)
            if not base_ingredient.is_special_base:
                raise AlchimistError(ErrorCode.NOT_FOUND, id=special)
            consumed[special] = consumed.get(special, 0) + portions
            if self.inventory.reagent_qty(special) < consumed[special]:
                raise AlchimistError(
                    ErrorCode.NO_SPECIAL_BASE, name=base_ingredient.name, id=special
                )
        inventory = self.inventory.consume_preview(consumed)

        potion_ids: tuple[str, ...] = ()
        yield_qty = 0
        if made is not None:
            # Одна варка даёт столько зелий, сколько порций, если не сказано иное;
            # Бармаглот — один, на сколько порций ни варили бы.
            default_yield = 1 if jabberwock else portions
            yield_qty = max(1, request.yield_qty if request.yield_qty > 1 else default_yield)
            have = inventory.potion_qty(made.id)
            inventory = self.inventory.with_potion(inventory, made.id, have + yield_qty, None)
            potion_ids = (made.id,)

        entry = JournalEntry(
            id=new_id(),
            ts=now(),
            type=JournalEntryType.BREW,
            base=None if special else request.base,
            base_ingredient_id=special,
            base_qty=portions if special else 1,
            reagents=tuple(ReagentStack(i, q) for i, q in sorted(reagents.items())),
            elements=elements,
            outcome=request.outcome,
            result=result,
            yield_qty=yield_qty,
            portions=portions,
            excess=excess,
            difficulty=difficulty,
            note=request.note,
        )

        # Оба файла готовятся до подмены: если сорвётся, инвентарь не тронут (03 §6.2).
        entries = [*self.journal.entries(newest_first=False), entry]
        writes = [
            self.inventory.prepare_write(inventory),
            self.journal.prepare_write(entries),
        ]
        try:
            commit_all(writes)
        except OSError:
            abort_all(writes)
            raise

        self.inventory.adopt(inventory)
        self.journal.adopt(entries)
        self.invalidate()
        self.bus.publish(InventoryChanged(tuple(consumed), potion_ids))
        self.bus.publish(JournalChanged((entry.id,)))

        # Сварили запланированное — запись из очереди уходит (FR-12.4).
        if self.queue is not None and request.potion_id:
            self.queue.remove_first_for(request.potion_id, reagents)

        messages: list[Message] = []
        if request.potion_id:
            potion = self.catalog.potion(request.potion_id)
            messages = self.catalog.potion_warnings(potion)
        return BrewOutcome(entry, tuple(messages))

    # ── FR-4.2 Использование зелья ────────────────────────────────────────
    def use_potion(self, potion_id: str, qty: int = 1, note: str = "") -> JournalEntry:
        self.inventory.take_potion(potion_id, qty)
        self.invalidate()
        return self.journal.record_use(potion_id, qty, note)

    # ── П-7.5 «Сохранить как рецепт» ──────────────────────────────────────
    def save_as_recipe(
        self, entry_id: str, potion_id: str | None = None
    ) -> tuple[Potion, list[Message]]:
        """Комбинация из журнала записывается в рецепт зелья. Срабатывает П-5.6."""
        entry = self.journal.entry(entry_id)
        target = potion_id or (entry.result.potion_id if entry.result else None)
        if not target or entry.base_key is None or entry.elements.is_empty:
            raise AlchimistError(ErrorCode.NOT_FOUND, id=entry_id)
        if entry.base_ingredient_id:
            # Варили на особой основе (П-4.4) — рецепт и будет требовать именно её.
            recipe = Recipe(
                bases=frozenset(),
                elements=entry.elements,
                required_base_id=entry.base_ingredient_id,
            )
        else:
            recipe = Recipe(bases=frozenset({entry.base}), elements=entry.elements)
        potion, messages = self.catalog.set_recipe(target, recipe)
        self.invalidate()
        return potion, messages

    # ── FR-7.6 Отмена последней варки ─────────────────────────────────────
    def undo_brew(self, entry_id: str) -> JournalEntry:
        """Возвращает реагенты, убирает зелье, помечает запись отменённой."""
        entry = self.journal.entry(entry_id)
        if entry.type is not JournalEntryType.BREW or entry.undone:
            raise AlchimistError(ErrorCode.NOT_FOUND, id=entry_id)

        inventory = self.inventory.inventory
        returned = [(s.ingredient_id, s.qty) for s in entry.reagents]
        if entry.base_ingredient_id:
            # Особая основа — тоже назад, ровно столько, сколько списали.
            returned.append((entry.base_ingredient_id, entry.base_qty))
        for ingredient_id, qty in returned:
            have = inventory.reagent_qty(ingredient_id)
            inventory = self.inventory.with_reagent(inventory, ingredient_id, have + qty, None)
        potion_ids: tuple[str, ...] = ()
        if entry.result and entry.result.potion_id and entry.yield_qty:
            have = inventory.potion_qty(entry.result.potion_id)
            if have < entry.yield_qty:
                raise AlchimistError(
                    ErrorCode.NOT_ENOUGH_POTIONS,
                    id=entry.result.potion_id,
                    have=have,
                    need=entry.yield_qty,
                )
            inventory = self.inventory.with_potion(
                inventory, entry.result.potion_id, have - entry.yield_qty, None
            )
            potion_ids = (entry.result.potion_id,)

        undone = replace(entry, undone=True)
        entries = [
            undone if e.id == entry.id else e for e in self.journal.entries(newest_first=False)
        ]
        writes = [
            self.inventory.prepare_write(inventory),
            self.journal.prepare_write(entries),
        ]
        try:
            commit_all(writes)
        except OSError:
            abort_all(writes)
            raise

        self.inventory.adopt(inventory)
        self.journal.adopt(entries)
        self.invalidate()
        self.bus.publish(InventoryChanged(tuple(i for i, _qty in returned), potion_ids))
        self.bus.publish(JournalChanged((entry.id,)))
        return undone

    def last_brew(self) -> JournalEntry | None:
        for entry in self.journal.entries():
            if entry.type is JournalEntryType.BREW and not entry.undone:
                return entry
        return None

    # ── обратные ссылки (FR-1.6) ──────────────────────────────────────────
    def recipes_using(self, ingredient: Ingredient) -> list[Potion]:
        """В каких известных рецептах реагент может участвовать."""
        return [
            potion
            for potion in self.catalog.potions()
            if potion.recipe is not None
            and not ingredient.elements.is_empty
            and ingredient.elements.fits_in(potion.recipe.elements)
        ]
