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
from asyncio import tasks
from datetime import datetime, timezone
import uuid

from .models import ExperimentDefinition, RunStatus, ScheduledExperiment, ScheduledTask
from .persistence.base import ExperimentRepository
from .registry import get_step_provider


class GraphValidationError(ValueError):
    """Raised up front for a malformed task graph (bad reference, duplicate id)."""


class Scheduler:
    def __init__(self, repository: ExperimentRepository) -> None:
        self._repository = repository

    async def schedule_experiment(self, experiment: ExperimentDefinition) -> ScheduledExperiment:
        self._validate_graph(experiment)

        scheduled_experiment = ScheduledExperiment(
            id=uuid.uuid4(),
            name=experiment.name,
            tasks=[],
            status=RunStatus.PENDING,
            created_at=_now(),
            updated_at=_now()
        )

        scheduled_tasks = [ScheduledTask(
            name=t.name,
            order=t.order,
            type=t.type,
            parameters=t.parameters) for t in experiment.tasks]

        for (task in scheduled_tasks)

        scheduled_experiment.tasks = tasks

        await self._repository.save_experiment(scheduled_experiment)

        tasks_by_id = {task.id: task for task in experiment.tasks}

        while True:
            pending = [t for t in experiment.tasks if t.status == RunStatus.PENDING]
            if not pending:
                break

            ready: list[ScheduledTask] = []
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

    async def _run_task(self, experiment: ScheduledExperiment, task: ScheduledTask) -> None:
        task.status = RunStatus.RUNNING
        await self._repository.update_task(experiment.id, task.id, status=task.status)

        try:
            provider_cls = get_step_provider(task.type)
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

    async def _fail_task(self, experiment: ScheduledExperiment, task: ScheduledTask, reason: str) -> None:
        task.status = RunStatus.FAILED
        task.error = reason
        await self._repository.update_task(
            experiment.id, task.id, status=task.status, error=task.error
        )

    @staticmethod
    def _validate_graph(experiment: ExperimentDefinition) -> None:
        seen_ids: set[str] = set()
        for task in experiment.tasks:
            if task.name in seen_ids:
                raise GraphValidationError(f"Duplicate task name '{task.name}' in experiment '{experiment.name}'.")
            seen_ids.add(task.name)

        all_ids = seen_ids
        for task in experiment.tasks:
            unknown = [dep_id for dep_id in task.depends_on if dep_id not in all_ids]
            if unknown:
                raise GraphValidationError(
                    f"Task '{task.name}' depends_on unknown task name(s): {unknown}"
                )


def _now() -> datetime:
    return datetime.now(timezone.utc)
