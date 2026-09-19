"""Proves the wiring works end to end with no external dependencies:
registers two toy providers, builds an experiment shaped like

    A --> B --> D
     \\-> C -/
     \\-> E --> F   (E always fails; F should cascade-fail without running)

and runs it against the in-memory repository. Run with:

    python examples/run_demo.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from orchestrator_core import Experiment, IStepProvider, Scheduler, Task, register_step_provider
from orchestrator_core.persistence import InMemoryExperimentRepository


@register_step_provider("demo.echo")
class EchoProvider(IStepProvider):
    """Toy provider: just returns whatever parameters it was given."""

    async def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # stand-in for real work
        return {"echoed": parameters}


@register_step_provider("demo.always_fails")
class AlwaysFailsProvider(IStepProvider):
    """Toy provider: always raises, to demonstrate cascading failure."""

    async def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        raise RuntimeError("simulated failure for demo purposes")


def build_demo_experiment() -> Experiment:
    return Experiment(
        id="exp-demo-1",
        name="Fan-out / fan-in / cascade-failure demo",
        tasks=[
            Task(id="A", task_type="demo.echo", parameters={"data_uri": "s3://bucket/input.csv"}),
            Task(id="B", task_type="demo.echo", parameters={"step": "B"}, depends_on=["A"]),
            Task(id="C", task_type="demo.echo", parameters={"step": "C"}, depends_on=["A"]),
            Task(id="D", task_type="demo.echo", parameters={"step": "D - joins B and C"}, depends_on=["B", "C"]),
            Task(id="E", task_type="demo.always_fails", parameters={}, depends_on=["A"]),
            Task(id="F", task_type="demo.echo", parameters={"step": "F - should never run"}, depends_on=["E"]),
        ],
    )


async def main() -> None:
    repository = InMemoryExperimentRepository()
    scheduler = Scheduler(repository)

    experiment = build_demo_experiment()
    result = await scheduler.run_experiment(experiment)

    print(f"Experiment '{result.name}' finished with status: {result.status.value}\n")
    for task in result.tasks:
        line = f"  {task.id:>2} [{task.task_type:<18}] -> {task.status.value}"
        if task.result is not None:
            line += f"  result={task.result}"
        if task.error is not None:
            line += f"  error={task.error!r}"
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
