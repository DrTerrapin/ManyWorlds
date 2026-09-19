# orchestrator-core

A small async engine for running **experiments** made of **tasks**, where
each task can be backed by classical code or a quantum job, and the
engine itself doesn't know or care which.

## Concepts

- **Experiment** — a named collection of `Task`s.
- **Task** — `{ id, task_type, parameters, depends_on }`. `task_type` is
  just a string; `parameters` is an open dict (this is where a task's
  reference to the user's input data lives - e.g. `{"data_uri": "..."}`).
  `depends_on` is a list of other task ids in the same experiment, so the
  graph supports fan-out (`B` and `C` both `depends_on: [A]`) and fan-in
  (`D` depends_on: `[B, C]`) - a DAG, not just a linear chain.
- **`IStepProvider`** — the contract a task implementation fulfills: one
  async `execute(parameters) -> dict`. `ClassicalStepProvider` and
  `QuantumStepProvider` are unimplemented base classes sitting between
  `IStepProvider` and real providers, for behavior that's shared within
  each family (retry policy, backend/job-polling logic, etc.) once there
  is any.
- **`Scheduler`** — runs an `Experiment`'s tasks in dependency-respecting
  waves, persisting status as it goes.

## Why `task_type` is just a string, and how it resolves to code

`OrchestratorCore` never imports provider code. It schedules and persists
`task_type` as an opaque string - which is what lets it schedule a task
type it has never seen. Resolving that string to an actual class happens
through a **registry** (`orchestrator_core/registry.py`): a provider class
is registered with

```python
@register_step_provider("risk.quantum_correlation")
class QuantumRiskProvider(QuantumStepProvider):
    async def execute(self, parameters): ...
```

The catch worth understanding up front: `@register_step_provider` only
*runs* - and only adds an entry to the registry - when Python actually
imports that module. The registry is a plain dict in process memory; it
isn't persisted and isn't shared across processes. So there are two
distinct roles here:

- **`OrchestratorCore`** (this package) only ever touches `task_type` as a
  string and `parameters` as a dict.
- **A worker** (whatever process actually calls `Scheduler.run_experiment`)
  is responsible for importing every provider package it wants available
  *before* the scheduler tries to resolve a `task_type`. For now that
  means: list the provider modules to import at worker startup. If you
  later want true drop-in-a-package plugin discovery with no code change,
  `importlib.metadata.entry_points()` is the standard mechanism for that
  (it's how pytest and Click discover plugins) - worth adding once there's
  more than a couple of provider packages to manage, not before.

`get_step_provider("some.unregistered.type")` raises `UnknownTaskTypeError`
naming what *is* registered, which is almost always enough to tell you
"you forgot to import that module."

## Cascading failure

If task `A` fails, everything that (transitively) `depends_on` it fails
too, without ever running - `Scheduler` propagates failure wave by wave
rather than trying to run every unaffected branch and only bailing at the
end. See `examples/run_demo.py` for a runnable example: `F depends_on [E]`
and `E` always raises, so `F` ends up `failed` with
`error="an upstream dependency failed"` despite `EchoProvider.execute`
never being called for it.

A dependency cycle (or a `depends_on` id that will never resolve for some
other reason) is detected the same way: if a wave produces no newly-ready
tasks *and* no newly-failed ones, nothing still-pending can ever change
state, so the scheduler force-fails what's left with an explanatory
message instead of looping forever. Malformed graphs - a duplicate task
id, or a `depends_on` pointing at an id that doesn't exist in the
experiment at all - are caught up front, before anything runs.

## Installing

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows cmd.exe; use Activate.ps1 for PowerShell
pip install -e .
```

## Running the demo (no Cosmos needed)

```bash
python examples/run_demo.py
```

This uses `InMemoryExperimentRepository` - nothing to configure, nothing
persisted anywhere. Good for local iteration and for writing tests.

## Switching to CosmosDB

`config.build_repository_from_env()` picks the backend from environment
variables so nothing in the scheduler or your provider code needs to know
which one is active:

| Variable | Required when | Default |
|---|---|---|
| `ORCHESTRATOR_REPOSITORY_BACKEND` | always | `memory` |
| `ORCHESTRATOR_COSMOS_ENDPOINT` | backend=cosmos | — |
| `ORCHESTRATOR_COSMOS_KEY` | backend=cosmos | — |
| `ORCHESTRATOR_COSMOS_DATABASE` | backend=cosmos | `orchestrator` |
| `ORCHESTRATOR_COSMOS_CONTAINER` | backend=cosmos | `experiments` |
| `ORCHESTRATOR_COSMOS_VERIFY_SSL` | backend=cosmos | `true` |

Call `await repository.ensure_container()` once at startup if the
database/container don't already exist - the constructor itself does no
I/O, so nothing gets created until you ask for it.

## Running a local CosmosDB emulator (Windows, via Docker)

Microsoft's current cross-platform emulator ("vNext") runs as a Linux
container under Docker Desktop on Windows (WSL2 backend) - this replaced
the older Windows-only emulator installer.

1. Install **Docker Desktop for Windows** if you don't have it, and make
   sure it's using the WSL2 backend (default on a recent install).

2. Pull and run the emulator:

   ```bash
   docker pull mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-latest
   docker run -p 8081:8081 -p 8080:8080 -p 1234:1234 ^
       mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-latest
   ```

   (`^` is cmd.exe's line-continuation character; use `` ` `` in
   PowerShell or put it all on one line.)

3. Set your environment for this project:

   ```bash
   set ORCHESTRATOR_REPOSITORY_BACKEND=cosmos
   set ORCHESTRATOR_COSMOS_ENDPOINT=https://localhost:8081
   set ORCHESTRATOR_COSMOS_KEY=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==
   set ORCHESTRATOR_COSMOS_VERIFY_SSL=false
   ```

   That key is the well-known, publicly-documented static key every
   Cosmos emulator instance uses - it's not a secret, don't reuse this
   pattern against a real account. `VERIFY_SSL=false` is what lets the
   Python client talk to the emulator's self-signed certificate without
   you separately importing it into Windows's trusted-certificate store;
   leave it `true` (the default) for a real Azure Cosmos account.

4. Create the database/container once (a short Python one-liner, or add
   a call to `ensure_container()` at the top of your own worker script):

   ```python
   import asyncio
   from orchestrator_core.persistence import CosmosExperimentRepository

   async def main():
       repo = CosmosExperimentRepository(
           "https://localhost:8081",
           "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==",
           "orchestrator", "experiments",
           connection_verify=False,
       )
       await repo.ensure_container()
       await repo.close()

   asyncio.run(main())
   ```

5. From here, swap `InMemoryExperimentRepository()` in the demo script for
   `config.build_repository_from_env()` and it'll persist to the emulator
   instead.

**Known limitation:** `update_task` is a read-modify-write against Cosmos
(load the whole experiment document, change one task, write it all back).
That's fine for one scheduler running one experiment at a time - the v1
assumption - but two writers racing on the same experiment document can
clobber each other. Cosmos's ETag-based optimistic concurrency is the
standard fix if that ever becomes real; not built now because there's
nothing yet that needs it.

## What's next

- Implement real providers under `ClassicalStepProvider` /
  `QuantumStepProvider` (the IonQ Qiskit-backed ones, presumably).
- A worker entry point that imports configured provider packages and
  calls `Scheduler.run_experiment` - right now `examples/run_demo.py` is
  standing in for that.
- ETag-based optimistic concurrency on `CosmosExperimentRepository`, if
  and when more than one writer touches an experiment concurrently.
- `importlib.metadata.entry_points()`-based provider discovery, once
  there's more than one provider package to wire up by hand.
