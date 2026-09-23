"""Proves the wiring works end to end with no external dependencies, across
three toy experiments run against the in-memory repository:

  1. build_dag_demo_experiment - a plain DAG:

        A --> B --> D
         \\-> C -/
         \\-> E --> F   (E always fails; F should cascade-fail without running)

  2. build_fanout_demo_experiment - a fan_out template expanded into one
     instance per list item, joined by a downstream task that waits on all
     of them (and fails if any instance does).

  3. build_branch_demo_experiment - two mutually-exclusive run_if branches
     on the same upstream result; the not-taken branch (and its own
     downstream task) is skipped rather than failed.

Run with:

    python examples/run_demo.py

Keep this file passing as you add new scheduler features - it's the
fastest way to notice a regression in the DAG/fan-out/branching behavior
without spinning up a real backend.
"""

from __future__ import annotations

import asyncio
from typing import Any

from orchestrator_core import (
    BranchCondition,
    ExperimentDefinition,
    FanOutSpec,
    ResultRef,
    RunStatus,
    ScheduledExperiment,
    ScheduledTask,
    Scheduler,
    TaskDefinition,
    TaskExecutor,
    TaskProvider,
    VariableRef,
    VariableStore,
    get_task_provider,
    register_task_provider,
)
from orchestrator_core.persistence import InMemoryExperimentRepository


@register_task_provider("demo.echo")
class EchoProvider(TaskProvider):
    """Toy provider: just returns whatever parameters it was given."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # stand-in for real work
        return {"echoed": parameters}


@register_task_provider("demo.always_fails")
class AlwaysFailsProvider(TaskProvider):
    """Toy provider: always raises, to demonstrate cascading failure."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        raise RuntimeError("simulated failure for demo purposes")


@register_task_provider("demo.make_list")
class MakeListProvider(TaskProvider):
    """Toy provider: produces the list a fan_out template expands over."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"items": [1, 2, 3, 4]}


@register_task_provider("demo.square")
class SquareProvider(TaskProvider):
    """Toy provider: one fan_out instance's work - squares its item."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        n = parameters["n"]
        return {"squared": n * n}


@register_task_provider("demo.join")
class JoinProvider(TaskProvider):
    """Toy provider: downstream task that only runs once every fan_out
    instance (or every not-skipped branch) it depends on has resolved."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"joined": True}


@register_task_provider("demo.classify")
class ClassifyProvider(TaskProvider):
    """Toy provider: produces the value the branch demo's run_if conditions test."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"level": "high"}


