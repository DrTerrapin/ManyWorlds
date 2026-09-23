"""Maps a `task_type` string to a concrete IStepProvider subclass.

Important: this registry is just a plain dict living in process memory.
`@register_step_provider(...)` only runs - and only adds an entry - when
Python actually imports the module that defines the decorated class.
OrchestratorCore itself never imports provider code and never needs to:
it only ever handles `task_type` as an opaque string. Something else
(a worker process, a plugin-loading step at startup) is responsible for
importing whatever provider packages should be available before asking
this registry to resolve a task_type into a class.

For now that "something else" is just: import the provider modules you
want available, at process startup, before calling get_step_provider.
Later, if you want true drop-in-a-package plugin discovery with no code
change, Python's `importlib.metadata.entry_points()` is the standard
mechanism (it's how pytest/Click plugins are discovered) - worth adding
once there's more than one provider package to manage.
"""

from __future__ import annotations

from typing import Callable

from .task_provider import TaskProvider

_registry: dict[str, type[TaskProvider]] = {}


class UnknownTaskTypeError(KeyError):
    """Raised when a task_type has no registered provider."""


class DuplicateTaskTypeError(ValueError):
    """Raised when two providers try to register the same task_type."""


def register_task_provider(
    task_type: str,
) -> Callable[[type[TaskProvider]], type[TaskProvider]]:
    """Class decorator: records `task_type -> cls` in the registry.

    Usage:
        @register_task_provider("risk.quantum_correlation")
        class QuantumRiskProvider(QuantumTaskProvider):
            async def execute(self, parameters): ...
    """

    def decorator(cls: type[TaskProvider]) -> type[TaskProvider]:
        if not issubclass(cls, TaskProvider):
            raise TypeError(
                f"{cls.__name__} must inherit from IStepProvider to be registered."
            )
        existing = _registry.get(task_type)
        if existing is not None and existing is not cls:
            raise DuplicateTaskTypeError(
                f"task_type '{task_type}' is already registered to "
                f"{existing.__module__}.{existing.__name__}; "
                f"cannot also register {cls.__module__}.{cls.__name__}."
            )
        _registry[task_type] = cls
        return cls

    return decorator


def get_task_provider(task_type: str) -> type[TaskProvider]:
    """Resolves a task_type string to its registered provider class.

    Raises UnknownTaskTypeError (with the list of what *is* registered) if
    nothing has registered that task_type - almost always because the
    module defining that provider hasn't been imported yet in this process.
    """
    try:
        return _registry[task_type]
    except KeyError:
        raise UnknownTaskTypeError(
            f"No task provider registered for task_type '{task_type}'. "
            f"Known task types: {registered_task_types()}. "
            "Make sure the module defining that provider has been imported "
            "before scheduling this task."
        ) from None


def registered_task_types() -> list[str]:
    return sorted(_registry)
