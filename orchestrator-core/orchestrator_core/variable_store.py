"""A per-experiment variable store, and the `$(varName)` substitution that
reads from it.

`VariableStore` wraps a ScheduledExperiment's `variables` dict *by
reference*, not by copy: `set_variable` mutates the same dict object the
caller sees on `experiment.variables`, so a write made during
`provider.execute(...)` is immediately visible to the caller afterward
with no return-value channel needed - the same "mutate through" pattern
ScheduledTask fields already rely on elsewhere in this codebase.

`substitute_variables` is a pure dict-in/dict-out transform with no
dependency on the executor or repository layer, which is why it lives
here next to VariableStore rather than in models.py (kept to pure
Pydantic schema) or as a TaskExecutor method (TaskExecutor exposes a thin
wrapper around it - see executor.py's `resolve_parameters`).
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\((\w+)\)")


class UnresolvedVariableError(KeyError):
    """Raised when a `$(varName)` placeholder names a variable that isn't
    in the store, at substitution time."""


class VariableStore:
    def __init__(self, variables: dict[str, Any]) -> None:
        self._variables = variables

    def set_variable(self, name: str, value: Any) -> None:
        self._variables[name] = value

    def get_variable(self, name: str, default: Any = None) -> Any:
        return self._variables.get(name, default)


def substitute_variables(value: Any, variables: dict[str, Any]) -> Any:
    """Recursively resolves `$(varName)` placeholders in `value`.

    - dict: substitutes values recursively (keys untouched).
    - list: substitutes each item recursively.
    - str exactly matching `$(varName)` (nothing else): type-preserving -
      returns the variable's real value/type directly (could be a list,
      dict, number, bool, etc).
    - str with `$(varName)` embedded in more text: string interpolation -
      stringifies the variable and splices it into the surrounding string;
      supports multiple placeholders in one string.
    - anything else: returned unchanged.

    Raises UnresolvedVariableError (a KeyError) if a referenced name isn't
    in `variables`.
    """
    if isinstance(value, dict):
        return {k: substitute_variables(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_variables(v, variables) for v in value]
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value)
        if exact is not None:
            name = exact.group(1)
            if name not in variables:
                raise UnresolvedVariableError(f"Unresolved variable reference '$({name})'")
            return variables[name]

        def _interpolate(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                raise UnresolvedVariableError(f"Unresolved variable reference '$({name})'")
            return str(variables[name])

        return _PLACEHOLDER.sub(_interpolate, value)
    return value