@register_task_provider("demo.set_threshold")
class SetThresholdProvider(TaskProvider):
    """Toy provider: writes variables the rest of the variables demo consumes -
    a scalar (for string-interpolation substitution) and a list (for a
    VariableRef-based fan_out)."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        variables.set_variable("threshold", 3)
        variables.set_variable("candidates", [1, 2, 3, 4, 5])
        return {"set": ["threshold", "candidates"]}


@register_task_provider("demo.report_uri")
class ReportUriProvider(TaskProvider):
    """Toy provider: just echoes back a parameter, to show the value it
    received after $(varName) substitution ran."""

    async def execute(self, parameters: dict[str, Any], variables: VariableStore) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"uri": parameters["uri"]}


def build_dag_demo_experiment() -> ExperimentDefinition:
    return ExperimentDefinition(
        name="Fan-in / cascade-failure demo",
        tasks=[
            TaskDefinition(name="A", order=0, type="demo.echo", parameters={"data_uri": "s3://bucket/input.csv"}),
            TaskDefinition(name="B", order=1, type="demo.echo", parameters={"step": "B"}, depends_on=["A"]),
            TaskDefinition(name="C", order=1, type="demo.echo", parameters={"step": "C"}, depends_on=["A"]),
            TaskDefinition(
                name="D", order=2, type="demo.echo", parameters={"step": "D - joins B and C"}, depends_on=["B", "C"]
            ),
            TaskDefinition(name="E", order=1, type="demo.always_fails", parameters={}, depends_on=["A"]),
            TaskDefinition(
                name="F", order=2, type="demo.echo", parameters={"step": "F - should never run"}, depends_on=["E"]
            ),
        ],
    )


def build_fanout_demo_experiment() -> ExperimentDefinition:
    return ExperimentDefinition(
        name="Fan-out / fan-in demo",
        tasks=[
            TaskDefinition(name="MakeList", order=0, type="demo.make_list"),
            TaskDefinition(
                name="Square",
                order=1,
                type="demo.square",
                depends_on=["MakeList"],
                fan_out=FanOutSpec(over=ResultRef(task="MakeList", path="items"), item_parameter="n"),
            ),
            TaskDefinition(
                name="Join", order=2, type="demo.join", depends_on=["Square"]
            ),
        ],
    )


def build_branch_demo_experiment() -> ExperimentDefinition:
    return ExperimentDefinition(
        name="Conditional branching demo",
        tasks=[
            TaskDefinition(name="Classify", order=0, type="demo.classify"),
            TaskDefinition(
                name="LowPath",
                order=1,
                type="demo.echo",
                parameters={"step": "handling low"},
                depends_on=["Classify"],
                run_if=BranchCondition(ref=ResultRef(task="Classify", path="level"), equals="low"),
            ),
            TaskDefinition(
                name="HighPath",
                order=1,
                type="demo.echo",
                parameters={"step": "handling high"},
                depends_on=["Classify"],
                run_if=BranchCondition(ref=ResultRef(task="Classify", path="level"), equals="high"),
            ),
            TaskDefinition(name="AfterLow", order=2, type="demo.join", depends_on=["LowPath"]),
            TaskDefinition(name="AfterHigh", order=2, type="demo.join", depends_on=["HighPath"]),
        ],
    )


def build_variables_demo_experiment() -> ExperimentDefinition:
    return ExperimentDefinition(
        name="Variable store demo",
        tasks=[
            TaskDefinition(name="SetThreshold", order=0, type="demo.set_threshold"),
            # Type-preserving substitution would keep "n" as the int 3 if
            # used as a whole-string placeholder; here it's embedded inside
            # a larger string, so string-interpolation substitution applies
            # and the int gets stringified into the URL.
            TaskDefinition(
                name="Report",
                order=1,
                type="demo.report_uri",
                parameters={"uri": "s3://bucket/threshold-$(threshold).csv"},
                depends_on=["SetThreshold"],
            ),
            # VariableRef-based fan_out: expands over a *variable* (set by
            # SetThreshold above), not a ResultRef.
            TaskDefinition(
                name="Square",
                order=1,
                type="demo.square",
                depends_on=["SetThreshold"],
                fan_out=FanOutSpec(over=VariableRef(name="candidates"), item_parameter="n"),
            ),
            TaskDefinition(name="Join", order=2, type="demo.join", depends_on=["Square"]),
        ],
    )


def print_result(result: ScheduledExperiment) -> None:
    print(f"Experiment '{result.name}' finished with status: {result.status.value}\n")
    # Sort by when each task last changed, not by insertion order - fan-out
    # instances are appended to the task list well after the tasks that
    # were already there, so insertion order doesn't reflect run order.
    for task in sorted(result.tasks, key=lambda t: t.updated_at):
        line = f"  {task.name:<10} [{task.type:<14}] -> {task.status.value}"
        if task.result is not None:
            line += f"  result={task.result}"
        if task.error is not None:
            line += f"  error={task.error!r}"
        if task.note is not None:
            line += f"  note={task.note!r}"
        print(line)
    print()


async def run_to_completion(executor: TaskExecutor, experiment: ScheduledExperiment) -> ScheduledExperiment:
    """The simplest possible driver of TaskExecutor's pull API: claim
    whatever's ready, run it in-process, report the result, repeat. A real
    "other piece" could just as easily be several workers each claiming
    from the same experiment concurrently, or a queue consumer - this is
    just enough to prove the split works end to end."""
    while not executor.is_complete(experiment):
        ready = await executor.claim_ready_tasks(experiment)
        if ready:
            await asyncio.gather(*(_execute_and_report(executor, experiment, task) for task in ready))
    return await executor.finalize_experiment(experiment)


async def _execute_and_report(executor: TaskExecutor, experiment: ScheduledExperiment, task: ScheduledTask) -> None:
    try:
        resolved_parameters = executor.resolve_parameters(experiment, task)
        provider = get_task_provider(task.type)()
        variable_store = VariableStore(experiment.variables)
        result = await provider.execute(resolved_parameters, variable_store)
        await executor.report_task_result(experiment, task.id, status=RunStatus.SUCCEEDED, result=result)
    except Exception as exc:  # noqa: BLE001 - a provider's own bug is still a task failure
        await executor.report_task_result(
            experiment, task.id, status=RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
        )


async def main() -> None:
    repository = InMemoryExperimentRepository()
    scheduler = Scheduler(repository)
    executor = TaskExecutor(repository)

    for build_experiment in (
        build_dag_demo_experiment,
        build_fanout_demo_experiment,
        build_branch_demo_experiment,
        build_variables_demo_experiment,
    ):
        scheduled = await scheduler.schedule_experiment(build_experiment())
        result = await run_to_completion(executor, scheduled)
        print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
