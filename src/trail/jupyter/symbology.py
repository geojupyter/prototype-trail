from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal

# a grammar layer's id, and a rule's id or None for the grammar layer's own predicates
type Scope = tuple[str, str | None]


def canonical(value: Any) -> str:
    out = json.dumps(value, sort_keys=True)
    return out


def number(value: Any) -> str:
    # 5.0 from a saved map and 5 from the dialog are the same threshold
    if (
        isinstance(value, float)
        and value.is_integer()
    ):
        value = int(value)
    out = str(value)
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class Conditions:
    when: tuple[str, ...] = ()
    when_op: str = 'all'

    @classmethod
    def of(cls, item: dict[str, Any]) -> Conditions:
        when = tuple(
            canonical(predicate)
            for predicate in item.get('when') or []
        )
        out = cls(
            when=when,
            when_op=item.get('whenOp') or 'all',
        )
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class PredicateChange:
    verb: Literal['added', 'removed']
    scope: Scope
    predicate: dict[str, Any]

    @property
    def is_geometry(self) -> bool:
        is_geometry = self.predicate.get('type') == 'geometryType'
        return is_geometry

    @property
    def label(self) -> str:
        if self.scope[1] is not None:
            out = f'rule condition {self.verb}'
        elif self.is_geometry:
            out = f'geometry condition {self.verb}'
        else:
            out = f'filter {self.verb}'
        return out

    @property
    def text(self) -> str:
        predicate = self.predicate
        kind = predicate.get('type')
        field = predicate.get('field')
        if kind == 'fieldCompare':
            out = f"{field} {predicate.get('op')} {number(predicate.get('value'))}"
        elif kind == 'between':
            out = f"{number(predicate.get('min'))} <= {field} <= {number(predicate.get('max'))}"
        elif kind == 'fieldEquals':
            out = f"{field} = {number(predicate.get('value'))}"
        elif kind == 'hasField':
            out = f'has {field}'
        elif kind == 'geometryType':
            out = str(predicate.get('value'))
        else:
            out = canonical(predicate)
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class CombinatorChange:
    scope: Scope
    old: str
    new: str

    @property
    def label(self) -> str:
        return 'combinator changed'

    @property
    def text(self) -> str:
        out = f'{self.old} -> {self.new}'
        return out


class SymbologyDiff:
    def __init__(
            self,
            old: dict[str, Any] | None,
            new: dict[str, Any] | None,
    ) -> None:
        self.old = old
        self.new = new

    @staticmethod
    def conditions(state: dict[str, Any] | None) -> dict[Scope, Conditions]:
        out: dict[Scope, Conditions] = {}
        if not isinstance(state, dict):
            return out
        for index, grammar in enumerate(state.get('layers') or []):
            grammar_id = str(grammar.get('id', f'#{index}'))
            out[grammar_id, None] = Conditions.of(grammar)
            for position, rule in enumerate(grammar.get('rules') or []):
                rule_id = str(rule.get('id', f'#{position}'))
                out[grammar_id, rule_id] = Conditions.of(rule)
        return out

    @cached_property
    def before(self) -> dict[Scope, Conditions]:
        out = self.conditions(self.old)
        return out

    @cached_property
    def after(self) -> dict[Scope, Conditions]:
        out = self.conditions(self.new)
        return out

    @cached_property
    def predicates(self) -> tuple[PredicateChange, ...]:
        empty = Conditions()
        changes = []
        scopes = dict.fromkeys([*self.before, *self.after])
        for scope in scopes:
            old = Counter(self.before.get(scope, empty).when)
            new = Counter(self.after.get(scope, empty).when)
            for verb, texts in (('removed', old - new), ('added', new - old)):
                changes.extend(
                    PredicateChange(
                        verb=verb,
                        scope=scope,
                        predicate=json.loads(text),
                    )
                    for text in texts.elements()
                )
        out = tuple(changes)
        return out

    @cached_property
    def combinators(self) -> tuple[CombinatorChange, ...]:
        empty = Conditions()
        changes = []
        scopes = dict.fromkeys([*self.before, *self.after])
        for scope in scopes:
            old = self.before.get(scope, empty).when_op
            new = self.after.get(scope, empty).when_op
            if old != new:
                changes.append(CombinatorChange(scope=scope, old=old, new=new))
        out = tuple(changes)
        return out

    @staticmethod
    def stripped(state: dict[str, Any] | None) -> str:
        if not isinstance(state, dict):
            return canonical(state)

        def bare(item: dict[str, Any]) -> dict[str, Any]:
            out = {
                key: value
                for key, value in item.items()
                if key not in ('when', 'whenOp')
            }
            return out

        layers = []
        for grammar in state.get('layers') or []:
            layer = bare(grammar)
            layer['rules'] = [
                bare(rule)
                for rule in grammar.get('rules') or []
            ]
            layers.append(layer)
        out = canonical({**state, 'layers': layers})
        return out

    @cached_property
    def has_other_changes(self) -> bool:
        has_changes = self.stripped(self.old) != self.stripped(self.new)
        return has_changes

    @property
    def has_filter_change(self) -> bool:
        has_change = bool(
            self.predicates
            or self.combinators
        )
        return has_change
