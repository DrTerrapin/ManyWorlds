"""The contract every task implementation fulfills.

Python has no `interface` keyword; `abc.ABC` + `@abstractmethod` is the
closest equivalent and, importantly for this design, it gives you real
inheritance: `IStepProvider` -> `ClassicalStepProvider` / `QuantumStepProvider`
-> concrete providers. A `typing.Protocol` would give you structural typing
(duck typing checked by static analysis) instead, with no base-class
hierarchy - not what we want here since ClassicalStepProvider and
QuantumStepProvider are meant to carry shared behavior later.

Because ABC enforces that every abstract method is implemented before a
class can be instantiated, ClassicalStepProvider and QuantumStepProvider
stay correctly "unimplemented" for free: they inherit `execute` as
abstract and don't implement it, so Python itself refuses to instantiate
them directly, exactly matching "base classes, not concrete yet."
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .variable_store import VariableStore


class TaskProvider(ABC):
    """Executes one task. A concrete provider is registered against a
    `task_type` string via @register_step_provider (see registry.py)."""

    @abstractmethod
    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        """Run the task and return its result.

        `parameters` is `ScheduledTask.parameters` from the experiment,
        with any `$(varName)` placeholders already substituted against the
        experiment's variable store (see TaskExecutor.resolve_parameters).
        `variables` is a VariableStore over that same variable store - call
        `variables.set_variable(name, value)` to make a value available to
        later tasks, either via `$(varName)` substitution in their own
        parameters or via a VariableRef in a downstream fan_out/run_if. The
        returned dict becomes `ScheduledTask.result`. Raise any exception to
        fail the task - the scheduler catches it, records
        `ScheduledTask.error`, and fails/cascades from there.
        """
        raise NotImplementedError


class ClassicalTaskProvider(TaskProvider, ABC):
    """Base class for task providers that run on ordinary CPU/classical
    code. Intentionally left unimplemented - shared classical-provider
    behavior (retry policy, timeouts, whatever emerges) lands here later."""


class QuantumTaskProvider(TaskProvider, ABC):
    """Base class for task providers backed by a quantum job (e.g. IonQ's
    simulator or QPU targets). Intentionally left unimplemented - shared
    quantum-provider behavior (job submission/polling, backend selection)
    lands here later."""
