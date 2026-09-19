"""Runs an Experiment's tasks to completion, respecting depends_on.

Execution model: repeated "waves". Each wave, every PENDING task whose
dependencies have all SUCCEEDED runs concurrently (asyncio.gather). Any
PENDING task with a FAILED dependency is failed immediately without
running, and that failure cascades transitively over subsequent waves
(if A fails, B which depends on A fails next wave, C which depends on B
fails the wave after that, and so on). If a wave produces no newly-ready
tasks and no newly-failed ones, whatever's left PENDING can never resolve
(a dependency cycle, or a depends_on referencing a task that will never
finish) - the remaining tasks are force-failed with that explanation
rather than looping forever.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .models import Experiment, RunStatus, Task
from .persistence.base import ExperimentRepository
from .registry import get_step_provider


class GraphValidationError(ValueError):
    """Raised up front for a malformed task graph (bad reference, duplicate id)."""


class Scheduler:
    def __init__(self, repository: ExperimentRepository) -> None:
        self._repository = repository

    async def run_experiment(self, experiment: Experiment) -> Experiment:
        self._validate_graph(experiment)

        experiment.status = RunStatus.RUNNING
        experiment.updated_at = _now()
        await self._repository.save_experiment(experiment)

        tasks_by_id = {task.id: task for task in experiment.tasks}

        while True:
            pending = [t for t in experiment.tasks if t.status == RunStatus.PENDING]
            if not pending:
                break

            ready: list[Task] = []
            for task in pending:
                deps = [tasks_by_id[dep_id] for dep_id in task.depends_on]
                if any(dep.status == RunStatus.FAILED for dep in deps):
                    await self._fail_task(experiment, task, "an upstream dependency failed")
                elif all(dep.status == RunStatus.SUCCEEDED for dep in deps):
                    ready.append(task)
                # else: still waiting on an unfinished (not yet terminal) dependency

            if ready:
                await asyncio.gather(*(self._run_task(experiment, task) for task in ready))
                continue

            # Nothing became ready this wave. If nothing failed above either,
            # nothing will ever change - that's a cycle (or a reference to a
            # task that itself never resolves), not just "still waiting".
            still_pending = [t for t in pending if t.status == RunStatus.PENDING]
            if still_pending:
                for task in still_pending:
                    await self._fail_task(
                        experiment,
                        task,
                        "unresolvable dependency graph (cycle, or a dependency "
                        "that will never complete)",
                    )

        experiment.status = (
            RunStatus.FAILED
            if any(t.status == RunStatus.FAILED for t in experiment.tasks)
            else RunStatus.SUCCEEDED
        )
        experiment.updated_at = _now()
        await self._repository.save_experiment(experiment)
        return experiment

    async def _run_task(self, experiment: Experiment, task: Task) -> None:
        task.status = RunStatus.RUNNING
        await self._repository.update_task(experiment.id, task.id, status=task.status)

        try:
            provider_cls = get_step_provider(task.task_type)
            provider = provider_cls()
            task.result = await provider.execute(task.parameters)
            task.status = RunStatus.SUCCEEDED
            task.error = None
        except Exception as exc:  # noqa: BLE001 - a provider's own bug is still a task failure
            task.status = RunStatus.FAILED
            task.error = f"{type(exc).__name__}: {exc}"

        await self._repository.update_task(
            experiment.id, task.id, status=task.status, result=task.result, error=task.error
        )

    async def _fail_task(self, experiment: Experiment, task: Task, reason: str) -> None:
        task.status = RunStatus.FAILED
        task.error = reason
        await self._repository.update_task(
            experiment.id, task.id, status=task.status, error=task.error
        )

    @staticmethod
    def _validate_graph(experiment: Experiment) -> None:
        seen_ids: set[str] = set()
        for task in experiment.tasks:
            if task.id in seen_ids:
                raise GraphValidationError(f"Duplicate task id '{task.id}' in experiment '{experiment.id}'.")
            seen_ids.add(task.id)

        all_ids = seen_ids
        for task in experiment.tasks:
            unknown = [dep_id for dep_id in task.depends_on if dep_id not in all_ids]
            if unknown:
                raise GraphValidationError(
                    f"Task '{task.id}' depends_on unknown task id(s): {unknown}"
                )


def _now() -> datetime:
    return datetime.now(timezone.utc)
