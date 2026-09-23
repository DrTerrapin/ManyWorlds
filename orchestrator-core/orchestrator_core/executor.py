"""Advances and runs a ScheduledExperiment's task graph, pull-style.

Deliberately not "run this experiment to completion" as one call. Instead:
`claim_ready_tasks` advances the graph as far as it can without running
anything (cascading FAILED/SKIPPED, expanding fan_out templates, gating
run_if branches) and hands back whatever's now genuinely ready to run,
already marked RUNNING so a second caller can't also claim it. Whoever
called it is responsible for actually executing those tasks - via a
TaskProvider in-process, on a worker elsewhere, however - and reporting
the outcome back through `report_task_result`. This is what lets more than
one worker pull from the same experiment concurrently, or lets execution
happen somewhere entirely different from scheduling.

See examples/run_demo.py's `run_to_completion` for the simplest possible
driver of this API: a single-process loop that claims, runs, and reports
until `is_complete`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .models import BranchCondition, RunStatus, ScheduledExperiment, ScheduledTask
from .persistence.base import ExperimentRepository


class TaskExecutor:
    def __init__(self, repository: ExperimentRepository) -> None:
        self._repository = repository

    async def claim_ready_tasks(self, experiment: ScheduledExperiment) -> list[ScheduledTask]:
        """One advancement pass over every PENDING task.

        Fails/skips tasks whose dependencies already resolved that way,
        expands any fan_out template whose dependencies just succeeded,
        evaluates any run_if gate whose dependencies just succeeded, and
        claims (flips to RUNNING, persists) whatever's genuinely ready.
        Returns just the newly-claimed tasks.

        If nothing happens at all this pass - nothing claimed, failed,
        skipped, or expanded - and nothing is currently RUNNING elsewhere
        (so no in-flight work could ever unblock the rest), whatever's
        left PENDING can never resolve: a dependency cycle, or a
        depends_on referencing a task that will never finish. Those are
        force-failed here rather than left to stall forever.
        """
        pending = [t for t in experiment.tasks if t.status == RunStatus.PENDING]
        claimed: list[ScheduledTask] = []
        progressed = False

        for task in pending:
            dep_statuses = [_dependency_status(experiment, dep_id) for dep_id in task.depends_on]

            if any(s == RunStatus.FAILED for s in dep_statuses):
                await self._fail_task(experiment, task, "an upstream dependency failed")
                progressed = True
            elif any(s == RunStatus.SKIPPED for s in dep_statuses):
                await self._skip_task(experiment, task, "an upstream dependency was skipped")
                progressed = True
            elif all(s == RunStatus.SUCCEEDED for s in dep_statuses):
                if task.fan_out is not None:
                    await self._expand_task(experiment, task)
                    progressed = True
                elif task.run_if is not None and not self._branch_holds(experiment, task.run_if):
                    await self._skip_task(experiment, task, "branch condition not met")
                    progressed = True
                else:
                    task.status = RunStatus.RUNNING
                    await self._repository.update_task(experiment.id, task.id, status=task.status)
                    claimed.append(task)
                    progressed = True
            # else: still waiting on an unfinished (not yet terminal) dependency

        if claimed or progressed:
            return claimed

        # Nothing changed this pass. If something's still RUNNING, that's
        # in-flight work a caller hasn't reported back on yet - normal,
        # just return empty and let the caller poll again later. Otherwise
        # nothing pending can ever become ready.
        if any(t.status == RunStatus.RUNNING for t in experiment.tasks):
            return []

        for task in pending:
            await self._fail_task(
                experiment,
                task,
                "unresolvable dependency graph (cycle, or a dependency that will never complete)",
            )
        return []

    async def report_task_result(
        self,
        experiment: ScheduledExperiment,
        task_id: uuid.UUID,
        *,
        status: RunStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Records a claimed task's outcome. Called by whoever actually ran it."""
        task = experiment.get_task(task_id)
        task.status = status
        task.result = result
        task.error = error
        await self._repository.update_task(experiment.id, task_id, status=status, result=result, error=error)

    def is_complete(self, experiment: ScheduledExperiment) -> bool:
        """True once every task has reached a terminal status."""
        return all(t.status.is_terminal for t in experiment.tasks)

    async def finalize_experiment(self, experiment: ScheduledExperiment) -> ScheduledExperiment:
        """Rolls the experiment up to FAILED (if any task FAILED) or
        SUCCEEDED, and persists it. Call once is_complete() is True."""
        experiment.status = (
            RunStatus.FAILED
            if any(t.status == RunStatus.FAILED for t in experiment.tasks)
            else RunStatus.SUCCEEDED
        )
        experiment.updated_at = datetime.now(timezone.utc)
        await self._repository.save_experiment(experiment)
        return experiment

    async def _fail_task(self, experiment: ScheduledExperiment, task: ScheduledTask, reason: str) -> None:
        task.status = RunStatus.FAILED
        task.error = reason
        await self._repository.update_task(
            experiment.id, task.id, status=task.status, error=task.error
        )

    async def _skip_task(self, experiment: ScheduledExperiment, task: ScheduledTask, reason: str) -> None:
        task.status = RunStatus.SKIPPED
        task.note = reason
        await self._repository.update_task(
            experiment.id, task.id, status=task.status, note=task.note
        )

    async def _expand_task(self, experiment: ScheduledExperiment, template: ScheduledTask) -> None:
        results_by_name = {t.name: t.result for t in experiment.tasks if t.result is not None}

        try:
            items = template.fan_out.over.resolve(results_by_name)
            if not isinstance(items, list):
                raise TypeError(f"resolved to {type(items).__name__}, expected a list")
        except Exception as exc:  # noqa: BLE001 - a bad fan_out reference is a task failure, not a crash
            await self._fail_task(experiment, template, f"fan_out expansion failed: {exc}")
            return

        instances = [
            ScheduledTask(
                name=f"{template.name}[{i}]",
                order=template.order,
                type=template.type,
                parameters={**template.parameters, template.fan_out.item_parameter: item},
                depends_on=list(template.depends_on),
                template_id=template.id,
                fan_out_index=i,
            )
            for i, item in enumerate(items)
        ]

        if instances:
            experiment.tasks.extend(instances)
            await self._repository.append_tasks(experiment.id, instances)

        template.status = RunStatus.SUCCEEDED
        template.result = {"fanned_out_into": [t.name for t in instances]}
        await self._repository.update_task(
            experiment.id, template.id, status=template.status, result=template.result
        )

    def _branch_holds(self, experiment: ScheduledExperiment, condition: BranchCondition) -> bool:
        results_by_name = {t.name: t.result for t in experiment.tasks if t.result is not None}
        value = condition.ref.resolve(results_by_name)
        if condition.equals is not None:
            return value == condition.equals
        return value in condition.in_


