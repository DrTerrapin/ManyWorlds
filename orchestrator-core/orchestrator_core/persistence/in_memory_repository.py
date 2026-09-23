"""In-memory ExperimentRepository. No external dependencies, no I/O -
useful for unit tests and for running the demo without standing up Cosmos.
Not shared across processes and not durable; that's the point.
"""

from __future__ import annotations

from typing import Any

from ..models import RunStatus, ScheduledExperiment, ScheduledTask
from .base import ExperimentRepository


class InMemoryExperimentRepository(ExperimentRepository):
    def __init__(self) -> None:
        self._store: dict[str, ScheduledExperiment] = {}

    async def save_experiment(self, experiment: ScheduledExperiment) -> None:
        # Deep-copy on the way in so callers mutating their own Experiment
        # object afterward can't silently corrupt what we've "persisted" -
        # matches how a real database boundary behaves.
        self._store[experiment.id] = experiment.model_copy(deep=True)

    async def load_experiment(self, experiment_id: str) -> ScheduledExperiment:
        try:
            return self._store[experiment_id].model_copy(deep=True)
        except KeyError:
            raise KeyError(f"No experiment found with id '{experiment_id}'") from None

    async def update_task(
        self,
        experiment_id: str,
        task_id: str,
        *,
        status: RunStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        note: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        experiment = self._store.get(experiment_id)
        if experiment is None:
            raise KeyError(f"No experiment found with id '{experiment_id}'")

        task = experiment.get_task(task_id)  # raises KeyError if missing
        task.status = status
        task.result = result
        task.error = error
        task.note = note
        if variables is not None:
            experiment.variables = variables

    async def append_tasks(self, experiment_id: str, tasks: list[ScheduledTask]) -> None:
        experiment = self._store.get(experiment_id)
        if experiment is None:
            raise KeyError(f"No experiment found with id '{experiment_id}'")

        existing_ids = {t.id for t in experiment.tasks}
        duplicates = existing_ids.intersection(t.id for t in tasks)
        if duplicates:
            raise ValueError(f"Task id(s) already exist on experiment '{experiment_id}': {duplicates}")

        experiment.tasks.extend(t.model_copy(deep=True) for t in tasks)
