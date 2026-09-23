"""CosmosDB-backed ExperimentRepository.

Each Experiment is stored as one JSON document, partitioned by its own
`id` (so `partition_key = "/id"` - fine for this scale; revisit if a
single experiment's document ever gets large or you need cross-experiment
queries by something other than id). `update_task` is a read-modify-write:
load the whole experiment document, mutate the one task in Python, write
the whole document back.

KNOWN LIMITATION: read-modify-write is not safe against two concurrent
writers racing on the same experiment (the second write can silently
clobber the first). Fine for a single scheduler instance running one
experiment at a time, which is the v1 assumption. If you later run
multiple scheduler workers against the same experiment concurrently, the
fix is optimistic concurrency via Cosmos's ETag: pass `etag=` /
`match_condition=` on the write and retry on a conflict. Flagging it here
rather than building it now, since it adds real complexity for a case
that doesn't exist yet.
"""

from __future__ import annotations

from typing import Any

from azure.cosmos import PartitionKey, exceptions
from azure.cosmos.aio import ContainerProxy, CosmosClient

from ..models import RunStatus, ScheduledExperiment, ScheduledTask
from .base import ExperimentRepository


class CosmosExperimentRepository(ExperimentRepository):
    def __init__(
        self,
        endpoint: str,
        key: str,
        database_name: str,
        container_name: str,
        *,
        connection_verify: bool = True,
    ) -> None:
        """`connection_verify=False` is what lets the SDK talk to the local
        emulator's self-signed certificate without you having to install it
        into your machine's trust store - see the README. Leave it True
        (the default) for a real Cosmos account.
        """
        self._client = CosmosClient(
            endpoint, credential=key, connection_verify=connection_verify
        )
        self._database_name = database_name
        self._container_name = container_name

    async def ensure_container(self, partition_key_path: str = "/id") -> None:
        """Creates the database/container if they don't already exist.
        Call this once at startup (see the demo script and README) - the
        constructor itself does no I/O, so nothing exists yet until you do.
        """
        database = await self._client.create_database_if_not_exists(
            id=self._database_name
        )
        await database.create_container_if_not_exists(
            id=self._container_name,
            partition_key=PartitionKey(path=partition_key_path),
        )

    async def close(self) -> None:
        await self._client.close()

    async def _container(self) -> ContainerProxy:
        database = self._client.get_database_client(self._database_name)
        return database.get_container_client(self._container_name)

    async def save_experiment(self, experiment: ScheduledExperiment) -> None:
        container = await self._container()
        await container.upsert_item(body=experiment.model_dump(mode="json"))

    async def load_experiment(self, experiment_id: str) -> ScheduledExperiment:
        container = await self._container()
        try:
            item = await container.read_item(
                item=experiment_id, partition_key=experiment_id
            )
        except exceptions.CosmosResourceNotFoundError:
            raise KeyError(f"No experiment found with id '{experiment_id}'") from None
        return ScheduledExperiment.model_validate(item)

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
        experiment = await self.load_experiment(experiment_id)
        task = experiment.get_task(task_id)  # raises KeyError if missing
        task.status = status
        task.result = result
        task.error = error
        task.note = note
        if variables is not None:
            experiment.variables = variables
        await self.save_experiment(experiment)

    async def append_tasks(self, experiment_id: str, tasks: list[ScheduledTask]) -> None:
        experiment = await self.load_experiment(experiment_id)  # raises KeyError if missing

        existing_ids = {t.id for t in experiment.tasks}
        duplicates = existing_ids.intersection(t.id for t in tasks)
        if duplicates:
            raise ValueError(f"Task id(s) already exist on experiment '{experiment_id}': {duplicates}")

        experiment.tasks.extend(tasks)
        await self.save_experiment(experiment)
