"""Data model for experiments and tasks.

These are the documents that get persisted (see persistence/) and passed
around the scheduler. Note what's deliberately *not* here: nothing about
how a task actually executes. `task_type` is just a string and `parameters`
is just a dict as far as this module is concerned - see step_provider.py
and registry.py for how a string turns into running code.

Each concept has a *definition* (the shape you'd author by hand - no id,
no status, nothing that only makes sense once something is actually
running) and a *scheduled* version (adds the instanced fields: id, status,
created_at, updated_at - plus, for a task, its result/error once it's run).
"""
from __future__ import annotations

import uuid

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    """Shared status enum for both Task and Experiment.

    Inheriting from `str` means this serializes as a plain string (e.g.
    "succeeded") in JSON/Cosmos documents rather than needing a custom
    encoder - `RunStatus.SUCCEEDED == "succeeded"` is True.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED)


class TaskDefinition(BaseModel):
    """The definition of one unit of work in an experiment.

    `task_type` is resolved to an actual StepProvider subclass at runtime
    via the registry (registry.py) - this model has no idea what kinds of
    tasks exist, which is what lets the orchestrator schedule task types
    it's never seen. `parameters` is intentionally an open dict: it's
    whatever the specific step provider needs, including wherever the user
    points at their input data (e.g. `{"data_uri": "...", "shots": 500}`).
    """

    name: str # Name of this task within it's experiment
    order: int # Order of this task in it's group
    type: str # The type of task, which will be resolved to a StepProvider at runtime
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class ExperimentDefinition(BaseModel):
    """A named, ordered-by-dependency collection of task definitions."""

    name: str
    tasks: list[TaskDefinition]


class ScheduledTask(TaskDefinition):
    """A `TaskDefinition` that has been instanced for scheduling/execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    result: dict[str, Any] | None = None
    error: str | None = None


class ScheduledExperiment(ExperimentDefinition):
    """An `ExperimentDefinition` that has been instanced for scheduling/execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tasks: list[ScheduledTask]

    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_task(self, task_id: str) -> ScheduledTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Experiment '{self.id}' has no task with id '{task_id}'")
