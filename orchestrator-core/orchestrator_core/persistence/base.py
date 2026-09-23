"""Abstract persistence contract. The scheduler only ever talks to this
interface, never to Cosmos (or anything else) directly - that's what makes
the backend configurable. See in_memory_repository.py for a
dependency-free implementation (handy for tests and the demo script) and
cosmos_repository.py for the real backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import RunStatus, ScheduledExperiment


class ExperimentRepository(ABC):
    @abstractmethod
    async def save_experiment(self, experiment: ScheduledExperiment) -> None:
        """Create or fully overwrite an experiment document."""

    @abstractmethod
    async def load_experiment(self, experiment_id: str) -> ScheduledExperiment:
        """Raises KeyError if no experiment with that id exists."""

    @abstractmethod
    async def update_task(
        self,
        experiment_id: str,
        task_id: str,
        *,
        status: RunStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Updates one task's status/result/error within an experiment.

        Raises KeyError if the experiment or task doesn't exist.
        """
