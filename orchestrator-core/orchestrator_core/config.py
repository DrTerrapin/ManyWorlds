"""Builds an ExperimentRepository from environment variables, so the
scheduler's caller doesn't have to hardcode (or even know) which backend
is in play. See README.md for the full variable list and local-emulator
setup.
"""

from __future__ import annotations

import os

from .persistence.base import ExperimentRepository
from .persistence.cosmos_repository import CosmosExperimentRepository
from .persistence.in_memory_repository import InMemoryExperimentRepository


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def build_repository_from_env() -> ExperimentRepository:
    """
    Reads:
        ORCHESTRATOR_REPOSITORY_BACKEND   "memory" (default) or "cosmos"

    When backend == "cosmos", additionally reads:
        ORCHESTRATOR_COSMOS_ENDPOINT      required
        ORCHESTRATOR_COSMOS_KEY           required
        ORCHESTRATOR_COSMOS_DATABASE      default "orchestrator"
        ORCHESTRATOR_COSMOS_CONTAINER     default "experiments"
        ORCHESTRATOR_COSMOS_VERIFY_SSL    default "true" - set to "false"
                                           for the local emulator's
                                           self-signed certificate
    """
    backend = os.environ.get("ORCHESTRATOR_REPOSITORY_BACKEND", "memory").strip().lower()

    if backend == "memory":
        return InMemoryExperimentRepository()

    if backend == "cosmos":
        try:
            endpoint = os.environ["ORCHESTRATOR_COSMOS_ENDPOINT"]
            key = os.environ["ORCHESTRATOR_COSMOS_KEY"]
        except KeyError as missing:
            raise RuntimeError(
                f"ORCHESTRATOR_REPOSITORY_BACKEND=cosmos requires {missing.args[0]} "
                "to be set - see README.md."
            ) from None

        database = os.environ.get("ORCHESTRATOR_COSMOS_DATABASE", "orchestrator")
        container = os.environ.get("ORCHESTRATOR_COSMOS_CONTAINER", "experiments")
        verify_ssl = _env_bool("ORCHESTRATOR_COSMOS_VERIFY_SSL", default=True)

        return CosmosExperimentRepository(
            endpoint,
            key,
            database,
            container,
            connection_verify=verify_ssl,
        )

    raise ValueError(
        f"Unknown ORCHESTRATOR_REPOSITORY_BACKEND '{backend}'. Expected 'memory' or 'cosmos'."
    )
