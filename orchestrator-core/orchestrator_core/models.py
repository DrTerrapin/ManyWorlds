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

from pydantic import BaseModel, Field, model_validator


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
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.SKIPPED)


class ResultRef(BaseModel):
    """Points at a value inside an upstream task's `result` dict.

    `task` is the upstream task's *name*, not its runtime id - this is
    authored on a TaskDefinition, before any id exists, and stays a name
    even after scheduling (unlike depends_on, which is resolved to ids).
    `path` is a dotted path into that task's `result`, e.g. "items" or
    "summary.count"; "" means the whole result dict. Numeric path segments
    index into lists (e.g. "items.0.id").
    """

    task: str
    path: str = ""

    def resolve(self, results_by_name: dict[str, Any]) -> Any:
        value = results_by_name[self.task]
        for part in (p for p in self.path.split(".") if p):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value


class VariableRef(BaseModel):
    """Points at a value in the experiment's variable store.

    `name` is the variable's name (as set via VariableStore.set_variable).
    `path` is a dotted path into that value, same semantics as
    ResultRef.path - "" means the whole value, numeric segments index
    into lists.
    """

    name: str
    path: str = ""

    def resolve(self, variables: dict[str, Any]) -> Any:
        value = variables[self.name]
        for part in (p for p in self.path.split(".") if p):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value


class BranchCondition(BaseModel):
    """A structured (non-eval) predicate against a ResultRef- or
    VariableRef-resolved value. Exactly one of `equals` / `in_` must be
    set."""

    ref: ResultRef | VariableRef
    equals: Any | None = None
    in_: list[Any] | None = Field(default=None, alias="in")

    @model_validator(mode="after")
    def _exactly_one_predicate(self) -> "BranchCondition":
        if (self.equals is None) == (self.in_ is None):
            raise ValueError("BranchCondition requires exactly one of 'equals' or 'in'.")
        return self


class FanOutSpec(BaseModel):
    """Marks a TaskDefinition as a template: expanded into one
    ScheduledTask per item of `over`'s resolved list, at runtime."""

    over: ResultRef | VariableRef
    item_parameter: str


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
    depends_on: list[str] = Field(default_factory=list)  # sibling task *names*; resolved to ids on ScheduledTask

    # Fan-out: marks this task as a template, expanded into one ScheduledTask
    # per item of `fan_out.over`'s resolved list once its deps succeed.
    fan_out: FanOutSpec | None = None

    # Branching: this task only runs if `run_if` holds against its deps'
    # results; otherwise it (and anything depending solely on it) is skipped.
    run_if: BranchCondition | None = None


class ExperimentDefinition(BaseModel):
    """A named, ordered-by-dependency collection of task definitions."""

    name: str
    tasks: list[TaskDefinition]
    variables: dict[str, Any] = Field(default_factory=dict)  # seeded onto ScheduledExperiment.variables


class ScheduledTask(TaskDefinition):
    """A `TaskDefinition` that has been instanced for scheduling/execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Overrides TaskDefinition.depends_on: once scheduled, dependencies are
    # resolved from author-facing names to the referenced siblings' ids.
    depends_on: list[uuid.UUID] = Field(default_factory=list)

    result: dict[str, Any] | None = None
    error: str | None = None
    note: str | None = None  # non-error explanation, e.g. why this task was skipped

    # Set only on a generated fan-out instance: the id of the template
    # task that produced it, and its position within that expansion.
    template_id: uuid.UUID | None = None
    fan_out_index: int | None = None


class ScheduledExperiment(ExperimentDefinition):
    """An `ExperimentDefinition` that has been instanced for scheduling/execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tasks: list[ScheduledTask]
    variables: dict[str, Any] = Field(default_factory=dict)

    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_task(self, task_id: str) -> ScheduledTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Experiment '{self.id}' has no task with id '{task_id}'")
