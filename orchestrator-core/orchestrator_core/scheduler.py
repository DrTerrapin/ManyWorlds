"""Turns an ExperimentDefinition into a persisted ScheduledExperiment.

This is intentionally the *only* thing Scheduler does: validate the graph,
instantiate a ScheduledTask per TaskDefinition, resolve each task's
name-based `depends_on` into the ids of the sibling ScheduledTasks it
refers to, and save the result. It never calls a TaskProvider and never
runs anything - that's TaskExecutor's job (see executor.py), which pulls
ready tasks out, expects them to be executed elsewhere, and takes the
result back via report_task_result.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .models import ExperimentDefinition, ResultRef, RunStatus, ScheduledExperiment, ScheduledTask
from .persistence.base import ExperimentRepository


class GraphValidationError(ValueError):
    """Raised up front for a malformed task graph (bad reference, duplicate id)."""


class Scheduler:
    def __init__(self, repository: ExperimentRepository) -> None:
        self._repository = repository

    async def schedule_experiment(self, experiment: ExperimentDefinition) -> ScheduledExperiment:
        self._validate_graph(experiment)

        # First pass: one ScheduledTask per definition, so every task has
        # an id - depends_on is filled in on the second pass since a task
        # can depend on a sibling that appears later in the list.
        scheduled_by_name: dict[str, ScheduledTask] = {
            t.name: ScheduledTask(
                name=t.name,
                order=t.order,
                type=t.type,
                parameters=t.parameters,
                depends_on=[],
                fan_out=t.fan_out,
                run_if=t.run_if,
            )
            for t in experiment.tasks
        }

        for t in experiment.tasks:
            scheduled_by_name[t.name].depends_on = [
                scheduled_by_name[dep_name].id for dep_name in t.depends_on
            ]

        scheduled_experiment = ScheduledExperiment(
            id=uuid.uuid4(),
            name=experiment.name,
            tasks=list(scheduled_by_name.values()),
            variables=dict(experiment.variables),
            status=RunStatus.PENDING,
            created_at=_now(),
            updated_at=_now(),
        )

        await self._repository.save_experiment(scheduled_experiment)
        return scheduled_experiment

    @staticmethod
    def _validate_graph(experiment: ExperimentDefinition) -> None:
        seen_ids: set[str] = set()
        for task in experiment.tasks:
            if task.name in seen_ids:
                raise GraphValidationError(f"Duplicate task name '{task.name}' in experiment '{experiment.name}'.")
            if "[" in task.name or "]" in task.name:
                raise GraphValidationError(
                    f"Task name '{task.name}' may not contain '[' or ']' - reserved for generated fan-out instance names."
                )
            seen_ids.add(task.name)

        all_ids = seen_ids
        tasks_by_name = {t.name: t for t in experiment.tasks}

        for task in experiment.tasks:
            unknown = [dep_id for dep_id in task.depends_on if dep_id not in all_ids]
            if unknown:
                raise GraphValidationError(
                    f"Task '{task.name}' depends_on unknown task name(s): {unknown}"
                )

            if task.fan_out is not None:
                # A VariableRef has no sibling task to check structurally - an
                # unset variable becomes a runtime failure at expansion time
                # instead (see executor.py's _resolve_ref / _expand_task).
                if isinstance(task.fan_out.over, ResultRef):
                    source_name = task.fan_out.over.task
                    if source_name == task.name:
                        raise GraphValidationError(f"Task '{task.name}' cannot fan out over its own result.")
                    if source_name not in task.depends_on:
                        raise GraphValidationError(
                            f"Task '{task.name}' has fan_out.over referencing '{source_name}', "
                            "which must also be listed in depends_on."
                        )
                    source_task = tasks_by_name[source_name]
                    if source_task.fan_out is not None:
                        raise GraphValidationError(
                            f"Task '{task.name}' cannot fan out over '{source_name}', which is itself "
                            "a fan_out template - nested fan-out isn't supported."
                        )
                if task.fan_out.item_parameter in task.parameters:
                    raise GraphValidationError(
                        f"Task '{task.name}' has fan_out.item_parameter '{task.fan_out.item_parameter}' "
                        "which collides with an existing key in its own parameters."
                    )

            if task.run_if is not None and isinstance(task.run_if.ref, ResultRef):
                source_name = task.run_if.ref.task
                if source_name not in task.depends_on:
                    raise GraphValidationError(
                        f"Task '{task.name}' has run_if referencing '{source_name}', "
                        "which must also be listed in depends_on."
                    )
                source_task = tasks_by_name[source_name]
                if source_task.fan_out is not None:
                    raise GraphValidationError(
                        f"Task '{task.name}' cannot branch on '{source_name}', which is a fan_out "
                        "template with no single result to test."
                    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
