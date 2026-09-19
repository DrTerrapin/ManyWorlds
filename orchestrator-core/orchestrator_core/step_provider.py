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


class StepProvider(ABC):
    """Executes one task. A concrete provider is registered against a
    `task_type` string via @register_step_provider (see registry.py)."""

    @abstractmethod
    async def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """Run the task and return its result.

        `parameters` is exactly `Task.parameters` from the experiment
        definition. The returned dict becomes `Task.result`. Raise any
        exception to fail the task - the scheduler catches it, records
        `Task.error`, and fails/cascades from there.
        """
        raise NotImplementedError


class ClassicalStepProvider(StepProvider, ABC):
    """Base class for step providers that run on ordinary CPU/classical
    code. Intentionally left unimplemented - shared classical-provider
    behavior (retry policy, timeouts, whatever emerges) lands here later."""


class QuantumStepProvider(StepProvider, ABC):
    """Base class for step providers backed by a quantum job (e.g. IonQ's
    simulator or QPU targets). Intentionally left unimplemented - shared
    quantum-provider behavior (job submission/polling, backend selection)
    lands here later."""