def _dependency_status(experiment: ScheduledExperiment, dep_id: uuid.UUID) -> RunStatus:
    """Resolves a `depends_on` id to a single aggregate RunStatus.

    Transparently handles the case where `dep_id` refers to a fan_out
    template: once the template itself has expanded (SUCCEEDED) or was
    skipped/failed before expanding, this rolls up the status of every
    instance it produced (`template_id == dep_id`) into one join result -
    SUCCEEDED only if all instances succeeded, FAILED if any failed,
    SKIPPED if any were skipped (and none failed).
    """
    template = next((t for t in experiment.tasks if t.id == dep_id and t.fan_out is not None), None)
    if template is not None:
        if not template.status.is_terminal:
            return RunStatus.PENDING
        if template.status in (RunStatus.SKIPPED, RunStatus.FAILED):
            return template.status

        instances = [t for t in experiment.tasks if t.template_id == dep_id]
        if not instances:
            return RunStatus.SUCCEEDED  # expanded into zero items: vacuous join success
        if any(i.status == RunStatus.FAILED for i in instances):
            return RunStatus.FAILED
        if any(i.status == RunStatus.SKIPPED for i in instances):
            return RunStatus.SKIPPED
        if all(i.status == RunStatus.SUCCEEDED for i in instances):
            return RunStatus.SUCCEEDED
        return RunStatus.RUNNING

    return next(t for t in experiment.tasks if t.id == dep_id).status
